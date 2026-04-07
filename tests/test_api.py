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
    assert "Multi-Agent Research Copilot" in response.text
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


def test_thread_prompt_flow_and_logs(tmp_path, monkeypatch):
    client = _build_client(tmp_path)

    def fake_run(self, goal: str, companies=None, request_id=None):
        if self.event_callback:
            self.event_callback(
                {
                    "agent_name": "researcher",
                    "event_type": "research",
                    "status": "completed",
                    "message": "Synthetic research completed.",
                }
            )
        state = ProjectState(
            request_id=request_id or "test-run",
            user_goal=goal,
            status="complete",
            final_output="## Final Answer\n\nSynthetic assistant response.",
        )
        return state

    monkeypatch.setattr("app.api.routes.CrewRunner.run", fake_run)

    thread_response = client.post("/api/threads", json={})
    assert thread_response.status_code == 200
    thread_id = thread_response.json()["thread"]["id"]

    message_response = client.post(
        f"/api/threads/{thread_id}/messages",
        json={"content": "Research coral restoration startups"},
    )
    assert message_response.status_code == 200
    payload = message_response.json()
    assert payload["thread"]["title"] == "Research coral restoration startups"
    assert len(payload["messages"]) == 2
    assert payload["messages"][0]["role"] == "user"
    assert payload["messages"][1]["role"] == "assistant"

    logs_response = client.get(f"/api/logs?thread_id={thread_id}")
    assert logs_response.status_code == 200
    assert len(logs_response.json()["logs"]) >= 2


def _build_client(tmp_path) -> TestClient:
    app = create_app(
        Settings(
            app_data_dir=str(tmp_path / "app-data"),
            openai_api_key="",
        )
    )
    return TestClient(app)
