from __future__ import annotations

from app.agents.base import BaseAgent
from app.schemas.state import ProjectState, WorkerArtifact


class FinalizerAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="finalizer", role="delivery")

    def finalize(self, state: ProjectState, worker_outputs: list[WorkerArtifact]) -> str:
        sections: list[str] = [
            "# Multi-Agent Coding Plan",
            "## Requested Change",
            state.user_goal,
            "## Implementation Plan",
        ]

        if state.implementation_plan:
            for item in state.implementation_plan:
                sections.append(
                    "\n".join(
                        [
                            f"### {item.title}",
                            f"Owner: `{item.owner}`",
                            f"Rationale: {item.rationale}",
                            "Acceptance criteria:",
                            *[f"- {criterion}" for criterion in item.acceptance_criteria],
                            "Write scope:",
                            *[f"- `{path}`" for path in item.write_scope],
                        ]
                    )
                )

        sections.append("## Proposed File Changes")
        if worker_outputs:
            for artifact in worker_outputs:
                block = [
                    f"### {artifact.owner}",
                    artifact.summary,
                ]
                for change in artifact.code_changes:
                    block.extend(
                        [
                            f"#### `{change.file_path}`",
                            f"- Change type: {change.change_type}",
                            f"- Summary: {change.summary}",
                            "```text",
                            change.proposal,
                            "```",
                        ]
                    )
                if artifact.risks:
                    block.append("Risks:")
                    block.extend(f"- {risk}" for risk in artifact.risks)
                sections.append("\n".join(block))
        else:
            sections.append("No worker outputs were produced.")

        sections.append("## Review")
        if state.review_notes:
            latest = state.review_notes[-1]
            sections.append(f"Passed: `{latest.passed}`")
            if latest.issues:
                sections.extend(f"- {issue}" for issue in latest.issues)
            else:
                sections.append("- No blocking issues detected.")
        else:
            sections.append("No review completed.")

        sections.append("## Validation Commands")
        sections.extend(f"- `{command}`" for command in state.validation_commands or ["pytest -q", "python3 -m py_compile app"])

        sections.append("## Remaining Risks")
        all_risks: list[str] = []
        for artifact in worker_outputs:
            all_risks.extend(artifact.risks)
        if all_risks:
            sections.extend(f"- {risk}" for risk in list(dict.fromkeys(all_risks)))
        else:
            sections.append("- Review generated no additional risks.")

        return "\n\n".join(section for section in sections if section)
