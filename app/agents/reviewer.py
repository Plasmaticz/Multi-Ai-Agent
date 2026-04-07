from __future__ import annotations

import json
import logging

from app.agents.base import BaseAgent
from app.schemas.state import ProjectState, ReviewNote
from app.tools.openai_responses import OpenAIResponsesClient, OpenAIResponsesError
from app.tools.thread_memory import format_run_context

logger = logging.getLogger(__name__)


class ReviewerAgent(BaseAgent):
    def __init__(self, llm_client: OpenAIResponsesClient | None = None):
        super().__init__(name="reviewer", role="critic")
        self.llm_client = llm_client

    def review(
        self,
        state: ProjectState,
        draft: str,
        min_sources_per_company: int,
    ) -> ReviewNote:
        issues = self._deterministic_issues(
            state=state,
            draft=draft,
            min_sources_per_company=min_sources_per_company,
        )

        llm_review = None
        if self.llm_client and self.llm_client.enabled:
            llm_review = self._review_with_llm(state=state, draft=draft)
            if llm_review is not None:
                issues.extend(llm_review.issues)

        deduped_issues = list(dict.fromkeys(issues))
        passed = len(deduped_issues) == 0
        confidence = 0.9 if passed else 0.55
        if llm_review is not None:
            confidence = round((confidence + llm_review.confidence) / 2, 2)
        return ReviewNote(passed=passed, issues=deduped_issues, confidence=confidence)

    def _deterministic_issues(
        self,
        state: ProjectState,
        draft: str,
        min_sources_per_company: int,
    ) -> list[str]:
        issues: list[str] = []

        for required_section in [
            "Executive Summary",
            "Method",
            "Company Snapshots",
            "Comparison Table",
            "Sources",
        ]:
            if required_section.lower() not in draft.lower():
                issues.append(f"Missing required section: {required_section}.")

        for note in state.research_notes:
            if len(note.sources) < min_sources_per_company:
                issues.append(
                    f"{note.company} has fewer than {min_sources_per_company} sources."
                )

        if "http" not in draft:
            issues.append("Draft does not include source links.")

        return issues

    def _review_with_llm(self, state: ProjectState, draft: str) -> ReviewNote | None:
        system_prompt = (
            "You are a strict reviewer agent. Validate report completeness, supportability, and "
            "internal consistency. Return only JSON."
        )
        user_prompt = (
            "Review this report and return JSON: "
            "{\"passed\": true/false, \"issues\": [\"...\"], \"confidence\": 0.0-1.0}.\n"
            f"Goal: {state.user_goal}\n"
            f"Requirements: {json.dumps(state.requirements)}\n"
            f"Conversation context: {format_run_context(state.run_context)}\n"
            f"Report draft: {draft}\n"
        )
        try:
            payload = self.llm_client.generate_json(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                max_output_tokens=900,
            )
            return ReviewNote.model_validate(payload)
        except (OpenAIResponsesError, ValueError):
            logger.exception("LLM review failed; continuing with deterministic checks only.")
            return None
