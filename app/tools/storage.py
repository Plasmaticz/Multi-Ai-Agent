from __future__ import annotations

from threading import Lock
from typing import Callable

from app.schemas.state import ProjectState


class ProjectStore:
    def __init__(self):
        self._items: dict[str, ProjectState] = {}
        self._lock = Lock()

    def save(self, state: ProjectState) -> ProjectState:
        with self._lock:
            stored = state.model_copy(deep=True)
            self._items[state.request_id] = stored
            return stored

    def get(self, request_id: str) -> ProjectState | None:
        with self._lock:
            state = self._items.get(request_id)
            return state.model_copy(deep=True) if state else None

    def update(self, request_id: str, updater: Callable[[ProjectState], None]) -> ProjectState | None:
        with self._lock:
            current = self._items.get(request_id)
            if current is None:
                return None

            next_state = current.model_copy(deep=True)
            updater(next_state)
            next_state.touch()
            self._items[request_id] = next_state
            return next_state.model_copy(deep=True)
