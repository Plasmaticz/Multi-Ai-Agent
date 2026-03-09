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
    research = "research"
    analyze = "analyze"
    write = "write"
    review = "review"
    finalize = "finalize"


class AgentTask(BaseModel):
    task_id: str
    task_type: TaskType
    assigned_to: str
    instructions: str
    status: TaskStatus = TaskStatus.pending
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_data: dict[str, Any] = Field(default_factory=dict)
