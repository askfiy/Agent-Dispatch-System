import uuid
import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from core.shared.base.scheme import BaseTableScheme


class AuditsLog(BaseTableScheme):
    __tablename__ = "audits_log"
    __table_args__ = (
        sa.Index("idx_audit_session_id_created_at", "session_id", "created_at"),
        {"comment": "会话审计表"},
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        sa.CHAR(36),
        nullable=False,
        index=True,
        comment="会话 ID",
    )

    message: Mapped[str] = mapped_column(
        sa.Text,
        nullable=False,
        comment="审计信息. 发生了什么.",
    )
