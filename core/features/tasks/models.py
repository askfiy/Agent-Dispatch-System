import uuid
import datetime
from typing import Any

from pydantic import field_serializer

from core.shared.enums import TaskState
from core.shared.base.models import BaseModel

from ..tasks_chat.models import TaskChatInCrudModel
from ..tasks_history.models import TaskHistoryInCrudModel
from ..tasks_workspace.models import TaskWorkspaceInCrudModel


class TaskInXyzModel(BaseModel):
    session_id: str

    id: int
    name: str
    state: TaskState

    expect_execute_time: datetime.datetime
    lasted_execute_time: datetime.datetime | None = None

    original_user_input: str
    created_at: datetime.datetime

    workspace: TaskWorkspaceInCrudModel


class TaskInCrudModel(BaseModel):
    session_id: str

    id: int
    name: str
    state: TaskState
    priority: int
    workspace_id: int

    expect_execute_time: datetime.datetime
    owner: str
    keywords: str
    original_user_input: str

    chats: list[TaskChatInCrudModel]
    histories: list[TaskHistoryInCrudModel]

    lasted_execute_time: datetime.datetime | None = None
    created_at: datetime.datetime

    @field_serializer("keywords")
    def _validator_keywords(self, keywords: str) -> list[str]:
        return keywords.split(",")


class TaskCreateModel(BaseModel):
    session_id: str
    name: str
    workspace_id: int
    expect_execute_time: datetime.datetime
    owner: str
    owner_timezone: str
    keywords: list[str]
    original_user_input: str
    mcp_server_infos: dict[str, Any]

    @field_serializer("keywords")
    def _validator_keywords(self, keywords: list[str]) -> str:
        return ",".join(keywords)


class TaskUpdateModel(BaseModel):
    name: str | None = None
    state: TaskState | None = None
    priority: int | None = None

    prev_round_id: uuid.UUID | None = None
    curr_round_id: uuid.UUID | None = None

    expect_execute_time: datetime.datetime | None = None
    lasted_execute_time: datetime.datetime | None = None

    keywords: list[str] | None = None
    original_user_input: str | None = None

    @field_serializer("keywords")
    def _validator_keywords(self, keywords: list[str] | None) -> str | None:
        if keywords:
            return ",".join(keywords)
        return None
