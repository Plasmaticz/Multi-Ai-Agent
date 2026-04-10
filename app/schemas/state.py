from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Source(BaseModel):
    title: str
    url: str
    snippet: str = ""
    retrieved_at: datetime = Field(default_factory=utcnow)


class ResearchNote(BaseModel):
    company: str
    question: str
    facts: list[str] = Field(default_factory=list)
    sources: list[Source] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class CompanyComparison(BaseModel):
    company: str
    cost_score: int = Field(ge=1, le=5)
    scalability_score: int = Field(ge=1, le=5)
    technology_score: int = Field(ge=1, le=5)
    rationale: str


class AnalysisResult(BaseModel):
    criteria: list[str] = Field(default_factory=lambda: ["cost", "scalability", "technology"])
    comparisons: list[CompanyComparison] = Field(default_factory=list)
    key_takeaways: list[str] = Field(default_factory=list)


class ReviewNote(BaseModel):
    passed: bool
    issues: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class RepoFinding(BaseModel):
    file_path: str
    line_number: int | None = None
    summary: str
    excerpt: str = ""
    score: float = Field(default=0.0, ge=0.0)


class WorkItem(BaseModel):
    work_item_id: str
    title: str
    owner: str
    write_scope: list[str] = Field(default_factory=list)
    rationale: str
    acceptance_criteria: list[str] = Field(default_factory=list)


class CodeChange(BaseModel):
    file_path: str
    change_type: str = "modify"
    summary: str
    proposal: str


class WorkerArtifact(BaseModel):
    work_item_id: str
    owner: str
    summary: str
    runtime_ms: float = Field(default=0.0, ge=0.0)
    files_touched: list[str] = Field(default_factory=list)
    code_changes: list[CodeChange] = Field(default_factory=list)
    tests_to_run: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ConversationTurn(BaseModel):
    role: str
    content: str


class RunContext(BaseModel):
    current_message: str
    thread_summary: str = ""
    recent_messages: list[ConversationTurn] = Field(default_factory=list)


class ProjectState(BaseModel):
    request_id: str
    user_goal: str
    deliverable_type: str = "report"
    requirements: list[str] = Field(default_factory=list)
    status: str = "planning"
    run_context: RunContext | None = None
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    research_notes: list[ResearchNote] = Field(default_factory=list)
    analysis: AnalysisResult | None = None
    repo_findings: list[RepoFinding] = Field(default_factory=list)
    implementation_plan: list[WorkItem] = Field(default_factory=list)
    worker_outputs: list[WorkerArtifact] = Field(default_factory=list)
    validation_commands: list[str] = Field(default_factory=list)
    drafts: list[str] = Field(default_factory=list)
    review_notes: list[ReviewNote] = Field(default_factory=list)
    final_output: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def touch(self) -> None:
        self.updated_at = utcnow()
