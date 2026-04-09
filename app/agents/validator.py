from __future__ import annotations

import json

from app.agents.base import BaseAgent
from app.schemas.state import WorkerArtifact


class ValidatorAgent(BaseAgent):
    def __init__(self):
        super().__init__(name="validator", role="validation_planner")

    def build_validation_commands(self, worker_outputs: list[WorkerArtifact]) -> list[str]:
        commands: list[str] = []
        for artifact in worker_outputs:
            commands.extend(artifact.tests_to_run)

        deduped = list(dict.fromkeys(commands))
        if not deduped:
            deduped = ["pytest -q", "python3 -m py_compile app"]
        return deduped

    def summarize_validation(self, commands: list[str]) -> str:
        return json.dumps(commands)
