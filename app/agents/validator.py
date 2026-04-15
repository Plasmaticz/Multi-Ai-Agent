from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from time import perf_counter

from app.agents.base import BaseAgent
from app.schemas.state import ValidationResult, WorkerArtifact


class ValidatorAgent(BaseAgent):
    def __init__(self, workspace_path: Path, timeout_seconds: int = 15):
        super().__init__(name="validator", role="validation_runner")
        self.workspace_path = workspace_path.resolve()
        self.timeout_seconds = timeout_seconds

    def validate(self, worker_outputs: list[WorkerArtifact]) -> tuple[list[str], list[ValidationResult]]:
        specs = self._detect_commands(worker_outputs)
        commands = [spec["label"] for spec in specs]
        results: list[ValidationResult] = []
        for spec in specs:
            if spec["status"] == "skipped":
                results.append(
                    ValidationResult(
                        command=spec["label"],
                        status="skipped",
                        reason=spec["reason"],
                    )
                )
                continue
            results.append(self._run_command(spec["argv"], spec["label"]))
        return commands, results

    def _detect_commands(self, worker_outputs: list[WorkerArtifact]) -> list[dict[str, object]]:
        specs: list[dict[str, object]] = []
        has_tests_dir = (self.workspace_path / "tests").exists()
        touched_python = any(path.endswith(".py") for artifact in worker_outputs for path in artifact.files_touched)
        package_json = self._load_package_json()
        has_package_json = package_json is not None
        package_scripts = package_json.get("scripts", {}) if isinstance(package_json, dict) else {}
        wants_ruff = self._has_any(("ruff.toml", ".ruff.toml", "pyproject.toml")) or touched_python
        wants_mypy = self._has_any(("mypy.ini", ".mypy.ini", "pyproject.toml")) or touched_python

        if has_tests_dir or any("pytest" in test for artifact in worker_outputs for test in artifact.tests_to_run):
            specs.append(self._tool_spec(["pytest", "-q"], "pytest -q", tool_name="pytest"))

        if wants_ruff:
            specs.append(self._tool_spec(["ruff", "check", "."], "ruff check .", tool_name="ruff"))

        if wants_mypy:
            specs.append(self._tool_spec(["mypy", "."], "mypy .", tool_name="mypy"))

        if has_package_json and isinstance(package_scripts, dict) and package_scripts.get("test"):
            specs.append(self._tool_spec(["npm", "test"], "npm test", tool_name="npm"))
        elif has_package_json:
            specs.append(self._skip_spec("npm test", "No npm test script found in package.json."))

        if has_package_json and isinstance(package_scripts, dict) and package_scripts.get("build"):
            specs.append(self._tool_spec(["npm", "run", "build"], "npm run build", tool_name="npm"))
        elif has_package_json:
            specs.append(self._skip_spec("npm run build", "No npm build script found in package.json."))

        deduped: list[dict[str, object]] = []
        seen = set()
        for spec in specs:
            label = spec["label"]
            if label in seen:
                continue
            seen.add(label)
            deduped.append(spec)
        return deduped

    def _run_command(self, argv: list[str], label: str) -> ValidationResult:
        started_at = perf_counter()
        try:
            completed = subprocess.run(
                argv,
                cwd=self.workspace_path,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                check=False,
            )
            duration_ms = (perf_counter() - started_at) * 1000
            return ValidationResult(
                command=label,
                status="passed" if completed.returncode == 0 else "failed",
                exit_code=completed.returncode,
                duration_ms=round(duration_ms, 2),
                stdout=(completed.stdout or "")[:4000],
                stderr=(completed.stderr or "")[:4000],
            )
        except subprocess.TimeoutExpired as exc:
            duration_ms = (perf_counter() - started_at) * 1000
            return ValidationResult(
                command=label,
                status="failed",
                exit_code=None,
                duration_ms=round(duration_ms, 2),
                stdout=(exc.stdout or "")[:4000] if exc.stdout else "",
                stderr=(exc.stderr or "")[:4000] if exc.stderr else "",
                reason=f"Timed out after {self.timeout_seconds} seconds.",
            )

    def summarize_validation(self, results: list[ValidationResult]) -> str:
        return json.dumps([result.model_dump(mode="json") for result in results])

    def _tool_spec(self, argv: list[str], label: str, *, tool_name: str) -> dict[str, object]:
        if shutil.which(tool_name) is None:
            return self._skip_spec(label, f"{tool_name} is not installed or not available on PATH.")
        return {
            "argv": argv,
            "label": label,
            "status": "ready",
            "reason": "",
        }

    def _skip_spec(self, label: str, reason: str) -> dict[str, object]:
        return {
            "argv": [],
            "label": label,
            "status": "skipped",
            "reason": reason,
        }

    def _has_any(self, file_names: tuple[str, ...]) -> bool:
        return any((self.workspace_path / name).exists() for name in file_names)

    def _load_package_json(self) -> dict | None:
        package_path = self.workspace_path / "package.json"
        if not package_path.exists():
            return None
        try:
            return json.loads(package_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
