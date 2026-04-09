from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    pending = "pending"
    in_progress = "in_progress"
    completed = "completed"
    failed = "failed"


class TaskType(str, Enum):
    plan = "plan"
    explore = "explore"
    architect = "architect"
    implement = "implement"
    review = "review"
    fix = "fix"
    validate = "validate"
    finalize = "finalize"


class AgentTask(BaseModel):
    task_id: str
    task_type: TaskType
    assigned_to: str
    instructions: str
    status: TaskStatus = TaskStatus.pending
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: dict[str, Any] = Field(default_factory=dict)
