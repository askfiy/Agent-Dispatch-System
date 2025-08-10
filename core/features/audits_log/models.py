import uuid
import json
import datetime
from typing import Any

from core.shared.base.models import BaseModel

from ..tasks.scheme import Tasks


class AuditInCrudModel(BaseModel):
    session_id: uuid.UUID
    message: str
    created_at: datetime.datetime


class AuditCreateModel(BaseModel):
    session_id: uuid.UUID
    message: str


class AuditCreateTaskLogModel(BaseModel):
    session_id: uuid.UUID
    thinking: str
    task: Tasks | None = None

    def to_audit_log(self) -> AuditCreateModel:
        task_dict = self.task.to_dict() if self.task else {}

        return AuditCreateModel(
            session_id=self.session_id,
            message=json.dumps(
                {"thinking": self.thinking, "task": task_dict}, ensure_ascii=False
            ),
        )
