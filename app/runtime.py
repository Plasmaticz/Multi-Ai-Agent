from __future__ import annotations

import sys
from pathlib import Path


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS"))
    return Path(__file__).resolve().parent.parent


def templates_dir() -> Path:
    return project_root() / "templates"


def static_dir() -> Path:
    return project_root() / "static"
