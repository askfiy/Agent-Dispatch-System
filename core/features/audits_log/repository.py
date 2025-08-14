import uuid
import sqlalchemy as sa

from core.shared.models.http import Paginator
from core.shared.base.repository import BaseCRUDRepository
from .scheme import AuditsLog


class AuditsLogRepository(BaseCRUDRepository[AuditsLog]):
    async def upget_paginator(
        self,
        session_id: str,
        paginator: Paginator,
    ) -> Paginator:
        query_stmt = sa.select(self.model).where(
            self.model.session_id == session_id
        )

        return await super().upget_paginator_by_stmt(
            paginator=paginator,
            stmt=query_stmt,
        )
