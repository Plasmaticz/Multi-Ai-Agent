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


class ProjectState(BaseModel):
    request_id: str
    user_goal: str
    deliverable_type: str = "report"
    requirements: list[str] = Field(default_factory=list)
    status: str = "planning"
    tasks: list[dict[str, Any]] = Field(default_factory=list)
    research_notes: list[ResearchNote] = Field(default_factory=list)
    analysis: AnalysisResult | None = None
    drafts: list[str] = Field(default_factory=list)
    review_notes: list[ReviewNote] = Field(default_factory=list)
    final_output: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    def touch(self) -> None:
        self.updated_at = utcnow()
