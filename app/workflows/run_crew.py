from __future__ import annotations

from app.agents.analyst import AnalystAgent
from app.agents.orchestrator import OrchestratorAgent
from app.agents.researcher import ResearcherAgent
from app.agents.reviewer import ReviewerAgent
from app.agents.writer import WriterAgent
from app.config import Settings, get_settings
from app.schemas.state import ProjectState
from app.schemas.tasks import TaskStatus, TaskType
from app.tools.scraper import PageFetcher
from app.tools.storage import ProjectStore
from app.tools.web_search import SearchProvider, WebSearchTool


class CrewRunner:
    def __init__(
        self,
        settings: Settings | None = None,
        store: ProjectStore | None = None,
        search_provider: SearchProvider | None = None,
    ):
        self.settings = settings or get_settings()
        self.store = store or ProjectStore()

        self.orchestrator = OrchestratorAgent()
        self.researcher = ResearcherAgent(
            search_tool=WebSearchTool(provider=search_provider),
            page_fetcher=PageFetcher(timeout_seconds=self.settings.request_timeout_seconds),
        )
        self.analyst = AnalystAgent()
        self.writer = WriterAgent()
        self.reviewer = ReviewerAgent()

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
        state.tasks = [
            task.model_dump(mode="json")
            for task in self.orchestrator.plan(state.request_id, company_list)
        ]
        self._set_task_status(state, TaskType.plan, TaskStatus.in_progress)
        self._set_task_status(state, TaskType.plan, TaskStatus.completed)
        self.store.save(state)

        state.status = "research"
        state.touch()
        for company in company_list:
            self._set_task_status(
                state,
                TaskType.research,
                TaskStatus.in_progress,
                company=company,
            )
            note = self.researcher.research_company(
                company=company,
                goal=goal,
                criteria=state.requirements,
                max_sources=max(3, self.settings.min_sources_per_company),
            )
            state.research_notes.append(note)
            self._set_task_status(
                state,
                TaskType.research,
                TaskStatus.completed,
                company=company,
            )
            state.touch()
            self.store.save(state)

        state.status = "analysis"
        self._set_task_status(state, TaskType.analyze, TaskStatus.in_progress)
        state.analysis = self.analyst.analyze(
            notes=state.research_notes,
            criteria=["cost", "scalability", "technology"],
        )
        self._set_task_status(state, TaskType.analyze, TaskStatus.completed)
        state.touch()
        self.store.save(state)

        state.status = "review"
        revision_focus: str | None = None
        attempts = max(1, self.settings.max_review_loops + 1)
        for _ in range(attempts):
            self._set_task_status(state, TaskType.write, TaskStatus.in_progress)
            draft = self.writer.write_report(state, revision_focus=revision_focus)
            state.drafts.append(draft)
            self._set_task_status(state, TaskType.write, TaskStatus.completed)

            self._set_task_status(state, TaskType.review, TaskStatus.in_progress)
            review = self.reviewer.review(
                state=state,
                draft=draft,
                min_sources_per_company=self.settings.min_sources_per_company,
            )
            state.review_notes.append(review)
            self._set_task_status(state, TaskType.review, TaskStatus.completed)
            state.touch()
            self.store.save(state)

            if review.passed:
                state.status = "complete"
                self._set_task_status(state, TaskType.finalize, TaskStatus.in_progress)
                state.final_output = draft
                self._set_task_status(state, TaskType.finalize, TaskStatus.completed)
                state.touch()
                self.store.save(state)
                return state

            revision_focus = "; ".join(review.issues)

        state.status = "needs_human_review"
        self._set_task_status(state, TaskType.finalize, TaskStatus.failed)
        state.final_output = state.drafts[-1] if state.drafts else ""
        state.touch()
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
