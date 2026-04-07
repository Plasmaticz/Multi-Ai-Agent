from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from app.config import Settings
from app.workflows.run_crew import CrewRunner

router = APIRouter()


class RunProjectRequest(BaseModel):
    goal: str = Field(min_length=5)
    companies: list[str] | None = None
    request_id: str | None = None


class CreateThreadRequest(BaseModel):
    title: str | None = None


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1)


class SaveSettingsRequest(BaseModel):
    openai_api_key: str | None = None
    clear_api_key: bool = False
    openai_model: str = Field(default="gpt-4.1-mini", min_length=3)
    max_concurrent_research: int = Field(default=4, ge=1, le=12)


@router.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return request.app.state.templates.TemplateResponse(
        request,
        "index.html",
        {"app_name": request.app.state.settings.app_name},
    )


@router.get("/api/threads")
def list_threads(request: Request) -> dict[str, Any]:
    local_store = request.app.state.local_store
    return {"threads": local_store.list_threads()}


@router.post("/api/threads")
def create_thread(request: Request, payload: CreateThreadRequest) -> dict[str, Any]:
    local_store = request.app.state.local_store
    thread = local_store.create_thread(title=payload.title or "New Thread")
    return {"thread": thread, "messages": []}


@router.get("/api/threads/{thread_id}")
def get_thread_detail(request: Request, thread_id: str) -> dict[str, Any]:
    local_store = request.app.state.local_store
    thread = local_store.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    return {
        "thread": thread,
        "messages": local_store.list_messages(thread_id),
    }


@router.post("/api/threads/{thread_id}/messages")
def send_message(request: Request, thread_id: str, payload: SendMessageRequest) -> dict[str, Any]:
    local_store = request.app.state.local_store
    thread = local_store.get_thread(thread_id)
    if thread is None:
        raise HTTPException(status_code=404, detail="Thread not found")

    content = payload.content.strip()
    user_message = local_store.add_message(thread_id, "user", content)
    local_store.rename_thread_if_placeholder(thread_id, content)
    run = local_store.create_run(thread_id, content, user_message["id"])
    local_store.add_log(
        thread_id=thread_id,
        run_id=run["id"],
        agent_name="system",
        event_type="run",
        status="started",
        message="Workflow run started.",
    )

    runner = CrewRunner(
        settings=_build_runtime_settings(request),
        store=request.app.state.project_store,
        event_callback=lambda event: local_store.add_log(
            thread_id=thread_id,
            run_id=run["id"],
            agent_name=event["agent_name"],
            event_type=event["event_type"],
            status=event["status"],
            message=event["message"],
        ),
    )

    try:
        state = runner.run(goal=content)
        local_store.complete_run(run["id"], state.status, state.model_dump(mode="json"))
        local_store.add_message(thread_id, "assistant", state.final_output, run_id=run["id"])
        local_store.add_log(
            thread_id=thread_id,
            run_id=run["id"],
            agent_name="system",
            event_type="run",
            status=state.status,
            message="Workflow run finished.",
        )
    except Exception as exc:
        error_message = f"Run failed: {exc}"
        local_store.complete_run(run["id"], "failed", {"error": error_message})
        local_store.add_message(thread_id, "assistant", error_message, run_id=run["id"])
        local_store.add_log(
            thread_id=thread_id,
            run_id=run["id"],
            agent_name="system",
            event_type="run",
            status="failed",
            message=error_message,
        )
        raise HTTPException(status_code=500, detail=error_message) from exc

    return {
        "thread": local_store.get_thread(thread_id),
        "messages": local_store.list_messages(thread_id),
        "logs": local_store.list_logs(thread_id=thread_id, limit=50),
    }


@router.get("/api/settings")
def get_settings(request: Request) -> dict[str, Any]:
    stored = request.app.state.local_store.get_settings()
    api_key = stored.get("openai_api_key")
    return {
        "has_api_key": bool(api_key),
        "api_key_preview": _mask_api_key(api_key),
        "openai_model": stored.get("openai_model") or request.app.state.settings.openai_model,
        "max_concurrent_research": stored.get("max_concurrent_research")
        or request.app.state.settings.max_concurrent_research,
    }


@router.post("/api/settings")
def save_settings(request: Request, payload: SaveSettingsRequest) -> dict[str, Any]:
    local_store = request.app.state.local_store
    existing = local_store.get_settings()
    api_key = existing.get("openai_api_key")
    if payload.clear_api_key:
        api_key = None
    elif payload.openai_api_key and payload.openai_api_key.strip():
        api_key = payload.openai_api_key.strip()

    stored = local_store.save_settings(
        openai_api_key=api_key,
        openai_model=payload.openai_model.strip(),
        max_concurrent_research=payload.max_concurrent_research,
    )
    return {
        "has_api_key": bool(stored.get("openai_api_key")),
        "api_key_preview": _mask_api_key(stored.get("openai_api_key")),
        "openai_model": stored.get("openai_model"),
        "max_concurrent_research": stored.get("max_concurrent_research"),
    }


@router.get("/api/logs")
def get_logs(request: Request, thread_id: str | None = None, limit: int = 200) -> dict[str, Any]:
    local_store = request.app.state.local_store
    return {"logs": local_store.list_logs(thread_id=thread_id, limit=limit)}


@router.post("/v1/projects/run")
def run_project(request: Request, payload: RunProjectRequest) -> dict[str, Any]:
    runner = CrewRunner(
        settings=_build_runtime_settings(request),
        store=request.app.state.project_store,
    )
    state = runner.run(
        goal=payload.goal,
        companies=payload.companies,
        request_id=payload.request_id,
    )
    return state.model_dump(mode="json")


@router.get("/v1/projects/{request_id}")
def get_project(request: Request, request_id: str) -> dict[str, Any]:
    state = request.app.state.project_store.get(request_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return state.model_dump(mode="json")


def _build_runtime_settings(request: Request) -> Settings:
    base_settings: Settings = request.app.state.settings
    stored = request.app.state.local_store.get_settings()
    updates: dict[str, Any] = {}
    if "openai_api_key" in stored:
        updates["openai_api_key"] = stored.get("openai_api_key")
    if stored.get("openai_model"):
        updates["openai_model"] = stored["openai_model"]
    if stored.get("max_concurrent_research"):
        updates["max_concurrent_research"] = stored["max_concurrent_research"]
    return base_settings.model_copy(update=updates)


def _mask_api_key(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) < 12:
        return "Configured"
    return f"{value[:7]}...{value[-4:]}"
