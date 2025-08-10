import uuid

from core.shared.models.http import Paginator
from core.shared.database.session import (
    AsyncSession,
    AsyncTxSession,
)
from core.shared.exceptions import ServiceNotFoundException
from .scheme import AuditsLog
from .models import AuditCreateModel
from .repository import AuditsLogRepository


async def get_or_404(repo: AuditsLogRepository, pk: int):
    db_obj = await repo.get(pk=pk)
    if not db_obj:
        raise ServiceNotFoundException(f"审计记录: {pk} 不存在")

    return db_obj


async def create(create_model: AuditCreateModel, session: AsyncTxSession) -> AuditsLog:
    repo = AuditsLogRepository(session=session)
    db_obj = await repo.create(create_model)
    return db_obj


async def upget_paginator(
    session_id: uuid.UUID, paginator: Paginator, session: AsyncSession
) -> Paginator:
    repo = AuditsLogRepository(session=session)
    return await repo.upget_paginator(session_id=session_id, paginator=paginator)
