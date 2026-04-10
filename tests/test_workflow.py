import time

from app.config import Settings
from app.schemas.state import CodeChange, RepoFinding, WorkerArtifact
from app.workflows.run_crew import CrewRunner


def test_workflow_produces_coding_plan(tmp_path):
    workspace = _create_workspace(tmp_path)
    settings = Settings(
        app_name="Multi-Agent Coding Copilot",
        openai_api_key="",
        workspace_dir=str(workspace),
        max_review_loops=1,
        max_concurrent_research=3,
    )
    runner = CrewRunner(settings=settings)

    state = runner.run(goal="Add JWT auth to the FastAPI app and write tests")

    assert state.status == "complete"
    assert state.deliverable_type == "implementation"
    assert len(state.repo_findings) >= 1
    assert len(state.implementation_plan) >= 2
    assert len(state.worker_outputs) == len(state.implementation_plan)
    assert len(state.review_notes) >= 1
    assert state.validation_commands
    assert state.metadata.get("llm_enabled") is False
    assert state.metadata.get("implementation_concurrency") == 3
    assert state.metadata.get("thread_count") == 3
    execution_metrics = state.metadata.get("execution_metrics", {})
    assert execution_metrics.get("configured_thread_count") == 3
    assert execution_metrics.get("active_worker_threads", 0) >= 1
    assert execution_metrics.get("total_run_time_ms", 0) >= 0
    assert execution_metrics.get("parallel_worker_wall_time_ms", 0) >= 0
    assert execution_metrics.get("estimated_sequential_worker_time_ms", 0) >= 0
    assert isinstance(execution_metrics.get("worker_runtimes_ms"), dict)
    assert all(task["status"] in {"completed", "failed"} for task in state.tasks)

    output = state.final_output.lower()
    assert "requested change" in output
    assert "proposed file changes" in output
    assert "validation commands" in output
    assert "execution metrics" in output


def test_parallel_code_workers_run_concurrently(tmp_path):
    workspace = _create_workspace(tmp_path)
    settings = Settings(
        app_name="Multi-Agent Coding Copilot",
        openai_api_key="",
        workspace_dir=str(workspace),
        max_review_loops=0,
        max_concurrent_research=3,
    )
    runner = CrewRunner(settings=settings)

    def fake_explore(goal: str, run_context=None, limit: int = 10):
        return [
            RepoFinding(file_path="app/api/routes.py", line_number=1, summary="Backend route", excerpt="JWT auth route", score=5.0),
            RepoFinding(file_path="static/js/app.js", line_number=1, summary="Frontend login UI", excerpt="login button", score=4.0),
            RepoFinding(file_path="tests/test_auth.py", line_number=1, summary="Token tests", excerpt="invalid token", score=4.0),
        ]

    def slow_implement(goal, work_item, findings, run_context=None, revision_focus=None):
        time.sleep(0.25)
        return WorkerArtifact(
            work_item_id=work_item.work_item_id,
            owner=work_item.owner,
            summary=f"Synthetic artifact for {work_item.title}",
            files_touched=work_item.write_scope[:1],
            code_changes=[
                CodeChange(
                    file_path=work_item.write_scope[0],
                    change_type="modify",
                    summary="Synthetic change",
                    proposal="Implement the requested coding update.",
                )
            ],
            tests_to_run=["pytest -q"],
            risks=[],
            confidence=0.8,
        )

    runner.repo_explorer.explore = fake_explore
    runner.code_worker.implement = slow_implement

    started_at = time.perf_counter()
    state = runner.run(goal="Add auth UI, backend JWT handling, and tests")
    elapsed = time.perf_counter() - started_at

    assert state.status == "complete"
    assert len(state.worker_outputs) == 3
    assert elapsed < 0.65
    execution_metrics = state.metadata.get("execution_metrics", {})
    assert execution_metrics.get("configured_thread_count") == 3
    assert execution_metrics.get("active_worker_threads") == 3
    assert execution_metrics.get("parallel_speedup", 1.0) > 1.0
    assert len(execution_metrics.get("worker_runtimes_ms", {})) == 3


def _create_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "app/api").mkdir(parents=True, exist_ok=True)
    (workspace / "static/js").mkdir(parents=True, exist_ok=True)
    (workspace / "tests").mkdir(parents=True, exist_ok=True)

    (workspace / "app/api/routes.py").write_text(
        "from fastapi import APIRouter\n\nrouter = APIRouter()\n\n# TODO: add JWT auth route and token validation\n",
        encoding="utf-8",
    )
    (workspace / "static/js/app.js").write_text(
        "const loginButton = document.getElementById('login-button');\n// TODO: wire login flow\n",
        encoding="utf-8",
    )
    (workspace / "tests/test_auth.py").write_text(
        "def test_invalid_token_rejected():\n    assert True\n",
        encoding="utf-8",
    )
    return workspace
