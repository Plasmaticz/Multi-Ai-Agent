from __future__ import annotations

from dataclasses import dataclass
from difflib import unified_diff
from pathlib import Path


@dataclass
class FileChangeResult:
    file_path: str
    status: str
    message: str


class WorkspaceTool:
    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path.resolve()

    def resolve_path(self, file_path: str) -> Path | None:
        candidate = (self.workspace_path / file_path).resolve()
        try:
            candidate.relative_to(self.workspace_path)
        except ValueError:
            return None
        return candidate

    def is_binary_path(self, path: Path) -> bool:
        try:
            data = path.read_bytes()
        except OSError:
            return False
        return b"\x00" in data

    def read_text(self, file_path: str) -> str:
        path = self.resolve_path(file_path)
        if path is None or not path.exists() or not path.is_file() or self.is_binary_path(path):
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return ""

    def write_text(self, file_path: str, content: str) -> FileChangeResult:
        path = self.resolve_path(file_path)
        if path is None:
            return FileChangeResult(file_path=file_path, status="rejected", message="Path escapes the workspace root.")

        if path.exists() and path.is_file() and self.is_binary_path(path):
            return FileChangeResult(file_path=file_path, status="rejected", message="Binary files are not supported.")

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return FileChangeResult(file_path=file_path, status="applied", message="File written successfully.")
        except OSError as exc:
            return FileChangeResult(file_path=file_path, status="rejected", message=str(exc))

    def build_unified_diff(self, file_path: str, proposed_content: str) -> str:
        current_content = self.read_text(file_path)
        before = current_content.splitlines(keepends=True)
        after = proposed_content.splitlines(keepends=True)
        diff = unified_diff(
            before,
            after,
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        )
        return "".join(diff)
