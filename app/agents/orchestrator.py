from __future__ import annotations

from uuid import uuid4

from app.agents.base import BaseAgent
from app.schemas.state import ProjectState
from app.schemas.tasks import AgentTask, TaskType


class OrchestratorAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="orchestrator", role="manager")

    def initialize_project(
        self,
        goal: str,
        companies: list[str],
        request_id: str | None = None,
    ) -> ProjectState:
        requirements = self._infer_requirements(goal)
        return ProjectState(
            request_id=request_id or str(uuid4()),
            user_goal=goal,
            requirements=requirements,
            status="planning",
            metadata={"companies": companies},
        )

    def resolve_companies(
        self,
        goal: str,
        explicit_companies: list[str] | None,
        fallback_companies: list[str],
    ) -> list[str]:
        if explicit_companies:
            return [company.strip() for company in explicit_companies if company.strip()]

        matched = [
            company
            for company in fallback_companies
            if company.lower() in goal.lower()
        ]
        if len(matched) >= 2:
            return matched

        return fallback_companies

    def plan(self, request_id: str, companies: list[str]) -> list[AgentTask]:
        tasks: list[AgentTask] = [
            AgentTask(
                task_id=f"{request_id}-plan",
                task_type=TaskType.plan,
                assigned_to="orchestrator",
                instructions="Build a centralized workflow and task map.",
            )
        ]

        for idx, company in enumerate(companies, start=1):
            tasks.append(
                AgentTask(
                    task_id=f"{request_id}-research-{idx}",
                    task_type=TaskType.research,
                    assigned_to="researcher",
                    instructions=f"Research {company} against required criteria.",
                    input_data={"company": company},
                )
            )

        tasks.extend(
            [
                AgentTask(
                    task_id=f"{request_id}-analysis",
                    task_type=TaskType.analyze,
                    assigned_to="analyst",
                    instructions="Build scored comparison from research notes.",
                ),
                AgentTask(
                    task_id=f"{request_id}-writer",
                    task_type=TaskType.write,
                    assigned_to="writer",
                    instructions="Draft report with table and recommendation.",
                ),
                AgentTask(
                    task_id=f"{request_id}-review",
                    task_type=TaskType.review,
                    assigned_to="reviewer",
                    instructions="Validate completeness, support, and formatting.",
                ),
                AgentTask(
                    task_id=f"{request_id}-finalize",
                    task_type=TaskType.finalize,
                    assigned_to="orchestrator",
                    instructions="Return final user-facing response.",
                ),
            ]
        )

        return tasks

    def _infer_requirements(self, goal: str) -> list[str]:
        goal_lower = goal.lower()
        requirements = ["citations"]

        if "compare" in goal_lower:
            requirements.append("comparison")
        if "cost" in goal_lower:
            requirements.append("cost")
        if "scalability" in goal_lower:
            requirements.append("scalability")
        if "technology" in goal_lower:
            requirements.append("technology")

        for required in ["cost", "scalability", "technology"]:
            if required not in requirements:
                requirements.append(required)

        return requirements
