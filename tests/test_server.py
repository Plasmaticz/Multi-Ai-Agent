import os

from fastapi import FastAPI

from app import server


def test_server_main_passes_runtime_arguments(monkeypatch):
    captured = {}

    def fake_run(app_target, factory, host, port, reload):
        captured["app_target"] = app_target
        captured["factory"] = factory
        captured["host"] = host
        captured["port"] = port
        captured["reload"] = reload

    monkeypatch.setattr(server.uvicorn, "run", fake_run)
    monkeypatch.setattr(
        "sys.argv",
        [
            "app.server",
            "--host",
            "127.0.0.1",
            "--port",
            "9090",
            "--app-data-dir",
            "/tmp/desktop-app",
            "--environment",
            "desktop",
        ],
    )

    server.main()

    assert captured["host"] == "127.0.0.1"
    assert captured["port"] == 9090
    assert captured["reload"] is False
    assert captured["factory"] is False
    assert isinstance(captured["app_target"], FastAPI)
    assert os.environ["APP_DATA_DIR"] == "/tmp/desktop-app"
    assert os.environ["ENVIRONMENT"] == "desktop"
