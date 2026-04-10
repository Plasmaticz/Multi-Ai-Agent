from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from time import perf_counter
from typing import Any, Callable

from app.agents.architect import ArchitectAgent
from app.agents.coder import CodeWorkerAgent
from app.agents.finalizer import FinalizerAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.repo_explorer import RepoExplorerAgent
from app.agents.reviewer import ReviewerAgent
from app.agents.validator import ValidatorAgent
from app.config import Settings, get_settings
from app.schemas.state import ProjectState, RunContext, WorkItem, WorkerArtifact
from app.schemas.tasks import TaskStatus, TaskType
from app.tools.openai_responses import OpenAIResponsesClient
from app.tools.repo_tools import RepoSearchTool
from app.tools.storage import ProjectStore


class CrewRunner:
    def __init__(
        self,
        settings: Settings | None = None,
        store: ProjectStore | None = None,
        search_provider=None,
        event_callback: Callable[[dict[str, Any]], None] | None = None,
    ):
        self.settings = settings or get_settings()
        self.store = store or ProjectStore()
        self.event_callback = event_callback
        self.llm_client = OpenAIResponsesClient(
            api_key=self.settings.openai_api_key,
            model=self.settings.openai_model,
            timeout_seconds=self.settings.openai_timeout_seconds,
            base_url=self.settings.openai_base_url,
        )

        repo_search = RepoSearchTool(workspace_path=self.settings.workspace_path)
        self.orchestrator = OrchestratorAgent()
        self.repo_explorer = RepoExplorerAgent(repo_search=repo_search, llm_client=self.llm_client)
        self.architect = ArchitectAgent(llm_client=self.llm_client)
        self.code_worker = CodeWorkerAgent(llm_client=self.llm_client)
        self.reviewer = ReviewerAgent(llm_client=self.llm_client)
        self.validator = ValidatorAgent()
        self.finalizer = FinalizerAgent()

    def run(
        self,
        goal: str,
        companies: list[str] | None = None,
        request_id: str | None = None,
        run_context: RunContext | None = None,
    ) -> ProjectState:
        state = self.orchestrator.initialize_project(goal=goal, request_id=request_id)
        run_started_at = perf_counter()
        state.run_context = run_context
        state.metadata["llm_enabled"] = self.llm_client.enabled
        state.metadata["implementation_concurrency"] = max(1, self.settings.max_concurrent_research)
        state.metadata["thread_count"] = max(1, self.settings.max_concurrent_research)
        state.metadata["execution_metrics"] = {
            "configured_thread_count": max(1, self.settings.max_concurrent_research),
            "active_worker_threads": 0,
            "total_run_time_ms": 0.0,
            "parallel_worker_wall_time_ms": 0.0,
            "estimated_sequential_worker_time_ms": 0.0,
            "parallel_speedup": 1.0,
            "worker_runtimes_ms": {},
            "implementation_passes": [],
        }

        state.tasks = [
            {
                "task_id": f"{state.request_id}-plan",
                "task_type": TaskType.plan.value,
                "assigned_to": "orchestrator",
                "instructions": "Plan the coding workflow and scope the work items.",
                "status": TaskStatus.pending.value,
                "input_data": {},
                "output_data": {},
            },
            {
                "task_id": f"{state.request_id}-explore",
                "task_type": TaskType.explore.value,
                "assigned_to": "repo_explorer",
                "instructions": "Scan the repository for relevant files, symbols, and context.",
                "status": TaskStatus.pending.value,
                "input_data": {},
                "output_data": {},
            },
            {
                "task_id": f"{state.request_id}-architect",
                "task_type": TaskType.architect.value,
                "assigned_to": "architect",
                "instructions": "Convert repository findings into disjoint coding work items.",
                "status": TaskStatus.pending.value,
                "input_data": {},
                "output_data": {},
            },
        ]

        self._emit_event("orchestrator", "plan", "started", "Planning coding workflow.")
        self._set_task_status(state, TaskType.plan, TaskStatus.in_progress)
        self._set_task_status(state, TaskType.plan, TaskStatus.completed)
        self._emit_event("orchestrator", "plan", "completed", "Workflow plan created.")
        self.store.save(state)

        state.status = "explore"
        self._emit_event("repo_explorer", "explore", "started", "Scanning repository for relevant context.")
        self._set_task_status(state, TaskType.explore, TaskStatus.in_progress)
        state.repo_findings = self.repo_explorer.explore(goal=goal, run_context=run_context)
        self._set_task_status(state, TaskType.explore, TaskStatus.completed)
        self._emit_event("repo_explorer", "explore", "completed", "Repository scan finished.")
        state.touch()
        self.store.save(state)

        state.status = "architect"
        self._emit_event("architect", "architect", "started", "Building coding work items.")
        self._set_task_status(state, TaskType.architect, TaskStatus.in_progress)
        fallback_items = self.orchestrator.build_work_items(state.request_id, goal, state.repo_findings)
        state.implementation_plan = self.architect.plan_work(
            goal=goal,
            findings=state.repo_findings,
            fallback_items=fallback_items,
            run_context=state.run_context,
        )
        state.tasks = [task.model_dump(mode="json") for task in self.orchestrator.plan(state.request_id, state.implementation_plan)]
        self._set_task_status(state, TaskType.plan, TaskStatus.completed)
        self._set_task_status(state, TaskType.explore, TaskStatus.completed)
        self._set_task_status(state, TaskType.architect, TaskStatus.completed)
        self._emit_event("architect", "architect", "completed", "Coding work items ready.")
        state.touch()
        self.store.save(state)

        state.status = "implement"
        self._emit_event("code_worker", "implement", "started", "Running parallel code workers.")
        state.worker_outputs = self._run_parallel_implementation(state=state, revision_focus=None)
        self._emit_event("code_worker", "implement", "completed", "Initial implementation proposals finished.")
        state.touch()
        self.store.save(state)

        attempts = max(1, self.settings.max_review_loops + 1)
        revision_focus: str | None = None
        for attempt in range(attempts):
            state.status = "review"
            self._emit_event("reviewer", "review", "started", "Reviewing proposed code changes.")
            self._set_task_status(state, TaskType.review, TaskStatus.in_progress)
            review = self.reviewer.review(state=state, worker_outputs=state.worker_outputs)
            state.review_notes.append(review)
            self._set_task_status(state, TaskType.review, TaskStatus.completed if review.passed else TaskStatus.failed)
            self._emit_event(
                "reviewer",
                "review",
                "completed" if review.passed else "needs_revision",
                "Review passed." if review.passed else "; ".join(review.issues),
            )
            state.touch()
            self.store.save(state)

            if review.passed:
                self._set_task_status(state, TaskType.fix, TaskStatus.completed)
                break

            if attempt == attempts - 1:
                state.status = "needs_human_review"
                break

            revision_focus = "; ".join(review.issues)
            state.status = "fix"
            self._emit_event("fixer", "fix", "started", "Revising worker outputs from review feedback.")
            self._set_task_status(state, TaskType.fix, TaskStatus.in_progress)
            state.worker_outputs = self._run_parallel_implementation(state=state, revision_focus=revision_focus)
            self._set_task_status(state, TaskType.fix, TaskStatus.completed)
            self._emit_event("fixer", "fix", "completed", "Revision pass finished.")
            state.touch()
            self.store.save(state)

        state.status = "validate"
        self._emit_event("validator", "validate", "started", "Preparing validation commands.")
        self._set_task_status(state, TaskType.validate, TaskStatus.in_progress)
        state.validation_commands = self.validator.build_validation_commands(state.worker_outputs)
        self._set_task_status(state, TaskType.validate, TaskStatus.completed)
        self._emit_event("validator", "validate", "completed", "Validation commands prepared.")
        state.touch()
        self.store.save(state)

        state.status = "complete" if state.review_notes and state.review_notes[-1].passed else "needs_human_review"
        self._emit_event("orchestrator", "finalize", "started", "Finalizing coding response.")
        self._set_task_status(state, TaskType.finalize, TaskStatus.in_progress)
        execution_metrics = state.metadata.setdefault("execution_metrics", {})
        execution_metrics["total_run_time_ms"] = round((perf_counter() - run_started_at) * 1000, 2)
        state.final_output = self.finalizer.finalize(state, state.worker_outputs)
        self._set_task_status(
            state,
            TaskType.finalize,
            TaskStatus.completed if state.status == "complete" else TaskStatus.failed,
        )
        self._emit_event(
            "orchestrator",
            "finalize",
            "completed" if state.status == "complete" else "failed",
            "Coding workflow completed." if state.status == "complete" else "Coding workflow needs human review.",
        )
        execution_metrics["total_run_time_ms"] = round((perf_counter() - run_started_at) * 1000, 2)
        state.touch()
        self.store.save(state)
        return state

    def _run_parallel_implementation(
        self,
        state: ProjectState,
        revision_focus: str | None,
    ) -> list[WorkerArtifact]:
        work_items = state.implementation_plan
        if not work_items:
            return []

        phase_started_at = perf_counter()
        worker_count = min(len(work_items), max(1, self.settings.max_concurrent_research))
        outputs_by_item: dict[str, WorkerArtifact] = {}
        worker_durations_ms: dict[str, float] = {}
        for work_item in work_items:
            self._set_task_status(
                state,
                TaskType.implement,
                TaskStatus.in_progress,
                work_item_id=work_item.work_item_id,
            )
            self._emit_event(
                work_item.owner,
                "implement_work_item",
                "started",
                f"Implementing {work_item.title}.",
            )
        state.touch()
        self.store.save(state)

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_item = {
                executor.submit(
                    self._execute_work_item,
                    state.user_goal,
                    work_item,
                    state.repo_findings,
                    state.run_context,
                    revision_focus,
                ): work_item
                for work_item in work_items
            }

            for future in as_completed(future_to_item):
                work_item = future_to_item[future]
                try:
                    artifact, runtime_ms, error = future.result()
                    worker_durations_ms[work_item.owner] = round(runtime_ms, 2)
                    if error is None and artifact is not None:
                        artifact.runtime_ms = round(runtime_ms, 2)
                        outputs_by_item[work_item.work_item_id] = artifact
                        self._set_task_status(
                            state,
                            TaskType.implement,
                            TaskStatus.completed,
                            work_item_id=work_item.work_item_id,
                        )
                        self._emit_event(
                            work_item.owner,
                            "implement_work_item",
                            "completed",
                            f"Completed {work_item.title} in {runtime_ms:.2f} ms.",
                        )
                    else:
                        outputs_by_item[work_item.work_item_id] = WorkerArtifact(
                            work_item_id=work_item.work_item_id,
                            owner=work_item.owner,
                            summary=f"Worker failed for {work_item.title}: {error}",
                            runtime_ms=round(runtime_ms, 2),
                            files_touched=work_item.write_scope,
                            code_changes=[],
                            tests_to_run=[],
                            risks=[f"Worker execution failed: {error}"],
                            confidence=0.0,
                        )
                        self._set_task_status(
                            state,
                            TaskType.implement,
                            TaskStatus.failed,
                            work_item_id=work_item.work_item_id,
                        )
                        self._emit_event(
                            work_item.owner,
                            "implement_work_item",
                            "failed",
                            f"Failed {work_item.title} after {runtime_ms:.2f} ms: {error}",
                        )
                except Exception as exc:
                    outputs_by_item[work_item.work_item_id] = WorkerArtifact(
                        work_item_id=work_item.work_item_id,
                        owner=work_item.owner,
                        summary=f"Worker failed for {work_item.title}: {exc}",
                        runtime_ms=0.0,
                        files_touched=work_item.write_scope,
                        code_changes=[],
                        tests_to_run=[],
                        risks=[f"Worker execution failed: {exc}"],
                        confidence=0.0,
                    )
                    self._set_task_status(
                        state,
                        TaskType.implement,
                        TaskStatus.failed,
                        work_item_id=work_item.work_item_id,
                    )
                    self._emit_event(
                        work_item.owner,
                        "implement_work_item",
                        "failed",
                        f"Failed {work_item.title}: {exc}",
                    )
                state.touch()
                self.store.save(state)

        phase_wall_time_ms = round((perf_counter() - phase_started_at) * 1000, 2)
        sequential_baseline_ms = round(sum(worker_durations_ms.values()), 2)
        speedup = round(sequential_baseline_ms / phase_wall_time_ms, 2) if phase_wall_time_ms > 0 else 1.0
        self._record_implementation_metrics(
            state=state,
            worker_count=worker_count,
            worker_durations_ms=worker_durations_ms,
            phase_wall_time_ms=phase_wall_time_ms,
            sequential_baseline_ms=sequential_baseline_ms,
            speedup=speedup,
            revision_focus=revision_focus,
        )

        return [outputs_by_item[item.work_item_id] for item in work_items if item.work_item_id in outputs_by_item]

    def _execute_work_item(
        self,
        goal: str,
        work_item: WorkItem,
        findings,
        run_context: RunContext | None,
        revision_focus: str | None,
    ) -> tuple[WorkerArtifact | None, float, str | None]:
        started_at = perf_counter()
        try:
            artifact = self.code_worker.implement(
                goal=goal,
                work_item=work_item,
                findings=findings,
                run_context=run_context,
                revision_focus=revision_focus,
            )
            return artifact, (perf_counter() - started_at) * 1000, None
        except Exception as exc:  # pragma: no cover - normalized into worker artifacts
            return None, (perf_counter() - started_at) * 1000, str(exc)

    def _record_implementation_metrics(
        self,
        *,
        state: ProjectState,
        worker_count: int,
        worker_durations_ms: dict[str, float],
        phase_wall_time_ms: float,
        sequential_baseline_ms: float,
        speedup: float,
        revision_focus: str | None,
    ) -> None:
        execution_metrics = state.metadata.setdefault("execution_metrics", {})
        implementation_passes = execution_metrics.setdefault("implementation_passes", [])
        implementation_passes.append(
            {
                "configured_thread_count": max(1, self.settings.max_concurrent_research),
                "active_worker_threads": worker_count,
                "phase": "revision" if revision_focus else "initial",
                "parallel_worker_wall_time_ms": phase_wall_time_ms,
                "estimated_sequential_worker_time_ms": sequential_baseline_ms,
                "parallel_speedup": speedup,
                "worker_runtimes_ms": worker_durations_ms,
            }
        )
        execution_metrics["configured_thread_count"] = max(1, self.settings.max_concurrent_research)
        execution_metrics["active_worker_threads"] = worker_count
        execution_metrics["parallel_worker_wall_time_ms"] = round(
            sum(item["parallel_worker_wall_time_ms"] for item in implementation_passes),
            2,
        )
        execution_metrics["estimated_sequential_worker_time_ms"] = round(
            sum(item["estimated_sequential_worker_time_ms"] for item in implementation_passes),
            2,
        )
        total_parallel = execution_metrics["parallel_worker_wall_time_ms"]
        total_sequential = execution_metrics["estimated_sequential_worker_time_ms"]
        execution_metrics["parallel_speedup"] = round(total_sequential / total_parallel, 2) if total_parallel > 0 else 1.0

        aggregated_runtimes: dict[str, float] = {}
        for item in implementation_passes:
            for owner, duration in item["worker_runtimes_ms"].items():
                aggregated_runtimes[owner] = round(aggregated_runtimes.get(owner, 0.0) + duration, 2)
        execution_metrics["worker_runtimes_ms"] = aggregated_runtimes

    def _set_task_status(
        self,
        state: ProjectState,
        task_type: TaskType,
        status: TaskStatus,
        work_item_id: str | None = None,
    ) -> None:
        for task in state.tasks:
            if task.get("task_type") != task_type.value:
                continue
            if work_item_id is not None and task.get("input_data", {}).get("work_item_id") != work_item_id:
                continue
            task["status"] = status.value
            if work_item_id is None:
                break

    def _emit_event(
        self,
        agent_name: str,
        event_type: str,
        status: str,
        message: str,
    ) -> None:
        if self.event_callback is None:
            return
        self.event_callback(
            {
                "agent_name": agent_name,
                "event_type": event_type,
                "status": status,
                "message": message,
            }
        )
