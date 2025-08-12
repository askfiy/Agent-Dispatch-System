import uuid
from collections.abc import Sequence

import sqlalchemy as sa

from core.shared.enums import TaskUnitState
from core.shared.models.http import Paginator
from core.shared.base.repository import BaseCRUDRepository
from .scheme import TasksUnit


class TasksUnitRepository(BaseCRUDRepository[TasksUnit]):
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

    async def get_round_units_id(self, round_id: uuid.UUID) -> Sequence[int]:
        stmt = sa.select(self.model.id).where(
            self.model.round_id == round_id,
            self.model.state.not_in([TaskUnitState.COMPLETE, TaskUnitState.CANCELLED]),
            sa.not_(self.model.is_deleted),
        )
        result = await self.session.execute(stmt)
        return result.scalars().unique().all()

    async def get_round_units(self, round_id: uuid.UUID) -> Sequence[TasksUnit]:
        stmt = sa.select(self.model).where(
            self.model.round_id == round_id,
            self.model.state == TaskUnitState.COMPLETE,
            sa.not_(self.model.is_deleted),
        )
        result = await self.session.execute(stmt)
        return result.scalars().unique().all()

    async def clear_round_units(self, round_id: uuid.UUID):
        await self.session.execute(
            sa.update(self.model)
            .where(
                self.model.round_id == round_id,
                sa.not_(self.model.is_deleted),
                self.model.state.not_in(
                    [TaskUnitState.COMPLETE, TaskUnitState.CANCELLED]
                ),
            )
            .values(state=TaskUnitState.CANCELLED)
        )

    async def get_by_task(self, task_id: int) -> Sequence[TasksUnit]:
        stmt = sa.select(self.model).where(
            self.model.task_id == task_id,
            self.model.state == TaskUnitState.COMPLETE,
            sa.not_(self.model.is_deleted),
        )

        result = await self.session.execute(stmt)
        return result.scalars().unique().all()
