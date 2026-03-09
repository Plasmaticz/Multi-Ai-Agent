from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.state import AnalysisResult, ResearchNote, ReviewNote


class ResearchOutput(BaseModel):
    notes: list[ResearchNote] = Field(default_factory=list)


class AnalysisOutput(BaseModel):
    result: AnalysisResult


class WriterOutput(BaseModel):
    draft: str


class ReviewerOutput(BaseModel):
    result: ReviewNote


class OrchestratorOutput(BaseModel):
    final_response: str
    status: str
