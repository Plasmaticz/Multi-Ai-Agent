from __future__ import annotations

from uuid import uuid4

from app.agents.base import BaseAgent
from app.schemas.state import ProjectState, RepoFinding, WorkItem
from app.schemas.tasks import AgentTask, TaskType


class OrchestratorAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="orchestrator", role="coding_coordinator")

    def initialize_project(
        self,
        goal: str,
        request_id: str | None = None,
    ) -> ProjectState:
        requirements = self._infer_requirements(goal)
        return ProjectState(
            request_id=request_id or str(uuid4()),
            user_goal=goal,
            deliverable_type="implementation",
            requirements=requirements,
            status="planning",
        )

    def build_work_items(self, request_id: str, goal: str, findings: list[RepoFinding]) -> list[WorkItem]:
        buckets = self._bucket_findings(goal, findings)
        required_owners = self._required_owners(goal)
        work_items: list[WorkItem] = []

        for owner, title in [
            ("repo_worker_backend", "Backend and application logic"),
            ("repo_worker_frontend", "Frontend and interaction updates"),
            ("repo_worker_tests", "Tests and validation coverage"),
        ]:
            scoped_findings = buckets[owner]
            if not scoped_findings and owner not in required_owners and owner != "repo_worker_backend":
                continue

            write_scope = []
            for finding in scoped_findings[:4]:
                if finding.file_path not in write_scope:
                    write_scope.append(finding.file_path)

            if not write_scope:
                write_scope = self._default_scope_for_owner(owner)

            work_items.append(
                WorkItem(
                    work_item_id=f"{request_id}-{owner}",
                    title=title,
                    owner=owner,
                    write_scope=write_scope,
                    rationale=self._rationale_for_owner(owner, goal, scoped_findings),
                    acceptance_criteria=self._criteria_for_owner(owner, goal),
                )
            )

        return work_items

    def plan(self, request_id: str, work_items: list[WorkItem]) -> list[AgentTask]:
        tasks: list[AgentTask] = [
            AgentTask(
                task_id=f"{request_id}-plan",
                task_type=TaskType.plan,
                assigned_to="orchestrator",
                instructions="Plan the coding workflow and scope the work items.",
            ),
            AgentTask(
                task_id=f"{request_id}-explore",
                task_type=TaskType.explore,
                assigned_to="repo_explorer",
                instructions="Scan the repository for relevant files, symbols, and context.",
            ),
            AgentTask(
                task_id=f"{request_id}-architect",
                task_type=TaskType.architect,
                assigned_to="architect",
                instructions="Convert repository findings into disjoint coding work items.",
            ),
        ]

        for work_item in work_items:
            tasks.append(
                AgentTask(
                    task_id=f"{request_id}-{work_item.owner}",
                    task_type=TaskType.implement,
                    assigned_to=work_item.owner,
                    instructions=f"Implement the work item for {work_item.title}.",
                    input_data={
                        "work_item_id": work_item.work_item_id,
                        "write_scope": work_item.write_scope,
                    },
                )
            )

        tasks.extend(
            [
                AgentTask(
                    task_id=f"{request_id}-review",
                    task_type=TaskType.review,
                    assigned_to="reviewer",
                    instructions="Review worker outputs for bugs, regressions, and missing coverage.",
                ),
                AgentTask(
                    task_id=f"{request_id}-fix",
                    task_type=TaskType.fix,
                    assigned_to="fixer",
                    instructions="Revise worker outputs to address reviewer findings if needed.",
                ),
                AgentTask(
                    task_id=f"{request_id}-validate",
                    task_type=TaskType.validate,
                    assigned_to="validator",
                    instructions="Produce validation commands and verification notes.",
                ),
                AgentTask(
                    task_id=f"{request_id}-finalize",
                    task_type=TaskType.finalize,
                    assigned_to="orchestrator",
                    instructions="Return the final coding response with proposed file changes and risks.",
                ),
            ]
        )

        return tasks

    def _infer_requirements(self, goal: str) -> list[str]:
        goal_lower = goal.lower()
        requirements = ["implementation", "review"]
        if any(term in goal_lower for term in ["test", "pytest", "unit test", "coverage"]):
            requirements.append("tests")
        if any(term in goal_lower for term in ["ui", "frontend", "react", "css", "button", "modal"]):
            requirements.append("frontend")
        if any(term in goal_lower for term in ["api", "backend", "server", "fastapi", "database", "auth"]):
            requirements.append("backend")
        requirements.append("validation")
        return list(dict.fromkeys(requirements))

    def _bucket_findings(self, goal: str, findings: list[RepoFinding]) -> dict[str, list[RepoFinding]]:
        buckets = {
            "repo_worker_backend": [],
            "repo_worker_frontend": [],
            "repo_worker_tests": [],
        }
        goal_lower = goal.lower()

        for finding in findings:
            path = finding.file_path.lower()
            if path.startswith("tests/") or "test" in path:
                buckets["repo_worker_tests"].append(finding)
            elif path.startswith("static/") or path.startswith("templates/") or path.endswith(('.css', '.js', '.html', '.tsx', '.jsx')):
                buckets["repo_worker_frontend"].append(finding)
            else:
                buckets["repo_worker_backend"].append(finding)

        return buckets

    def _required_owners(self, goal: str) -> set[str]:
        goal_lower = goal.lower()
        owners = {"repo_worker_backend"}
        if any(term in goal_lower for term in ["frontend", "ui", "modal", "button", "layout"]):
            owners.add("repo_worker_frontend")
        if any(term in goal_lower for term in ["test", "pytest", "coverage"]):
            owners.add("repo_worker_tests")
        return owners

    def _criteria_for_owner(self, owner: str, goal: str) -> list[str]:
        criteria = ["Proposed changes are grounded in existing repository structure."]
        if owner == "repo_worker_backend":
            criteria.extend(
                [
                    "Backend logic or server-side files to modify are identified.",
                    "Edge cases and integration risks are addressed.",
                ]
            )
        elif owner == "repo_worker_frontend":
            criteria.extend(
                [
                    "UI or interaction changes are scoped to concrete files.",
                    "User-facing behavior changes are explained clearly.",
                ]
            )
        elif owner == "repo_worker_tests":
            criteria.extend(
                [
                    "Test coverage gaps are identified.",
                    "Validation commands or test files are proposed.",
                ]
            )
        if "auth" in goal.lower():
            criteria.append("Authentication and failure states are covered.")
        return criteria

    def _default_scope_for_owner(self, owner: str) -> list[str]:
        defaults = {
            "repo_worker_backend": ["app/main.py", "app/api/routes.py", "app/workflows/run_crew.py"],
            "repo_worker_frontend": ["templates/index.html", "static/js/app.js", "static/css/app.css"],
            "repo_worker_tests": ["tests/test_api.py", "tests/test_workflow.py"],
        }
        return defaults.get(owner, [])

    def _rationale_for_owner(self, owner: str, goal: str, findings: list[RepoFinding]) -> str:
        if findings:
            return (
                f"This workstream is relevant to '{goal}' because the repository findings point to "
                f"{', '.join(finding.file_path for finding in findings[:3])}."
            )
        if owner == "repo_worker_tests":
            return "Validation should cover the requested change even if test files were not matched directly."
        if owner == "repo_worker_frontend":
            return "User-facing changes often require frontend or template updates."
        return "Core application logic typically flows through backend routes, workflow code, and configuration."
