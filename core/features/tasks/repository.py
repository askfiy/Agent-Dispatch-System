import asyncio
from datetime import timedelta
from typing import override, Any
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.orm import aliased, subqueryload, with_loader_criteria, joinedload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.orm.strategy_options import _AbstractLoad  # pyright: ignore[reportPrivateUsage]
from sqlalchemy.orm.util import LoaderCriteriaOption
from sqlalchemy.dialects.mysql import match

from core.shared.enums import TaskState
from core.shared.models.http import Paginator
from core.shared.base.repository import BaseCRUDRepository
from .models import TaskCreateModel
from .scheme import Tasks
from ..tasks_chat.scheme import TasksChat
from ..tasks_unit.scheme import TasksUnit
from ..tasks_history.scheme import TasksHistory
from ..tasks_workspace.scheme import TasksWorkspace


class TasksCrudRepository(BaseCRUDRepository[Tasks]):
    def __init__(self, session: AsyncSession):
        super().__init__(session=session)

        self.default_limit_count = 10
        self.default_joined_loads = [Tasks.chats, Tasks.histories]

    def _get_history_loader_options(
        self, limit_count: int
    ) -> list[_AbstractLoad | LoaderCriteriaOption]:
        history_alias_for_ranking = aliased(TasksHistory)
        ranked_histories_cte = (
            sa.select(
                history_alias_for_ranking.id,
                sa.func.row_number()
                .over(
                    partition_by=history_alias_for_ranking.task_id,
                    order_by=history_alias_for_ranking.created_at.desc(),
                )
                .label("rn"),
            )
            .where(history_alias_for_ranking.task_id == Tasks.id)
            .cte("ranked_histories_cte")
        )

        return [
            subqueryload(Tasks.histories),
            with_loader_criteria(
                TasksHistory,
                TasksHistory.id.in_(
                    sa.select(ranked_histories_cte.c.id).where(
                        ranked_histories_cte.c.rn <= limit_count
                    )
                ),
            ),
        ]

    def _get_chat_loader_options(
        self, limit_count: int
    ) -> list[_AbstractLoad | LoaderCriteriaOption]:
        chat_alias_for_ranking = aliased(TasksChat)
        ranked_chats_cte = (
            sa.select(
                chat_alias_for_ranking.id,
                sa.func.row_number()
                .over(
                    partition_by=chat_alias_for_ranking.task_id,
                    order_by=chat_alias_for_ranking.created_at.desc(),
                )
                .label("rn"),
            )
            .where(chat_alias_for_ranking.task_id == Tasks.id)
            .cte("ranked_chats_cte")
        )

        return [
            subqueryload(Tasks.chats),
            with_loader_criteria(
                TasksChat,
                TasksChat.id.in_(
                    sa.select(ranked_chats_cte.c.id).where(
                        ranked_chats_cte.c.rn <= limit_count
                    )
                ),
            ),
        ]

    async def workspace_has_bind(self, workspace_id: int) -> bool:
        exists_stmt = (
            sa.select(self.model.id)
            .where(
                self.model.workspace_id == workspace_id, sa.not_(self.model.is_deleted)
            )
            .exists()
        )

        stmt = sa.select(sa.literal(True)).where(exists_stmt)

        result = await self.session.execute(stmt)

        return result.scalar_one_or_none() is not None

    @override
    async def create(self, create_model: TaskCreateModel) -> Tasks:
        task = await super().create(create_model=create_model)

        # 创建 task 后需要手动 load 一下 chats 和 histories.
        await self.session.refresh(task, [Tasks.chats.key, Tasks.histories.key])
        return task

    @override
    async def get(
        self, pk: int, joined_loads: list[InstrumentedAttribute[Any]] | None = None
    ) -> Tasks | None:
        extend_joined_loads = self.default_joined_loads.copy()
        extend_joined_loads.extend(joined_loads or [])

        stmt = sa.select(self.model).where(
            self.model.id == pk, sa.not_(self.model.is_deleted)
        )

        if extend_joined_loads:
            for join_field in extend_joined_loads:
                if Tasks.chats is join_field:
                    stmt = stmt.options(
                        *self._get_chat_loader_options(self.default_limit_count)
                    )
                elif Tasks.histories is join_field:
                    stmt = stmt.options(
                        *self._get_history_loader_options(self.default_limit_count)
                    )
                else:
                    stmt = stmt.options(joinedload(join_field))

        result = await self.session.execute(stmt)

        return result.unique().scalar_one_or_none()

    async def refactor(self, db_obj: Tasks) -> Tasks:
        """只删除 Chat. Unit. History. 保留当前任务, 以及 workspace 信息."""
        task = db_obj

        soft_delete_coroutines = [
            self.session.execute(
                sa.update(table)
                .where(
                    # 注意：这里需要检查表是否有 task_id 属性，这些表都有
                    table.task_id == task.id,
                    sa.not_(table.is_deleted),
                )
                .values(is_deleted=True, deleted_at=sa.func.now())
            )
            for table in [
                TasksChat,
                TasksUnit,
                TasksHistory,
            ]
        ]

        await asyncio.gather(*soft_delete_coroutines)

        # 因为有事务装饰器的存在， 故这里所有的操作均为原子操作.
        await self.session.refresh(task)

        return task

    @override
    async def delete(self, db_obj: Tasks) -> Tasks:
        task = db_obj

        # 软删除 tasks
        # task = await super().delete(db_obj)

        soft_delete_coroutines = [
            self.session.execute(
                sa.update(table)
                .where(
                    # 注意：这里需要检查表是否有 task_id 属性，这些表都有
                    table.task_id == task.id,
                    sa.not_(table.is_deleted),
                )
                .values(is_deleted=True, deleted_at=sa.func.now())
            )
            for table in [
                TasksChat,
                TasksUnit,
                TasksHistory,
            ]
        ]

        soft_delete_coroutines.append(
            self.session.execute(
                sa.update(self.model)
                .where(self.model.id == db_obj.id)
                .values(is_deleted=True, deleted_at=sa.func.now())
            )
        )

        if db_obj.workspace_id:
            soft_delete_coroutines.insert(
                0,
                self.session.execute(
                    sa.update(TasksWorkspace)
                    .where(
                        TasksWorkspace.id == db_obj.workspace_id,
                        sa.not_(TasksWorkspace.is_deleted),
                    )
                    .values(is_deleted=True, deleted_at=sa.func.now())
                ),
            )

        await asyncio.gather(*soft_delete_coroutines)

        # 因为有事务装饰器的存在， 故这里所有的操作均为原子操作.
        await self.session.refresh(task)

        return task

    async def upget_paginator(
        self,
        paginator: Paginator,
    ) -> Paginator:
        query_stmt = sa.select(self.model).where(sa.not_(self.model.is_deleted))
        query_stmt = query_stmt.options(
            *self._get_chat_loader_options(self.default_limit_count)
        )
        query_stmt = query_stmt.options(
            *self._get_history_loader_options(self.default_limit_count)
        )

        return await super().upget_paginator_by_stmt(
            paginator=paginator,
            stmt=query_stmt,
        )

    async def get_dispatch_tasks_id(self) -> Sequence[int]:
        stmt = (
            sa.select(self.model.id)
            .where(
                sa.not_(self.model.is_deleted),
                self.model.state.in_([TaskState.INITIAL, TaskState.SCHEDULING]),
                self.model.expect_execute_time < sa.func.now(),
            )
            .order_by(
                self.model.expect_execute_time.asc(),
                self.model.priority.desc(),
                self.model.created_at.asc(),
            )
            .with_for_update(skip_locked=True)
        )

        result = await self.session.execute(stmt)

        tasks_id = result.scalars().unique().all()

        await self.session.execute(
            sa.update(self.model)
            .where(self.model.id.in_(tasks_id))
            .values(state=TaskState.QUEUING, lasted_execute_time=sa.func.now())
        )

        return tasks_id

    async def get_review_tasks_id(self) -> Sequence[int]:
        # 入队时间或者 ACTIVATING 运行超过 20 分钟的
        stmt = sa.select(self.model.id).where(
            sa.not_(self.model.is_deleted),
            self.model.state.in_([TaskState.ACTIVATING, TaskState.QUEUING]),
            self.model.lasted_execute_time < sa.func.now() - timedelta(minutes=20),
        )

        result = await self.session.execute(stmt)

        tasks_id = result.scalars().unique().all()

        return tasks_id

    async def get_tasks_by_session_ids(
        self, session_ids: list[str], state: str | None = None
    ) -> Sequence[Tasks]:
        stmt = (
            sa.select(self.model)
            .where(
                sa.not_(self.model.is_deleted), self.model.session_id.in_(session_ids)
            )
            .order_by(self.model.created_at.desc())
        )

        stmt = stmt.options(joinedload(self.model.workspace))
        stmt = stmt.options(joinedload(self.model.chats))

        if state == TaskState.WAITING.value:
            stmt = stmt.where(self.model.state == TaskState.WAITING)
        elif state == TaskState.FINISHED.value:
            stmt = stmt.where(self.model.state == TaskState.FINISHED)
        elif state == TaskState.FAILED.value:
            stmt = stmt.where(
                self.model.state.in_([TaskState.FAILED, TaskState.CANCELLED])
            )
        elif state == "in_progress":
            stmt = stmt.where(
                self.model.state.in_(
                    [
                        TaskState.ACTIVATING,
                        TaskState.QUEUING,
                        TaskState.INITIAL,
                        TaskState.SCHEDULING,
                    ]
                )
            )

        result = await self.session.execute(stmt)
        return result.scalars().unique().all()

    async def get_tasks_count_by_session_ids(
        self, session_ids: list[str], state: TaskState
    ) -> int:
        stmt = (
            sa.select(self.model.id)
            .where(
                sa.not_(self.model.is_deleted),
                self.model.session_id.in_(session_ids),
                self.model.state == state,
            )
            .order_by(self.model.created_at.desc())
        )

        count_stmt = sa.select(sa.func.count()).select_from(stmt.subquery())
        count = await self.session.execute(count_stmt)
        return count.scalar_one()

    async def get_tasks_by_keywords_and_session_ids(
        self, session_ids: list[str], keywords: str
    ) -> Sequence[Tasks]:
        match_expr = match(
            self.model.__table__.c.keywords,
            against=keywords,
            # in_boolean_mode=True,
            in_natural_language_mode=True,
            # with_query_expansion=True,
        )
        stmt = (
            sa.select(self.model)
            .where(
                sa.not_(self.model.is_deleted),
                self.model.session_id.in_(session_ids),
                match_expr,
            )
            .order_by(sa.desc(match_expr))
        )

        stmt = stmt.options(joinedload(self.model.workspace))
        stmt = stmt.options(joinedload(self.model.chats))

        result = await self.session.execute(stmt)
        return result.scalars().unique().all()
