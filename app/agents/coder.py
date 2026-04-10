from __future__ import annotations

import json
import logging

from app.agents.base import BaseAgent
from app.schemas.state import CodeChange, RepoFinding, RunContext, WorkItem, WorkerArtifact
from app.tools.openai_responses import OpenAIResponsesClient, OpenAIResponsesError
from app.tools.thread_memory import format_run_context

logger = logging.getLogger(__name__)


class CodeWorkerAgent(BaseAgent):
    def __init__(self, llm_client: OpenAIResponsesClient | None = None):
        super().__init__(name="code_worker", role="implementation_worker")
        self.llm_client = llm_client

    def implement(
        self,
        goal: str,
        work_item: WorkItem,
        findings: list[RepoFinding],
        run_context: RunContext | None = None,
        revision_focus: str | None = None,
    ) -> WorkerArtifact:
        relevant_findings = self._filter_findings(work_item, findings)
        if self.llm_client and self.llm_client.enabled:
            llm_artifact = self._implement_with_llm(
                goal=goal,
                work_item=work_item,
                findings=relevant_findings,
                run_context=run_context,
                revision_focus=revision_focus,
            )
            if llm_artifact is not None:
                return llm_artifact

        return self._fallback_artifact(
            goal=goal,
            work_item=work_item,
            findings=relevant_findings,
            revision_focus=revision_focus,
        )

    def _implement_with_llm(
        self,
        *,
        goal: str,
        work_item: WorkItem,
        findings: list[RepoFinding],
        run_context: RunContext | None,
        revision_focus: str | None,
    ) -> WorkerArtifact | None:
        try:
            payload = self.llm_client.generate_json(
                system_prompt=(
                    "You are a code implementation worker in a multi-agent coding assistant. "
                    "Propose concrete file-level changes. Return JSON only."
                ),
                user_prompt=(
                    "Return JSON with the structure {\"work_item_id\": \"...\", \"owner\": \"...\", "
                    "\"summary\": \"...\", \"files_touched\": [\"...\"], \"code_changes\": ["
                    "{\"file_path\": \"...\", \"change_type\": \"modify\", \"summary\": \"...\", \"proposal\": \"...\"}], "
                    "\"tests_to_run\": [\"...\"], \"risks\": [\"...\"], \"confidence\": 0.0}\n\n"
                    f"Goal: {goal}\n"
                    f"Conversation context:\n{format_run_context(run_context)}\n\n"
                    f"Work item: {work_item.model_dump_json()}\n"
                    f"Relevant repository findings: {json.dumps([finding.model_dump(mode='json') for finding in findings])}\n"
                    f"Reviewer revision focus: {revision_focus or 'None'}"
                ),
                max_output_tokens=1800,
            )
            artifact = WorkerArtifact.model_validate(payload)
            if artifact.work_item_id != work_item.work_item_id:
                return None
            return artifact
        except (OpenAIResponsesError, ValueError, TypeError):
            logger.exception("LLM code worker failed; falling back to deterministic artifact.")
            return None

    def _fallback_artifact(
        self,
        *,
        goal: str,
        work_item: WorkItem,
        findings: list[RepoFinding],
        revision_focus: str | None,
    ) -> WorkerArtifact:
        changes: list[CodeChange] = []
        files_touched: list[str] = []
        for path in work_item.write_scope[:3]:
            files_touched.append(path)
            related = next((finding for finding in findings if finding.file_path == path), None)
            proposal_lines = [
                f"Goal alignment: {goal}",
                f"Workstream focus: {work_item.title}",
                f"Acceptance criteria: {'; '.join(work_item.acceptance_criteria)}",
            ]
            if related is not None and related.excerpt:
                proposal_lines.append(f"Existing context: {related.excerpt}")
            if revision_focus:
                proposal_lines.append(f"Revision focus: {revision_focus}")
            changes.append(
                CodeChange(
                    file_path=path,
                    change_type="modify",
                    summary=f"Update {path} to satisfy {work_item.title.lower()}.",
                    proposal="\n".join(proposal_lines),
                )
            )

        tests_to_run = ["pytest -q"] if "test" in work_item.owner or "tests" in work_item.title.lower() else ["pytest -q", "python3 -m py_compile app"]
        risks = [
            "Proposals are repository-aware but not applied automatically.",
            "Integration points should be verified before merging changes.",
        ]
        if revision_focus:
            risks.append("Reviewer issues should be re-checked after applying revisions.")

        return WorkerArtifact(
            work_item_id=work_item.work_item_id,
            owner=work_item.owner,
            summary=f"Proposed coding changes for {work_item.title}.",
            runtime_ms=0.0,
            files_touched=files_touched,
            code_changes=changes,
            tests_to_run=tests_to_run,
            risks=risks,
            confidence=0.55 if revision_focus else 0.65,
        )

    def _filter_findings(self, work_item: WorkItem, findings: list[RepoFinding]) -> list[RepoFinding]:
        relevant = [finding for finding in findings if finding.file_path in work_item.write_scope]
        return relevant or findings[:5]
