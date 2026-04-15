from __future__ import annotations

from app.agents.base import BaseAgent
from app.schemas.state import ProjectState, ValidationResult, WorkerArtifact


class FinalizerAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="finalizer", role="delivery")

    def finalize(self, state: ProjectState, worker_outputs: list[WorkerArtifact]) -> str:
        execution_metrics = state.metadata.get("execution_metrics", {})
        apply_status = state.metadata.get("apply_status", "pending")
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
                            f"- Apply status: {change.apply_status}",
                        ]
                    )
                    if change.unified_diff:
                        block.extend(
                            [
                                "```diff",
                                change.unified_diff,
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

        sections.append("## Validation")
        sections.extend(self._render_validation_results(state.validation_results, state.validation_commands))

        sections.append("## Apply Readiness")
        if apply_status == "ready":
            sections.append("- Patch set is eligible for guarded apply.")
        elif apply_status == "applied":
            sections.append("- Patch set was already applied to the selected workspace.")
        elif apply_status == "blocked":
            sections.append("- Patch set is not safe to apply yet because validation failed.")
        else:
            sections.append(f"- Apply status: `{apply_status}`")

        sections.append("## Execution Metrics")
        if execution_metrics:
            sections.append(f"- Configured thread count: `{execution_metrics.get('configured_thread_count', 1)}`")
            sections.append(f"- Active worker threads: `{execution_metrics.get('active_worker_threads', 0)}`")
            sections.append(f"- Total run time: `{self._format_ms(execution_metrics.get('total_run_time_ms', 0.0))}`")
            sections.append(
                f"- Parallel worker wall time: `{self._format_ms(execution_metrics.get('parallel_worker_wall_time_ms', 0.0))}`"
            )
            sections.append(
                f"- Estimated sequential baseline: `{self._format_ms(execution_metrics.get('estimated_sequential_worker_time_ms', 0.0))}`"
            )
            sections.append(f"- Parallel speedup: `{execution_metrics.get('parallel_speedup', 1.0)}x`")
            worker_runtimes = execution_metrics.get("worker_runtimes_ms", {})
            if worker_runtimes:
                sections.append("Per-worker run time:")
                sections.extend(
                    f"- `{owner}`: `{self._format_ms(duration_ms)}`"
                    for owner, duration_ms in worker_runtimes.items()
                )
        else:
            sections.append("- Execution metrics were not captured for this run.")

        sections.append("## Remaining Risks")
        all_risks: list[str] = []
        for artifact in worker_outputs:
            all_risks.extend(artifact.risks)
        if all_risks:
            sections.extend(f"- {risk}" for risk in list(dict.fromkeys(all_risks)))
        else:
            sections.append("- Review generated no additional risks.")

        return "\n\n".join(section for section in sections if section)

    def summarize_run(self, state: ProjectState, *, workspace_dir: str, run_status: str) -> str:
        execution_metrics = state.metadata.get("execution_metrics", {})
        validation_results = state.validation_results
        lines = [
            "# Run Summary",
            f"- Goal: {state.user_goal}",
            f"- Workspace: `{workspace_dir}`",
            f"- Final status: `{run_status}`",
            f"- Apply status: `{state.metadata.get('apply_status', 'pending')}`",
            f"- Configured thread count: `{execution_metrics.get('configured_thread_count', 1)}`",
            f"- Active worker threads: `{execution_metrics.get('active_worker_threads', 0)}`",
            f"- Total run time: `{self._format_ms(execution_metrics.get('total_run_time_ms', 0.0))}`",
            f"- Parallel speedup: `{execution_metrics.get('parallel_speedup', 1.0)}x`",
        ]
        if validation_results:
            lines.append("- Validation results:")
            lines.extend(
                f"  - `{result.command}`: `{result.status}`"
                + (f" (exit {result.exit_code})" if result.exit_code is not None else "")
                + (f" - {result.reason}" if result.reason else "")
                for result in validation_results
            )
        else:
            lines.append("- Validation results: none recorded")
        return "\n".join(lines)

    def _format_ms(self, value: float) -> str:
        return f"{float(value):.2f} ms"

    def _render_validation_results(
        self,
        results: list[ValidationResult],
        commands: list[str],
    ) -> list[str]:
        if results:
            lines: list[str] = []
            for result in results:
                summary = f"- `{result.command}`: `{result.status}`"
                if result.exit_code is not None:
                    summary += f" (exit `{result.exit_code}`)"
                if result.reason:
                    summary += f" - {result.reason}"
                lines.append(summary)
                if result.stderr:
                    lines.append("```text")
                    lines.append(result.stderr[:800])
                    lines.append("```")
            return lines
        if commands:
            return [f"- `{command}`" for command in commands]
        return ["- No validation commands were detected for this workspace."]
