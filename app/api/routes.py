from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from app.agents.finalizer import FinalizerAgent
from app.config import Settings
from app.schemas.state import CodeChange, ProjectState, RunContext
from app.tools.thread_memory import build_run_context, refresh_thread_summary
from app.tools.workspace_tools import WorkspaceTool
from app.workflows.run_crew import CrewRunner

logger = logging.getLogger(__name__)

router = APIRouter()


class CreateThreadRequest(BaseModel):
    title: str | None = None


class MessageRequest(BaseModel):
    content: str = Field(min_length=1)


class SettingsRequest(BaseModel):
    openai_api_key: str | None = None
    clear_api_key: bool = False
    openai_model: str | None = None
    workspace_dir: str | None = None
    max_concurrent_research: int | None = Field(default=None, ge=1, le=12)


@router.get("/")
def index(request: Request):
    return request.app.state.templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "app_name": request.app.state.settings.app_name,
        },
    )


@router.get("/api/meta")
def metadata(request: Request) -> dict[str, Any]:
    settings = request.app.state.settings
    return {
        "app_name": settings.app_name,
        "environment": settings.environment,
        "desktop_mode": settings.environment == "desktop",
        "app_data_dir": str(settings.app_data_path),
    }


@router.get("/api/threads")
def list_threads(request: Request) -> dict[str, Any]:
    return {"threads": request.app.state.local_store.list_threads()}


@router.post("/api/threads")
def create_thread(request: Request, payload: CreateThreadRequest) -> dict[str, Any]:
    thread = request.app.state.local_store.create_thread(title=payload.title or "New Thread")
    return {"thread": thread}


@router.delete("/api/threads/{thread_id}")
def delete_thread(thread_id: str, request: Request) -> dict[str, Any]:
    store = request.app.state.local_store
    _get_thread_or_404(store, thread_id)
    active_run = store.get_active_run(thread_id)
    if active_run is not None:
        raise HTTPException(status_code=409, detail="Cannot delete a thread while a run is still in progress.")

    store.delete_thread(thread_id)
    return {"deleted": True, "thread_id": thread_id}


@router.get("/api/threads/{thread_id}")
def get_thread_detail(thread_id: str, request: Request) -> dict[str, Any]:
    return _thread_payload(request.app.state.local_store, thread_id, request)


@router.post("/api/threads/{thread_id}/messages")
def send_message(thread_id: str, payload: MessageRequest, request: Request) -> JSONResponse:
    store = request.app.state.local_store
    thread = _get_thread_or_404(store, thread_id)
    content = payload.content.strip()
    if not content:
        raise HTTPException(status_code=400, detail="Message content is required.")

    previous_messages = store.list_messages(thread_id)
    run_context = build_run_context(
        thread_summary=thread.get("thread_summary", ""),
        previous_messages=previous_messages,
        current_message=content,
        recent_limit=request.app.state.settings.thread_recent_message_limit,
    )

    user_message = store.add_message(thread_id=thread_id, role="user", content=content)
    store.rename_thread_if_placeholder(thread_id, content)
    run = store.create_run(thread_id=thread_id, goal=content, user_message_id=user_message["id"])
    store.add_log(
        thread_id=thread_id,
        run_id=run["id"],
        agent_name="system",
        event_type="run",
        status="queued",
        message="Run queued.",
    )

    runtime_settings = _resolve_runtime_settings(request)
    request.app.state.run_executor.submit(
        _execute_thread_run,
        request.app,
        thread_id,
        run["id"],
        content,
        thread.get("thread_summary", ""),
        run_context,
        runtime_settings,
    )

    response_payload = _thread_payload(store, thread_id, request)
    response_payload["run"] = _serialize_run(store.get_run(run["id"]), request)
    return JSONResponse(status_code=202, content=response_payload)


@router.get("/api/threads/{thread_id}/runs/{run_id}")
def get_thread_run(thread_id: str, run_id: str, request: Request) -> dict[str, Any]:
    store = request.app.state.local_store
    _get_thread_or_404(store, thread_id)
    run = store.get_run(run_id)
    if run is None or run["thread_id"] != thread_id:
        raise HTTPException(status_code=404, detail="Run not found.")

    payload = _thread_payload(store, thread_id, request)
    payload["run"] = _serialize_run(run, request)
    payload["events"] = store.list_logs(thread_id=thread_id, limit=500, ascending=True)
    return payload


@router.post("/api/threads/{thread_id}/runs/{run_id}/apply")
def apply_thread_run(thread_id: str, run_id: str, request: Request) -> dict[str, Any]:
    store = request.app.state.local_store
    _get_thread_or_404(store, thread_id)
    run = store.get_run(run_id)
    if run is None or run["thread_id"] != thread_id:
        raise HTTPException(status_code=404, detail="Run not found.")

    if run["status"] != "complete":
        raise HTTPException(status_code=409, detail="Only completed runs can be applied.")

    state = _load_project_state_from_run(run)
    if state is None:
        raise HTTPException(status_code=409, detail="Run does not contain an applyable result.")

    can_apply, reason = _can_apply_state(state)
    if not can_apply:
        raise HTTPException(status_code=409, detail=reason)
    if state.metadata.get("apply_status") == "applied":
        raise HTTPException(status_code=409, detail="Changes for this run were already applied.")

    runtime_settings = _resolve_runtime_settings(request)
    workspace_tool = WorkspaceTool(runtime_settings.workspace_path)
    work_items_by_id = {item.work_item_id: item for item in state.implementation_plan}
    applied_count = 0
    rejected_count = 0
    summaries: list[str] = []

    for artifact in state.worker_outputs:
        work_item = work_items_by_id.get(artifact.work_item_id)
        allowed_paths = set(work_item.write_scope) if work_item is not None else set()
        for change in artifact.code_changes:
            if change.apply_status == "applied":
                continue
            result_message = ""
            if change.change_type not in {"modify", "create"}:
                change.apply_status = "rejected"
                result_message = "Unsupported change type."
            elif change.file_path not in allowed_paths:
                change.apply_status = "rejected"
                result_message = "File is outside the assigned write scope."
            elif workspace_tool.resolve_path(change.file_path) is None:
                change.apply_status = "rejected"
                result_message = "File is outside the selected workspace."
            elif not change.proposed_content or not change.unified_diff:
                change.apply_status = "rejected"
                result_message = "Patch is missing proposed content or diff."
            else:
                write_result = workspace_tool.write_text(change.file_path, change.proposed_content)
                change.apply_status = write_result.status
                result_message = write_result.message

            if change.apply_status == "applied":
                applied_count += 1
            else:
                rejected_count += 1
            summaries.append(f"{change.file_path}: {change.apply_status} ({result_message})")

    state.metadata["apply_status"] = "applied" if rejected_count == 0 else "partial"
    state.metadata["apply_summary"] = {
        "applied_count": applied_count,
        "rejected_count": rejected_count,
        "details": summaries,
    }

    store.update_run_result(run_id, state.model_dump(mode="json"))
    store.add_log(
        thread_id=thread_id,
        run_id=run_id,
        agent_name="system",
        event_type="apply",
        status="completed" if rejected_count == 0 else "needs_human_review",
        message=f"Applied {applied_count} file change(s); rejected {rejected_count}.",
    )
    store.add_message(
        thread_id=thread_id,
        role="assistant",
        run_id=run_id,
        message_type="text",
        content=_build_apply_message(state),
    )

    payload = _thread_payload(store, thread_id, request)
    payload["run"] = _serialize_run(store.get_run(run_id), request)
    return payload


@router.get("/api/threads/{thread_id}/runs/{run_id}/export/{kind}")
def export_thread_run(thread_id: str, run_id: str, kind: str, request: Request) -> Response:
    store = request.app.state.local_store
    _get_thread_or_404(store, thread_id)
    run = store.get_run(run_id)
    if run is None or run["thread_id"] != thread_id:
        raise HTTPException(status_code=404, detail="Run not found.")

    state = _load_project_state_from_run(run)
    if state is None:
        raise HTTPException(status_code=409, detail="Run does not contain exportable content.")

    runtime_settings = _resolve_runtime_settings(request)
    finalizer = FinalizerAgent()

    if kind == "report":
        content = state.final_output or finalizer.finalize(state, state.worker_outputs)
        media_type = "text/markdown; charset=utf-8"
        filename = "coding-run-report.md"
    elif kind == "summary":
        content = finalizer.summarize_run(
            state,
            workspace_dir=str(runtime_settings.workspace_path),
            run_status=run["status"],
        )
        media_type = "text/markdown; charset=utf-8"
        filename = "coding-run-summary.md"
    elif kind == "logs":
        content = json.dumps(store.list_logs(thread_id=thread_id, run_id=run_id, limit=500, ascending=True), indent=2)
        media_type = "application/json; charset=utf-8"
        filename = "coding-run-logs.json"
    else:
        raise HTTPException(status_code=404, detail="Unknown export kind.")

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/api/settings")
def get_settings(request: Request) -> dict[str, Any]:
    return _settings_response(request)


@router.post("/api/settings")
def save_settings(payload: SettingsRequest, request: Request) -> dict[str, Any]:
    store = request.app.state.local_store
    existing = store.get_settings()

    if payload.clear_api_key:
        openai_api_key = None
    elif payload.openai_api_key:
        openai_api_key = payload.openai_api_key.strip()
    elif "openai_api_key" in existing:
        openai_api_key = existing.get("openai_api_key")
    else:
        openai_api_key = request.app.state.settings.openai_api_key

    openai_model = payload.openai_model or existing.get("openai_model") or request.app.state.settings.openai_model
    workspace_dir = payload.workspace_dir or existing.get("workspace_dir") or request.app.state.settings.workspace_dir
    normalized_workspace_dir = _normalize_workspace_dir(workspace_dir)
    max_concurrent_research = (
        payload.max_concurrent_research
        or existing.get("max_concurrent_research")
        or request.app.state.settings.max_concurrent_research
    )

    store.save_settings(
        openai_api_key=openai_api_key,
        openai_model=openai_model,
        workspace_dir=normalized_workspace_dir,
        max_concurrent_research=int(max_concurrent_research),
    )
    return _settings_response(request)


@router.get("/api/logs")
def get_logs(
    request: Request,
    thread_id: str | None = None,
    run_id: str | None = None,
    limit: int = 200,
    ascending: bool = False,
) -> dict[str, Any]:
    logs = request.app.state.local_store.list_logs(
        thread_id=thread_id,
        run_id=run_id,
        limit=limit,
        ascending=ascending,
    )
    return {"logs": logs}


def _execute_thread_run(
    app,
    thread_id: str,
    run_id: str,
    goal: str,
    previous_summary: str,
    run_context: RunContext,
    runtime_settings: Settings,
) -> None:
    store = app.state.local_store

    def emit(event: dict[str, Any]) -> None:
        store.add_log(
            thread_id=thread_id,
            run_id=run_id,
            agent_name=event["agent_name"],
            event_type=event["event_type"],
            status=event["status"],
            message=event["message"],
        )

    store.add_log(
        thread_id=thread_id,
        run_id=run_id,
        agent_name="system",
        event_type="run",
        status="started",
        message="Run started.",
    )

    runner = CrewRunner(
        settings=runtime_settings,
        store=app.state.project_store,
        event_callback=emit,
    )

    try:
        state = runner.run(goal=goal, request_id=run_id, run_context=run_context)
        final_output = state.final_output or "Run completed without a final answer."
        message_type = "text" if state.status == "complete" else "error"
        store.add_message(
            thread_id=thread_id,
            role="assistant",
            content=final_output,
            run_id=run_id,
            message_type=message_type,
        )
        if state.status == "complete":
            store.update_thread_summary(
                thread_id,
                refresh_thread_summary(
                    llm_client=runner.llm_client,
                    previous_summary=previous_summary,
                    run_context=run_context,
                    assistant_output=final_output,
                ),
            )
        store.complete_run(run_id, state.status, state.model_dump(mode="json"))
        store.add_log(
            thread_id=thread_id,
            run_id=run_id,
            agent_name="system",
            event_type="run",
            status="completed" if state.status == "complete" else state.status,
            message="Run finished." if state.status == "complete" else f"Run finished with status {state.status}.",
        )
    except Exception as exc:
        logger.exception("Thread run failed.")
        error_message = f"Run failed: {exc}"
        store.add_log(
            thread_id=thread_id,
            run_id=run_id,
            agent_name="system",
            event_type="run",
            status="failed",
            message=error_message,
        )
        store.add_message(
            thread_id=thread_id,
            role="assistant",
            content=error_message,
            run_id=run_id,
            message_type="error",
        )
        store.complete_run(run_id, "failed", {"error": str(exc)})


def _thread_payload(store, thread_id: str, request: Request) -> dict[str, Any]:
    thread = _get_thread_or_404(store, thread_id)
    latest_run = store.get_latest_run(thread_id)
    active_run = store.get_active_run(thread_id)
    return {
        "thread": thread,
        "messages": store.list_messages(thread_id),
        "events": store.list_logs(thread_id=thread_id, limit=500, ascending=True),
        "active_run": _serialize_run(active_run, request) if active_run else None,
        "latest_run": _serialize_run(latest_run, request) if latest_run else None,
    }


def _get_thread_or_404(store, thread_id: str) -> dict[str, Any]:
    thread = store.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found.")
    return thread


def _resolve_runtime_settings(request: Request) -> Settings:
    base_settings = request.app.state.settings
    stored = request.app.state.local_store.get_settings()
    updates: dict[str, Any] = {}
    for key in ("openai_api_key", "openai_model", "workspace_dir", "max_concurrent_research"):
        if stored.get(key) not in (None, ""):
            updates[key] = stored.get(key)
    return base_settings.model_copy(update=updates)


def _settings_response(request: Request) -> dict[str, Any]:
    base_settings = request.app.state.settings
    stored = request.app.state.local_store.get_settings()

    has_api_key = False
    api_key_preview = ""
    if "openai_api_key" in stored:
        has_api_key = bool(stored.get("openai_api_key"))
        api_key_preview = _mask_api_key(stored.get("openai_api_key"))
    elif base_settings.openai_api_key:
        has_api_key = True
        api_key_preview = _mask_api_key(base_settings.openai_api_key)

    return {
        "has_api_key": has_api_key,
        "api_key_preview": api_key_preview,
        "openai_model": stored.get("openai_model") or base_settings.openai_model,
        "workspace_dir": stored.get("workspace_dir") or str(base_settings.workspace_path),
        "max_concurrent_research": stored.get("max_concurrent_research") or base_settings.max_concurrent_research,
    }


def _normalize_workspace_dir(value: str) -> str:
    candidate = Path(value).expanduser().resolve()
    if not candidate.exists():
        raise HTTPException(status_code=400, detail="Workspace folder does not exist.")
    if not candidate.is_dir():
        raise HTTPException(status_code=400, detail="Workspace folder must be a directory.")
    return str(candidate)


def _serialize_run(run: dict[str, Any] | None, request: Request) -> dict[str, Any] | None:
    if run is None:
        return None

    payload = dict(run)
    state = _load_project_state_from_run(run)
    payload["result"] = state.model_dump(mode="json") if state is not None else None
    if state is None:
        payload["can_apply"] = False
        payload["apply_status"] = None
        return payload

    can_apply, reason = _can_apply_state(state, run_status=run["status"])
    payload["can_apply"] = can_apply
    payload["apply_block_reason"] = reason if not can_apply else ""
    payload["apply_status"] = state.metadata.get("apply_status", "pending")
    payload["workspace_dir"] = str(_resolve_runtime_settings(request).workspace_path)
    return payload


def _load_project_state_from_run(run: dict[str, Any]) -> ProjectState | None:
    raw = run.get("result_json")
    if not raw:
        return None
    try:
        return ProjectState.model_validate(json.loads(raw))
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def _can_apply_state(state: ProjectState, *, run_status: str | None = None) -> tuple[bool, str]:
    if run_status is not None and run_status != "complete":
        return False, "Only completed runs can be applied."
    if state.metadata.get("apply_status") == "applied":
        return False, "Changes for this run were already applied."
    if not state.worker_outputs:
        return False, "No worker artifacts are available to apply."
    if state.review_notes and not state.review_notes[-1].passed:
        return False, "The latest review did not pass."
    if any(result.status == "failed" for result in state.validation_results):
        return False, "Validation failed for this run."
    for artifact in state.worker_outputs:
        if not artifact.code_changes:
            return False, f"{artifact.owner} does not have applyable code changes."
        for change in artifact.code_changes:
            if not change.proposed_content or not change.unified_diff:
                return False, f"{change.file_path} is missing content or diff data."
    return True, ""


def _build_apply_message(state: ProjectState) -> str:
    summary = state.metadata.get("apply_summary", {})
    details = summary.get("details", [])
    lines = [
        "## Apply Results",
        f"- Apply status: `{state.metadata.get('apply_status', 'pending')}`",
        f"- Applied files: `{summary.get('applied_count', 0)}`",
        f"- Rejected files: `{summary.get('rejected_count', 0)}`",
    ]
    if details:
        lines.append("Details:")
        lines.extend(f"- {detail}" for detail in details)
    return "\n".join(lines)


def _mask_api_key(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return value
    return f"{value[:6]}...{value[-4:]}"
