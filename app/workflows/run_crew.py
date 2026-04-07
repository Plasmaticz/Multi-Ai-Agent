from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable

from app.agents.analyst import AnalystAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.researcher import ResearcherAgent
from app.agents.reviewer import ReviewerAgent
from app.agents.writer import WriterAgent
from app.config import Settings, get_settings
from app.schemas.state import ProjectState, ResearchNote
from app.schemas.tasks import TaskStatus, TaskType
from app.tools.openai_responses import OpenAIResponsesClient
from app.tools.scraper import PageFetcher
from app.tools.storage import ProjectStore
from app.tools.web_search import SearchProvider, WebSearchTool


class CrewRunner:
    def __init__(
        self,
        settings: Settings | None = None,
        store: ProjectStore | None = None,
        search_provider: SearchProvider | None = None,
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

        self.orchestrator = OrchestratorAgent()
        self.researcher = ResearcherAgent(
            search_tool=WebSearchTool(provider=search_provider),
            page_fetcher=PageFetcher(timeout_seconds=self.settings.request_timeout_seconds),
        )
        self.analyst = AnalystAgent(llm_client=self.llm_client)
        self.writer = WriterAgent(llm_client=self.llm_client)
        self.reviewer = ReviewerAgent(llm_client=self.llm_client)

    def run(
        self,
        goal: str,
        companies: list[str] | None = None,
        request_id: str | None = None,
    ) -> ProjectState:
        company_list = self.orchestrator.resolve_companies(
            goal=goal,
            explicit_companies=companies,
            fallback_companies=self.settings.default_company_list,
        )

        state = self.orchestrator.initialize_project(
            goal=goal,
            companies=company_list,
            request_id=request_id,
        )
        state.metadata["llm_enabled"] = self.llm_client.enabled
        state.metadata["research_concurrency"] = min(
            len(company_list),
            max(1, self.settings.max_concurrent_research),
        )
        state.tasks = [
            task.model_dump(mode="json")
            for task in self.orchestrator.plan(state.request_id, company_list)
        ]
        self._emit_event("orchestrator", "plan", "started", "Planning workflow.")
        self._set_task_status(state, TaskType.plan, TaskStatus.in_progress)
        self._set_task_status(state, TaskType.plan, TaskStatus.completed)
        self._emit_event("orchestrator", "plan", "completed", "Workflow plan created.")
        self.store.save(state)

        state.status = "research"
        state.touch()
        self._emit_event("researcher", "research", "started", "Running concurrent company research.")
        state.research_notes = self._run_parallel_research(state=state, company_list=company_list)
        state.touch()
        self._emit_event("researcher", "research", "completed", "Research stage finished.")
        self.store.save(state)

        state.status = "analysis"
        self._emit_event("analyst", "analysis", "started", "Generating company comparison.")
        self._set_task_status(state, TaskType.analyze, TaskStatus.in_progress)
        state.analysis = self.analyst.analyze(
            notes=state.research_notes,
            criteria=["cost", "scalability", "technology"],
        )
        self._set_task_status(state, TaskType.analyze, TaskStatus.completed)
        state.touch()
        self._emit_event("analyst", "analysis", "completed", "Analysis stage finished.")
        self.store.save(state)

        state.status = "review"
        revision_focus: str | None = None
        attempts = max(1, self.settings.max_review_loops + 1)
        for _ in range(attempts):
            self._emit_event("writer", "write", "started", "Drafting report.")
            self._set_task_status(state, TaskType.write, TaskStatus.in_progress)
            draft = self.writer.write_report(state, revision_focus=revision_focus)
            state.drafts.append(draft)
            self._set_task_status(state, TaskType.write, TaskStatus.completed)
            self._emit_event("writer", "write", "completed", "Draft generated.")

            self._emit_event("reviewer", "review", "started", "Reviewing draft.")
            self._set_task_status(state, TaskType.review, TaskStatus.in_progress)
            review = self.reviewer.review(
                state=state,
                draft=draft,
                min_sources_per_company=self.settings.min_sources_per_company,
            )
            state.review_notes.append(review)
            self._set_task_status(state, TaskType.review, TaskStatus.completed)
            state.touch()
            self._emit_event(
                "reviewer",
                "review",
                "completed" if review.passed else "needs_revision",
                "Review passed." if review.passed else "; ".join(review.issues),
            )
            self.store.save(state)

            if review.passed:
                state.status = "complete"
                self._emit_event("orchestrator", "finalize", "started", "Finalizing response.")
                self._set_task_status(state, TaskType.finalize, TaskStatus.in_progress)
                state.final_output = draft
                self._set_task_status(state, TaskType.finalize, TaskStatus.completed)
                state.touch()
                self._emit_event("orchestrator", "finalize", "completed", "Run completed.")
                self.store.save(state)
                return state

            revision_focus = "; ".join(review.issues)

        state.status = "needs_human_review"
        self._set_task_status(state, TaskType.finalize, TaskStatus.failed)
        state.final_output = state.drafts[-1] if state.drafts else ""
        state.touch()
        self._emit_event("orchestrator", "finalize", "failed", "Run needs human review.")
        self.store.save(state)
        return state

    def _set_task_status(
        self,
        state: ProjectState,
        task_type: TaskType,
        status: TaskStatus,
        company: str | None = None,
    ) -> None:
        for task in state.tasks:
            if task.get("task_type") != task_type.value:
                continue
            if company is not None and task.get("input_data", {}).get("company") != company:
                continue
            task["status"] = status.value
            break

    def _run_parallel_research(
        self,
        state: ProjectState,
        company_list: list[str],
    ) -> list[ResearchNote]:
        max_sources = max(3, self.settings.min_sources_per_company)
        worker_count = min(len(company_list), max(1, self.settings.max_concurrent_research))
        notes_by_company: dict[str, ResearchNote] = {}

        for company in company_list:
            self._set_task_status(
                state,
                TaskType.research,
                TaskStatus.in_progress,
                company=company,
            )
            self._emit_event("researcher", "research_company", "started", f"Researching {company}.")
        state.touch()
        self.store.save(state)

        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            future_to_company = {
                executor.submit(
                    self.researcher.research_company,
                    company=company,
                    goal=state.user_goal,
                    criteria=state.requirements,
                    max_sources=max_sources,
                ): company
                for company in company_list
            }

            for future in as_completed(future_to_company):
                company = future_to_company[future]
                try:
                    notes_by_company[company] = future.result()
                    self._set_task_status(
                        state,
                        TaskType.research,
                        TaskStatus.completed,
                        company=company,
                    )
                    self._emit_event(
                        "researcher",
                        "research_company",
                        "completed",
                        f"Completed research for {company}.",
                    )
                except Exception as exc:
                    notes_by_company[company] = self._build_failed_research_note(
                        company=company,
                        goal=state.user_goal,
                        error=str(exc),
                    )
                    self._set_task_status(
                        state,
                        TaskType.research,
                        TaskStatus.failed,
                        company=company,
                    )
                    self._emit_event(
                        "researcher",
                        "research_company",
                        "failed",
                        f"Research failed for {company}: {exc}",
                    )
                state.touch()
                self.store.save(state)

        return [notes_by_company[company] for company in company_list]

    def _build_failed_research_note(
        self,
        company: str,
        goal: str,
        error: str,
    ) -> ResearchNote:
        return ResearchNote(
            company=company,
            question=f"{company} {goal}",
            facts=[f"Automated research failed for {company}: {error}"],
            sources=[],
            confidence=0.0,
        )

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
