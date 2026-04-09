from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
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
        state.run_context = run_context
        state.metadata["llm_enabled"] = self.llm_client.enabled
        state.metadata["implementation_concurrency"] = max(1, self.settings.max_concurrent_research)

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

        worker_count = min(len(work_items), max(1, self.settings.max_concurrent_research))
        outputs_by_item: dict[str, WorkerArtifact] = {}
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
                    self.code_worker.implement,
                    goal=state.user_goal,
                    work_item=work_item,
                    findings=state.repo_findings,
                    run_context=state.run_context,
                    revision_focus=revision_focus,
                ): work_item
                for work_item in work_items
            }

            for future in as_completed(future_to_item):
                work_item = future_to_item[future]
                try:
                    outputs_by_item[work_item.work_item_id] = future.result()
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
                        f"Completed {work_item.title}.",
                    )
                except Exception as exc:
                    outputs_by_item[work_item.work_item_id] = WorkerArtifact(
                        work_item_id=work_item.work_item_id,
                        owner=work_item.owner,
                        summary=f"Worker failed for {work_item.title}: {exc}",
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

        return [outputs_by_item[item.work_item_id] for item in work_items if item.work_item_id in outputs_by_item]

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
