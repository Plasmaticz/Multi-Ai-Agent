from __future__ import annotations

import json
import logging

from app.agents.base import BaseAgent
from app.schemas.state import RepoFinding, RunContext
from app.tools.openai_responses import OpenAIResponsesClient, OpenAIResponsesError
from app.tools.repo_tools import RepoSearchTool
from app.tools.thread_memory import format_run_context

logger = logging.getLogger(__name__)


class RepoExplorerAgent(BaseAgent):
    def __init__(self, repo_search: RepoSearchTool, llm_client: OpenAIResponsesClient | None = None):
        super().__init__(name="repo_explorer", role="repository_context_gathering")
        self.repo_search = repo_search
        self.llm_client = llm_client

    def explore(self, goal: str, run_context: RunContext | None = None, limit: int = 10) -> list[RepoFinding]:
        query = goal
        if run_context is not None:
            query = " ".join(
                [
                    goal,
                    run_context.thread_summary,
                    " ".join(turn.content for turn in run_context.recent_messages),
                ]
            )

        raw_matches = self.repo_search.search(query=query, limit=max(limit * 2, 12))
        if self.llm_client and self.llm_client.enabled and raw_matches:
            llm_findings = self._rank_with_llm(goal=goal, run_context=run_context, raw_matches=raw_matches, limit=limit)
            if llm_findings is not None:
                return llm_findings

        findings: list[RepoFinding] = []
        for match in raw_matches[:limit]:
            findings.append(
                RepoFinding(
                    file_path=match.file_path,
                    line_number=match.line_number,
                    summary=f"Relevant match in {match.file_path} for the requested coding task.",
                    excerpt=match.excerpt,
                    score=float(match.score),
                )
            )
        return findings

    def _rank_with_llm(
        self,
        *,
        goal: str,
        run_context: RunContext | None,
        raw_matches,
        limit: int,
    ) -> list[RepoFinding] | None:
        try:
            payload = self.llm_client.generate_json(
                system_prompt=(
                    "You are a repository explorer for a multi-agent coding assistant. "
                    "Choose the most relevant files for implementing the user's request. Return structured JSON only."
                ),
                user_prompt=(
                    "Return JSON with the structure {\"findings\": [{\"file_path\": \"...\", \"line_number\": 1 or null, "
                    "\"summary\": \"...\", \"excerpt\": \"...\", \"score\": 0.0}]}\n\n"
                    f"Goal: {goal}\n"
                    f"Conversation context:\n{format_run_context(run_context)}\n\n"
                    f"Raw repository matches: {json.dumps([match.__dict__ for match in raw_matches])}\n"
                    f"Return at most {limit} findings."
                ),
                max_output_tokens=1200,
            )
            findings = [RepoFinding.model_validate(item) for item in payload.get("findings", [])]
            return findings[:limit] if findings else None
        except (OpenAIResponsesError, ValueError, TypeError):
            logger.exception("LLM repo exploration failed; falling back to deterministic ranking.")
            return None
