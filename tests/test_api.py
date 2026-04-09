import time

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.schemas.state import ProjectState


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

    initial = client.get("/api/settings")
    assert initial.status_code == 200
    assert initial.json()["has_api_key"] is False

    saved = client.post(
        "/api/settings",
        json={
            "openai_api_key": "sk-test-123456789",
            "openai_model": "gpt-4.1-mini",
            "max_concurrent_research": 3,
        },
    )
    assert saved.status_code == 200
    assert saved.json()["has_api_key"] is True
    assert saved.json()["max_concurrent_research"] == 3

    loaded = client.get("/api/settings")
    assert loaded.status_code == 200
    assert loaded.json()["has_api_key"] is True
    assert loaded.json()["openai_model"] == "gpt-4.1-mini"


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
    app = create_app(
        Settings(
            app_name="Multi-Agent Coding Copilot",
            app_data_dir=str(tmp_path / "app-data"),
            workspace_dir=str(tmp_path / "workspace"),
            openai_api_key="",
            environment=environment,
        )
    )
    return TestClient(app)
