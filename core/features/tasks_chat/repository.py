import sqlalchemy as sa

from core.shared.enums import MessageRole
from core.shared.models.http import Paginator
from core.shared.base.repository import BaseCRUDRepository
from .scheme import TasksChat


class TasksChatRepository(BaseCRUDRepository[TasksChat]):
    async def upget_paginator(
        self,
        task_id: int,
        paginator: Paginator,
    ) -> Paginator:
        query_stmt = sa.select(self.model).where(
            self.model.task_id == task_id, sa.not_(self.model.is_deleted)
        )

        return await super().upget_paginator_by_stmt(
            paginator=paginator,
            stmt=query_stmt,
        )

    async def get_last_messages(
        self, task_id: int, role: MessageRole
    ) -> TasksChat | None:
        stmt = (
            sa.select(self.model)
            .where(
                self.model.task_id == task_id,
                self.model.role == role,
                sa.not_(self.model.is_deleted),
            )
            .order_by(self.model.created_at.desc())
            .limit(1)
        )

        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()
