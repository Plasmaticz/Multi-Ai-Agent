import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas.state import CodeChange, ProjectState, ReviewNote, ValidationResult, WorkItem, WorkerArtifact
from app.tools.repo_tools import RepoSearchTool


def test_health_check(tmp_path):
    client = _build_client(tmp_path)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_index_shell_renders(tmp_path):
    client = _build_client(tmp_path)
    response = client.get("/")
    assert response.status_code == 200
    assert "Multi-Agent Coding Copilot" in response.text
    assert "New Thread" in response.text


def test_settings_persist_locally(tmp_path):
    client = _build_client(tmp_path)
    workspace = tmp_path / "selected-workspace"
    workspace.mkdir()

    initial = client.get("/api/settings")
    assert initial.status_code == 200
    assert initial.json()["has_api_key"] is False

    saved = client.post(
        "/api/settings",
        json={
            "openai_api_key": "sk-test-123456789",
            "openai_model": "gpt-4.1-mini",
            "workspace_dir": str(workspace),
            "max_concurrent_research": 3,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["has_api_key"] is True
    assert saved.json()["max_concurrent_research"] == 3
    assert Path(saved.json()["workspace_dir"]) == workspace.resolve()

    loaded = client.get("/api/settings")
    assert loaded.status_code == 200
    assert loaded.json()["has_api_key"] is True
    assert loaded.json()["openai_model"] == "gpt-4.1-mini"
    assert Path(loaded.json()["workspace_dir"]) == workspace.resolve()


def test_settings_reject_invalid_workspace_folder(tmp_path):
    client = _build_client(tmp_path)

    saved = client.post(
        "/api/settings",
        json={
            "workspace_dir": str(tmp_path / "missing-workspace"),
        },
    )
    assert saved.status_code == 400
    assert saved.json()["detail"] == "Workspace folder does not exist."


def test_metadata_endpoint_uses_runtime_settings(tmp_path):
    client = _build_client(tmp_path, environment="desktop")
    response = client.get("/api/meta")
    assert response.status_code == 200
    payload = response.json()
    assert payload["desktop_mode"] is True
    assert payload["environment"] == "desktop"
    assert "app-data" in payload["app_data_dir"]


def test_thread_prompt_flow_polls_to_completion_and_logs(tmp_path, monkeypatch):
    client = _build_client(tmp_path)

    def fake_run(self, goal: str, companies=None, request_id=None, run_context=None):
        if self.event_callback:
            self.event_callback(
                {
                    "agent_name": "repo_explorer",
                    "event_type": "explore",
                    "status": "completed",
                    "message": "Repository scan finished.",
                }
            )
            self.event_callback(
                {
                    "agent_name": "repo_worker_backend",
                    "event_type": "implement_work_item",
                    "status": "completed",
                    "message": "Completed Backend and application logic.",
                }
            )
        return ProjectState(
            request_id=request_id or "test-run",
            user_goal=goal,
            status="complete",
            run_context=run_context,
            final_output="# Multi-Agent Coding Plan\n\nSynthetic coding response.",
        )

    monkeypatch.setattr("app.api.routes.CrewRunner.run", fake_run)

    thread_response = client.post("/api/threads", json={})
    assert thread_response.status_code == 200
    thread_id = thread_response.json()["thread"]["id"]

    message_response = client.post(
        f"/api/threads/{thread_id}/messages",
        json={"content": "Add JWT auth to the FastAPI app and write tests."},
    )
    assert message_response.status_code == 202
    payload = message_response.json()
    assert payload["thread"]["title"] == "JWT Auth FastAPI Tests"
    assert payload["run"] is not None

    run_payload = _wait_for_run(client, thread_id, payload["run"]["id"])
    assert run_payload["run"]["status"] == "complete"
    assert run_payload["messages"][-1]["role"] == "assistant"
    assert run_payload["messages"][-1]["content"] == "# Multi-Agent Coding Plan\n\nSynthetic coding response."

    detail = client.get(f"/api/threads/{thread_id}")
    assert detail.status_code == 200
    detail_payload = detail.json()
    assert detail_payload["thread"]["thread_summary"]
    assert len(detail_payload["events"]) >= 3


def test_thread_run_uses_summary_and_recent_turns(tmp_path, monkeypatch):
    client = _build_client(tmp_path)
    app = client.app
    store = app.state.local_store
    thread = store.create_thread("Coding Thread")
    thread_id = thread["id"]
    store.update_thread_summary(thread_id, "We already decided to add JWT auth and protect the API routes.")
    store.add_message(thread_id, "user", "Add JWT auth to the FastAPI app.")
    store.add_message(thread_id, "assistant", "We should update the auth helpers and route protections.")

    captured = {}

    def fake_run(self, goal: str, companies=None, request_id=None, run_context=None):
        captured["goal"] = goal
        captured["run_context"] = run_context.model_dump(mode="json") if run_context else None
        return ProjectState(
            request_id=request_id or "context-run",
            user_goal=goal,
            status="complete",
            run_context=run_context,
            final_output="Follow-up coding answer.",
        )

    monkeypatch.setattr("app.api.routes.CrewRunner.run", fake_run)

    response = client.post(
        f"/api/threads/{thread_id}/messages",
        json={"content": "Now add tests for invalid tokens and expired sessions."},
    )
    assert response.status_code == 202
    run_id = response.json()["run"]["id"]
    _wait_for_run(client, thread_id, run_id)

    assert captured["goal"] == "Now add tests for invalid tokens and expired sessions."
    assert captured["run_context"]["thread_summary"].startswith("We already decided")
    assert len(captured["run_context"]["recent_messages"]) == 2
    assert captured["run_context"]["current_message"] == "Now add tests for invalid tokens and expired sessions."


def test_failed_run_persists_inline_error_message(tmp_path, monkeypatch):
    client = _build_client(tmp_path)

    def fake_run(self, goal: str, companies=None, request_id=None, run_context=None):
        if self.event_callback:
            self.event_callback(
                {
                    "agent_name": "repo_worker_backend",
                    "event_type": "implement_work_item",
                    "status": "started",
                    "message": "Implementing Backend and application logic.",
                }
            )
        raise RuntimeError("boom")

    monkeypatch.setattr("app.api.routes.CrewRunner.run", fake_run)

    thread_id = client.post("/api/threads", json={}).json()["thread"]["id"]
    response = client.post(
        f"/api/threads/{thread_id}/messages",
        json={"content": "Force a coding workflow error"},
    )
    assert response.status_code == 202
    run_id = response.json()["run"]["id"]

    run_payload = _wait_for_run(client, thread_id, run_id)
    assert run_payload["run"]["status"] == "failed"
    assert run_payload["messages"][-1]["message_type"] == "error"
    assert "Run failed: boom" in run_payload["messages"][-1]["content"]
    assert any(event["status"] == "failed" for event in run_payload["events"])


def test_delete_thread_removes_it_from_database(tmp_path):
    client = _build_client(tmp_path)
    store = client.app.state.local_store

    first = client.post("/api/threads", json={"title": "Keep Me"}).json()["thread"]
    second = client.post("/api/threads", json={"title": "Delete Me"}).json()["thread"]
    store.add_message(second["id"], "user", "Add JWT auth.")
    store.add_message(second["id"], "assistant", "Planned auth changes.")

    response = client.delete(f"/api/threads/{second['id']}")
    assert response.status_code == 200
    assert response.json() == {"deleted": True, "thread_id": second["id"]}

    threads_payload = client.get("/api/threads").json()
    thread_ids = {thread["id"] for thread in threads_payload["threads"]}
    assert first["id"] in thread_ids
    assert second["id"] not in thread_ids

    missing = client.get(f"/api/threads/{second['id']}")
    assert missing.status_code == 404


def test_apply_run_writes_changes_inside_workspace(tmp_path):
    client = _build_client(tmp_path)
    store = client.app.state.local_store
    workspace = tmp_path / "workspace"
    target = workspace / "app/api/routes.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("from fastapi import APIRouter\n", encoding="utf-8")
    original = target.read_text(encoding="utf-8")

    thread = store.create_thread("Apply Thread")
    run = store.create_run(thread["id"], "Add JWT auth", "user-message-id")
    state = _build_completed_state(
        request_id=run["id"],
        goal="Add JWT auth",
        file_path="app/api/routes.py",
        proposed_content=f"{original}\n# Applied patch\n",
    )
    store.complete_run(run["id"], "complete", state.model_dump(mode="json"))

    response = client.post(f"/api/threads/{thread['id']}/runs/{run['id']}/apply")
    assert response.status_code == 200
    payload = response.json()
    assert payload["run"]["apply_status"] == "applied"
    assert "# Applied patch" in target.read_text(encoding="utf-8")
    assert any(message["run_id"] == run["id"] and "Apply Results" in message["content"] for message in payload["messages"])


def test_apply_run_blocks_failed_validation(tmp_path):
    client = _build_client(tmp_path)
    store = client.app.state.local_store
    thread = store.create_thread("Blocked Apply")
    run = store.create_run(thread["id"], "Add JWT auth", "user-message-id")
    state = _build_completed_state(
        request_id=run["id"],
        goal="Add JWT auth",
        file_path="app/api/routes.py",
        proposed_content="print('x')\n",
    )
    state.validation_results = [
        ValidationResult(command="pytest -q", status="failed", exit_code=1, stderr="boom"),
    ]
    store.complete_run(run["id"], "complete", state.model_dump(mode="json"))

    response = client.post(f"/api/threads/{thread['id']}/runs/{run['id']}/apply")
    assert response.status_code == 409
    assert response.json()["detail"] == "Validation failed for this run."


def test_export_run_report_summary_and_logs(tmp_path):
    client = _build_client(tmp_path)
    store = client.app.state.local_store
    thread = store.create_thread("Export Thread")
    run = store.create_run(thread["id"], "Add JWT auth", "user-message-id")
    state = _build_completed_state(
        request_id=run["id"],
        goal="Add JWT auth",
        file_path="app/api/routes.py",
        proposed_content="from fastapi import APIRouter\n",
    )
    store.complete_run(run["id"], "complete", state.model_dump(mode="json"))
    store.add_log(
        thread_id=thread["id"],
        run_id=run["id"],
        agent_name="system",
        event_type="run",
        status="completed",
        message="Run finished.",
    )

    report = client.get(f"/api/threads/{thread['id']}/runs/{run['id']}/export/report")
    assert report.status_code == 200
    assert "Multi-Agent Coding Plan" in report.text

    summary = client.get(f"/api/threads/{thread['id']}/runs/{run['id']}/export/summary")
    assert summary.status_code == 200
    assert "Run Summary" in summary.text
    assert "Add JWT auth" in summary.text

    logs = client.get(f"/api/threads/{thread['id']}/runs/{run['id']}/export/logs")
    assert logs.status_code == 200
    assert logs.json()[0]["message"] == "Run finished."


def test_repo_tool_blocks_reads_outside_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside_file = tmp_path / "outside.py"
    outside_file.write_text("secret = 1\n", encoding="utf-8")

    tool = RepoSearchTool(workspace.resolve())

    assert tool.read_file_excerpt("../outside.py") == ""


def _wait_for_run(client: TestClient, thread_id: str, run_id: str, timeout: float = 2.0) -> dict:
    deadline = time.time() + timeout
    last_payload = None
    while time.time() < deadline:
        response = client.get(f"/api/threads/{thread_id}/runs/{run_id}")
        assert response.status_code == 200
        last_payload = response.json()
        if last_payload["run"]["status"] != "running":
            return last_payload
        time.sleep(0.05)
    raise AssertionError(f"Run {run_id} did not finish in time. Last payload: {last_payload}")


def _build_client(tmp_path, environment: str = "dev") -> TestClient:
    workspace = tmp_path / "workspace"
    workspace.mkdir(exist_ok=True)
    app = create_app(
        Settings(
            app_name="Multi-Agent Coding Copilot",
            app_data_dir=str(tmp_path / "app-data"),
            workspace_dir=str(workspace),
            openai_api_key="",
            environment=environment,
        )
    )
    return TestClient(app)


def _build_completed_state(*, request_id: str, goal: str, file_path: str, proposed_content: str) -> ProjectState:
    return ProjectState(
        request_id=request_id,
        user_goal=goal,
        status="complete",
        implementation_plan=[
            WorkItem(
                work_item_id="backend",
                title="Backend Worker",
                owner="repo_worker_backend",
                write_scope=[file_path],
                rationale="Update the backend file.",
                acceptance_criteria=["File change is proposed."],
            )
        ],
        worker_outputs=[
            WorkerArtifact(
                work_item_id="backend",
                owner="repo_worker_backend",
                summary="Backend patch ready.",
                files_touched=[file_path],
                code_changes=[
                    CodeChange(
                        file_path=file_path,
                        change_type="modify",
                        summary="Update backend file",
                        proposed_content=proposed_content,
                        unified_diff="--- a/file\n+++ b/file\n@@\n-old\n+new\n",
                        apply_status="pending",
                    )
                ],
                tests_to_run=["pytest -q"],
                risks=[],
                confidence=0.8,
            )
        ],
        validation_commands=["pytest -q"],
        validation_results=[ValidationResult(command="pytest -q", status="passed", exit_code=0)],
        review_notes=[ReviewNote(passed=True, issues=[], confidence=0.9)],
        final_output="# Multi-Agent Coding Plan\n\nPrepared patch.",
        metadata={"apply_status": "ready"},
    )
