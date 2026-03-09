from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.tools.storage import ProjectStore
from app.workflows.run_crew import CrewRunner

router = APIRouter(prefix="/v1/projects", tags=["projects"])
_store = ProjectStore()
_runner = CrewRunner(store=_store)


class RunProjectRequest(BaseModel):
    goal: str = Field(min_length=5)
    companies: list[str] | None = None
    request_id: str | None = None


@router.post("/run")
def run_project(payload: RunProjectRequest):
    state = _runner.run(
        goal=payload.goal,
        companies=payload.companies,
        request_id=payload.request_id,
    )
    return state.model_dump(mode="json")


@router.get("/{request_id}")
def get_project(request_id: str):
    state = _store.get(request_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Project not found")
    return state.model_dump(mode="json")
