from __future__ import annotations

import json
import logging

from app.agents.base import BaseAgent
from app.schemas.state import RepoFinding, RunContext, WorkItem
from app.tools.openai_responses import OpenAIResponsesClient, OpenAIResponsesError
from app.tools.thread_memory import format_run_context

logger = logging.getLogger(__name__)


class ArchitectAgent(BaseAgent):
    def __init__(self, llm_client: OpenAIResponsesClient | None = None):
        super().__init__(name="architect", role="implementation_planning")
        self.llm_client = llm_client

    def plan_work(
        self,
        goal: str,
        findings: list[RepoFinding],
        fallback_items: list[WorkItem],
        run_context: RunContext | None = None,
    ) -> list[WorkItem]:
        if self.llm_client and self.llm_client.enabled and findings:
            llm_items = self._plan_with_llm(goal=goal, findings=findings, run_context=run_context)
            if llm_items is not None:
                return llm_items
        return fallback_items

    def _plan_with_llm(
        self,
        *,
        goal: str,
        findings: list[RepoFinding],
        run_context: RunContext | None,
    ) -> list[WorkItem] | None:
        try:
            payload = self.llm_client.generate_json(
                system_prompt=(
                    "You are an architect agent for a coding copilot. Break the request into 1-3 disjoint work items "
                    "with clear ownership, write scope, rationale, and acceptance criteria. Return JSON only."
                ),
                user_prompt=(
                    "Return JSON with the structure {\"work_items\": [{\"work_item_id\": \"...\", "
                    "\"title\": \"...\", \"owner\": \"...\", \"write_scope\": [\"...\"], "
                    "\"rationale\": \"...\", \"acceptance_criteria\": [\"...\"]}]}\n\n"
                    f"Goal: {goal}\n"
                    f"Conversation context:\n{format_run_context(run_context)}\n\n"
                    f"Repository findings: {json.dumps([finding.model_dump(mode='json') for finding in findings])}"
                ),
                max_output_tokens=1200,
            )
            work_items = [WorkItem.model_validate(item) for item in payload.get("work_items", [])]
            if not work_items:
                return None
            return work_items[:3]
        except (OpenAIResponsesError, ValueError, TypeError):
            logger.exception("LLM architecture planning failed; using deterministic work items.")
            return None
