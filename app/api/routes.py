from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.config import Settings
from app.schemas.state import RunContext
from app.tools.thread_memory import build_run_context, refresh_thread_summary
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
    return _thread_payload(request.app.state.local_store, thread_id)


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

    response_payload = _thread_payload(store, thread_id)
    response_payload["run"] = store.get_run(run["id"])
    return JSONResponse(status_code=202, content=response_payload)


@router.get("/api/threads/{thread_id}/runs/{run_id}")
def get_thread_run(thread_id: str, run_id: str, request: Request) -> dict[str, Any]:
    store = request.app.state.local_store
    _get_thread_or_404(store, thread_id)
    run = store.get_run(run_id)
    if run is None or run["thread_id"] != thread_id:
        raise HTTPException(status_code=404, detail="Run not found.")

    payload = _thread_payload(store, thread_id)
    payload["run"] = run
    payload["events"] = store.list_logs(thread_id=thread_id, limit=500, ascending=True)
    return payload


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


def _thread_payload(store, thread_id: str) -> dict[str, Any]:
    thread = _get_thread_or_404(store, thread_id)
    return {
        "thread": thread,
        "messages": store.list_messages(thread_id),
        "events": store.list_logs(thread_id=thread_id, limit=500, ascending=True),
        "active_run": store.get_active_run(thread_id),
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


def _mask_api_key(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return value
    return f"{value[:6]}...{value[-4:]}"
