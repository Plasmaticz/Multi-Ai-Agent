from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class RepoMatch:
    file_path: str
    line_number: int | None
    excerpt: str
    score: int


class RepoSearchTool:
    def __init__(self, workspace_path: Path):
        self.workspace_path = workspace_path

    def search(self, query: str, limit: int = 12) -> list[RepoMatch]:
        terms = self._extract_terms(query)
        if not terms:
            return []

        matches: dict[tuple[str, int | None], RepoMatch] = {}
        for rank, term in enumerate(terms, start=1):
            for found in self._run_rg(term, per_term_limit=max(4, limit)):
                key = (found.file_path, found.line_number)
                score = max(1, len(terms) - rank + 1)
                existing = matches.get(key)
                if existing is None:
                    matches[key] = RepoMatch(
                        file_path=found.file_path,
                        line_number=found.line_number,
                        excerpt=found.excerpt,
                        score=score,
                    )
                else:
                    existing.score += score

        ordered = sorted(
            matches.values(),
            key=lambda item: (item.score, -len(item.file_path)),
            reverse=True,
        )
        return ordered[:limit]

    def read_file_excerpt(self, file_path: str, line_number: int | None = None, context: int = 6) -> str:
        path = (self.workspace_path / file_path).resolve()
        if not path.exists() or not path.is_file():
            return ""

        try:
            lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
        except OSError:
            return ""

        if not lines:
            return ""

        if line_number is None:
            selected = lines[: min(len(lines), context * 2)]
            return "\n".join(selected[: context * 2])

        start = max(0, line_number - context - 1)
        end = min(len(lines), line_number + context)
        return "\n".join(lines[start:end])

    def _run_rg(self, term: str, per_term_limit: int) -> list[RepoMatch]:
        rg_path = shutil.which("rg")
        if rg_path is None:
            return self._fallback_scan(term, per_term_limit)

        command = [
            rg_path,
            "-n",
            "-i",
            "--hidden",
            "--glob",
            "!.git",
            "--glob",
            "!node_modules",
            "--glob",
            "!.venv",
            "--glob",
            "!dist",
            "--glob",
            "!__pycache__",
            "--max-count",
            str(per_term_limit),
            term,
            str(self.workspace_path),
        ]
        try:
            completed = subprocess.run(
                command,
                check=False,
                capture_output=True,
                text=True,
            )
        except OSError:
            return []

        if completed.returncode not in (0, 1):
            return []

        results: list[RepoMatch] = []
        for line in completed.stdout.splitlines():
            parts = line.split(":", 2)
            if len(parts) != 3:
                continue
            abs_path, line_no, excerpt = parts
            try:
                relative = str(Path(abs_path).resolve().relative_to(self.workspace_path))
            except ValueError:
                relative = abs_path
            try:
                parsed_line = int(line_no)
            except ValueError:
                parsed_line = None
            results.append(
                RepoMatch(
                    file_path=relative,
                    line_number=parsed_line,
                    excerpt=excerpt.strip(),
                    score=1,
                )
            )
        return results

    def _fallback_scan(self, term: str, limit: int) -> list[RepoMatch]:
        pattern = re.compile(re.escape(term), re.IGNORECASE)
        results: list[RepoMatch] = []
        for path in self.workspace_path.rglob("*"):
            if len(results) >= limit:
                break
            if not path.is_file() or self._is_ignored(path):
                continue
            try:
                content = path.read_text(encoding="utf-8", errors="ignore").splitlines()
            except OSError:
                continue
            for idx, line in enumerate(content, start=1):
                if pattern.search(line):
                    results.append(
                        RepoMatch(
                            file_path=str(path.relative_to(self.workspace_path)),
                            line_number=idx,
                            excerpt=line.strip(),
                            score=1,
                        )
                    )
                    break
        return results

    def _extract_terms(self, query: str) -> list[str]:
        tokens = [token.lower() for token in re.findall(r"[A-Za-z_][A-Za-z0-9_/-]+", query)]
        stopwords = {
            "the",
            "and",
            "for",
            "with",
            "from",
            "that",
            "this",
            "into",
            "then",
            "them",
            "code",
            "agent",
            "need",
            "make",
            "build",
            "should",
            "would",
            "about",
            "have",
            "more",
            "towards",
        }
        filtered = [token for token in tokens if len(token) > 2 and token not in stopwords]
        deduped: list[str] = []
        for token in filtered:
            if token not in deduped:
                deduped.append(token)
        return deduped[:6]

    def _is_ignored(self, path: Path) -> bool:
        ignored_parts = {".git", "node_modules", ".venv", "dist", "__pycache__"}
        return any(part in ignored_parts for part in path.parts)


import shutil
