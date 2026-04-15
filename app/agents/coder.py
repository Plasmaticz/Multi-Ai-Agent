from __future__ import annotations

import json
import logging
from pathlib import Path

from app.agents.base import BaseAgent
from app.schemas.state import CodeChange, RepoFinding, RunContext, WorkItem, WorkerArtifact
from app.tools.openai_responses import OpenAIResponsesClient, OpenAIResponsesError
from app.tools.thread_memory import format_run_context
from app.tools.workspace_tools import WorkspaceTool

logger = logging.getLogger(__name__)


class CodeWorkerAgent(BaseAgent):
    def __init__(self, llm_client: OpenAIResponsesClient | None = None, workspace_path: Path | None = None):
        super().__init__(name="code_worker", role="implementation_worker")
        self.llm_client = llm_client
        self.workspace_tool = WorkspaceTool(workspace_path or Path.cwd())

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
                return self._sanitize_artifact(llm_artifact, work_item)

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
                    "{\"file_path\": \"...\", \"change_type\": \"modify\", \"summary\": \"...\", "
                    "\"proposed_content\": \"...\", \"unified_diff\": \"\", \"apply_status\": \"pending\"}], "
                    "\"tests_to_run\": [\"...\"], \"risks\": [\"...\"], \"confidence\": 0.0}\n\n"
                    f"Goal: {goal}\n"
                    f"Conversation context:\n{format_run_context(run_context)}\n\n"
                    f"Work item: {work_item.model_dump_json()}\n"
                    f"Relevant repository findings: {json.dumps([finding.model_dump(mode='json') for finding in findings])}\n"
                    f"Current file contents: {json.dumps(self._current_file_context(work_item))}\n"
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
            existing_content = self.workspace_tool.read_text(path)
            proposed_content = self._build_fallback_content(
                file_path=path,
                existing_content=existing_content,
                goal=goal,
                work_item=work_item,
                related=related.excerpt if related is not None else "",
                revision_focus=revision_focus,
            )
            changes.append(
                CodeChange(
                    file_path=path,
                    change_type="modify",
                    summary=f"Update {path} to satisfy {work_item.title.lower()}.",
                    proposed_content=proposed_content,
                    unified_diff=self.workspace_tool.build_unified_diff(path, proposed_content),
                    apply_status="pending",
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

    def _sanitize_artifact(self, artifact: WorkerArtifact, work_item: WorkItem) -> WorkerArtifact:
        allowed_paths = set(work_item.write_scope)
        sanitized_changes: list[CodeChange] = []
        files_touched: list[str] = []
        risks = list(artifact.risks)

        for change in artifact.code_changes:
            if change.change_type not in {"modify", "create"}:
                risks.append(f"Rejected unsupported change type for {change.file_path}.")
                continue
            if change.file_path not in allowed_paths:
                risks.append(f"Rejected change outside write scope: {change.file_path}.")
                continue
            resolved = self.workspace_tool.resolve_path(change.file_path)
            if resolved is None:
                risks.append(f"Rejected path outside workspace: {change.file_path}.")
                continue
            if resolved.exists() and self.workspace_tool.is_binary_path(resolved):
                risks.append(f"Rejected binary file change: {change.file_path}.")
                continue
            proposed_content = change.proposed_content or self.workspace_tool.read_text(change.file_path)
            unified_diff = self.workspace_tool.build_unified_diff(change.file_path, proposed_content)
            if not unified_diff and proposed_content == self.workspace_tool.read_text(change.file_path):
                risks.append(f"No effective diff produced for {change.file_path}.")
                continue
            sanitized_changes.append(
                CodeChange(
                    file_path=change.file_path,
                    change_type=change.change_type,
                    summary=change.summary,
                    proposed_content=proposed_content,
                    unified_diff=unified_diff,
                    apply_status="pending",
                )
            )
            files_touched.append(change.file_path)

        artifact.code_changes = sanitized_changes
        artifact.files_touched = list(dict.fromkeys(files_touched))
        artifact.risks = list(dict.fromkeys(risks))
        return artifact

    def _current_file_context(self, work_item: WorkItem) -> dict[str, str]:
        return {
            path: self.workspace_tool.read_text(path)
            for path in work_item.write_scope[:3]
        }

    def _build_fallback_content(
        self,
        *,
        file_path: str,
        existing_content: str,
        goal: str,
        work_item: WorkItem,
        related: str,
        revision_focus: str | None,
    ) -> str:
        extension = Path(file_path).suffix.lower()
        comment_prefix = "//"
        if extension in {".py", ".sh", ".yml", ".yaml"}:
            comment_prefix = "#"
        elif extension in {".html", ".xml"}:
            comment_prefix = "<!--"

        note_parts = [
            f"{comment_prefix} Planned change for {work_item.title}",
            f"{comment_prefix} Goal: {goal}",
        ]
        if related:
            note_parts.append(f"{comment_prefix} Context: {related}")
        if revision_focus:
            note_parts.append(f"{comment_prefix} Revision focus: {revision_focus}")
        note = "\n".join(note_parts)

        if comment_prefix == "<!--":
            note = "\n".join(
                [
                    f"<!-- Planned change for {work_item.title} -->",
                    f"<!-- Goal: {goal} -->",
                    f"<!-- Context: {related or 'N/A'} -->",
                    *( [f"<!-- Revision focus: {revision_focus} -->"] if revision_focus else [] ),
                ]
            )

        existing = existing_content.rstrip("\n")
        if not existing:
            return f"{note}\n"
        return f"{existing}\n\n{note}\n"
