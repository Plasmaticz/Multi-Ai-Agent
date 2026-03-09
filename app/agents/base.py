from __future__ import annotations


class BaseAgent:
    def __init__(self, name: str, role: str):
        self.name = name
        self.role = role
