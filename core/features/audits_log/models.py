import uuid
import json
import datetime
import logging

from core.shared.base.models import BaseModel

logger = logging.getLogger("Audits")


class AuditInCrudModel(BaseModel):
    session_id: uuid.UUID
    message: str
    created_at: datetime.datetime


class AuditCreateModel(BaseModel):
    session_id: uuid.UUID
    message: str


class AuditLLMlogModel(BaseModel):
    session_id: uuid.UUID
    thinking: str
    message: str
    tokens: dict[str, int]

    def to_audit_log(self) -> AuditCreateModel:
        audit_log = {
            "message": self.message,
            "thinking": self.thinking,
            "tokens": self.tokens,
        }

        logger.info(audit_log)
        return AuditCreateModel(
            session_id=self.session_id,
            message=json.dumps(audit_log, ensure_ascii=False),
        )
