from __future__ import annotations

import json
import logging

from app.agents.base import BaseAgent
from app.schemas.state import ProjectState, ReviewNote, WorkerArtifact
from app.tools.openai_responses import OpenAIResponsesClient, OpenAIResponsesError
from app.tools.thread_memory import format_run_context

logger = logging.getLogger(__name__)


class ReviewerAgent(BaseAgent):
    def __init__(self, llm_client: OpenAIResponsesClient | None = None):
        super().__init__(name="reviewer", role="code_reviewer")
        self.llm_client = llm_client

    def review(
        self,
        state: ProjectState,
        worker_outputs: list[WorkerArtifact],
    ) -> ReviewNote:
        issues = self._deterministic_issues(state=state, worker_outputs=worker_outputs)

        llm_review = None
        if self.llm_client and self.llm_client.enabled:
            llm_review = self._review_with_llm(state=state, worker_outputs=worker_outputs)
            if llm_review is not None:
                issues.extend(llm_review.issues)

        deduped = list(dict.fromkeys(issues))
        passed = len(deduped) == 0
        confidence = 0.88 if passed else 0.58
        if llm_review is not None:
            confidence = round((confidence + llm_review.confidence) / 2, 2)
        return ReviewNote(passed=passed, issues=deduped, confidence=confidence)

    def _deterministic_issues(
        self,
        state: ProjectState,
        worker_outputs: list[WorkerArtifact],
    ) -> list[str]:
        issues: list[str] = []
        by_work_item = {artifact.work_item_id: artifact for artifact in worker_outputs}

        for work_item in state.implementation_plan:
            artifact = by_work_item.get(work_item.work_item_id)
            if artifact is None:
                issues.append(f"Missing worker output for {work_item.title}.")
                continue
            if not artifact.code_changes:
                issues.append(f"{work_item.title} does not include proposed code changes.")
            if not artifact.files_touched:
                issues.append(f"{work_item.title} does not identify files to update.")
            if any(path not in work_item.write_scope for path in artifact.files_touched):
                issues.append(f"{work_item.title} touches files outside its declared write scope.")

        touched_by_owner: dict[str, str] = {}
        for artifact in worker_outputs:
            for path in artifact.files_touched:
                if path in touched_by_owner and touched_by_owner[path] != artifact.owner:
                    issues.append(f"Multiple workers propose edits to {path}, which risks merge conflicts.")
                touched_by_owner[path] = artifact.owner

        if "tests" in state.requirements:
            if not any(artifact.tests_to_run for artifact in worker_outputs):
                issues.append("The requested change mentions tests, but no validation commands were proposed.")

        return issues

    def _review_with_llm(self, state: ProjectState, worker_outputs: list[WorkerArtifact]) -> ReviewNote | None:
        try:
            payload = self.llm_client.generate_json(
                system_prompt=(
                    "You are a strict code reviewer for a multi-agent coding assistant. "
                    "Look for regressions, missing coverage, unsafe assumptions, and file ownership conflicts. Return JSON only."
                ),
                user_prompt=(
                    "Return JSON with the structure {\"passed\": true/false, \"issues\": [\"...\"], \"confidence\": 0.0}.\n\n"
                    f"Goal: {state.user_goal}\n"
                    f"Requirements: {json.dumps(state.requirements)}\n"
                    f"Conversation context:\n{format_run_context(state.run_context)}\n\n"
                    f"Implementation plan: {json.dumps([item.model_dump(mode='json') for item in state.implementation_plan])}\n"
                    f"Worker outputs: {json.dumps([artifact.model_dump(mode='json') for artifact in worker_outputs])}"
                ),
                max_output_tokens=1200,
            )
            return ReviewNote.model_validate(payload)
        except (OpenAIResponsesError, ValueError, TypeError):
            logger.exception("LLM code review failed; continuing with deterministic checks.")
            return None
