import datetime

from core.shared.enums import TaskState
from core.shared.base.models import BaseModel


class TaskHistoryInCrudModel(BaseModel):
    state: TaskState
    process: str
    thinking: str
    created_at: datetime.datetime


class TaskHistoryCreateModel(BaseModel):
    task_id: int
    state: TaskState
    process: str
    thinking: str
