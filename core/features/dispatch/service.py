import typing
import uuid
import asyncio
import logging
from collections.abc import Sequence
from datetime import datetime, timezone
from typing import Any, override


from core.shared.globals import broker, Agent, RSession
from core.shared.components.openai.agent import OutputSchemaType
from core.shared.database.session import (
    get_async_session_direct,
    get_async_tx_session_direct,
    AsyncTxSession,
)
from core.shared.enums import TaskState, MessageRole, TaskUnitState
from ..tasks.scheme import Tasks
from ..tasks_unit.scheme import TasksUnit
from ..tasks_chat.scheme import TasksChat
from ..tasks.models import TaskCreateModel, TaskUpdateModel
from ..tasks_chat.models import TaskChatCreateModel
from ..tasks_unit.models import TaskUnitCreateModel, TaskUnitUpdateModel
from ..audits_log.models import AuditCreateModel, AuditCreateTaskLogModel
from ..tasks_workspace.models import TaskWorkspaceCreateModel, TaskWorkspaceUpdateModel
from ..tasks import service as tasks_service
from ..tasks_unit import service as tasks_unit_service
from ..tasks_history import service as tasks_history_service
from ..tasks_chat import service as tasks_chat_service
from ..tasks_workspace import service as tasks_workspace_service
from ..audits_log import service as audits_log_service
from .models import (
    TaskDispatchCreateModel,
    TaskDispatchGeneratorInfoModel,
    TaskDispatchGeneratorPlanningModel,
    TaskDispatchUpdatePlanningModel,
    TaskDispatchExecuteUnitModel,
    TaskDispatchGeneratorExecuteUnitModel,
    TaskDispatchExecuteUnitOutputModel,
    TaskUnitDispatchModel,
    TaskDispatchGeneratorNextStateModel,
)
from . import prompt


logger = logging.getLogger("Dispatch-Task")


# ---- 调度器核心方法
class Dispatch:
    # 正常调度器驱动任务
    ready_tasks_topic = "ready-tasks"
    # Unit 核心驱动任务
    running_tasks_topic = "running-tasks"

    @classmethod
    async def start_ready_producer(cls):
        """开始调度生产"""
        while True:
            tasks_id = await get_dispatch_tasks_id()
            for task_id in tasks_id:
                await cls.send_to_ready_topic(task_id=task_id)
            await asyncio.sleep(60)

    @classmethod
    async def start_ready_consumer(cls, message: dict[str, int]):
        """消费就绪任务"""
        await execute_task(task_id=message["task_id"])

    @classmethod
    async def start_running_consumer(cls, message: dict[str, int]):
        """消费运行任务"""
        await running_task(task_id=message["task_id"])

    @classmethod
    async def send_to_ready_topic(cls, task_id: int):
        """发送到就绪任务"""
        await broker.send(topic=cls.ready_tasks_topic, message={"task_id": task_id})

    @classmethod
    async def send_to_running_topic(cls, task_id: int):
        """发送到运行任务"""
        await broker.send(topic=cls.running_tasks_topic, message={"task_id": task_id})

    @classmethod
    async def start(cls):
        """启动调度器"""
        asyncio.create_task(cls.start_ready_producer())

        await broker.consumer(
            topic=cls.ready_tasks_topic, callback=cls.start_ready_consumer, count=5
        )
        await broker.consumer(
            topic=cls.running_tasks_topic, callback=cls.start_running_consumer, count=5
        )

    @classmethod
    async def shutdown(cls):
        """关闭调度器"""
        await broker.shutdown()


async def get_dispatch_tasks_id() -> Sequence[int]:
    """
    获取调度任务
    """
    async with get_async_tx_session_direct() as session:
        return await tasks_service.get_dispatch_tasks_id(session=session)


# -----

# ---- Task Agent ----


class TaskAgent(Agent):
    @override
    async def run(
        self,
        input: str | list[dict[str, Any]],
        session: RSession | None = None,
        output_type: type[OutputSchemaType] | None = None,
        **kwargs: Any,
    ):
        response = await super().run(
            input=input, session=session, output_type=output_type, **kwargs
        )
        return response

    async def create_task(self, create_model: TaskDispatchCreateModel) -> Tasks | str:
        """
        创建任务
        """

        response_model: TaskDispatchGeneratorInfoModel = await self.run(
            output_type=TaskDispatchGeneratorInfoModel,
            input=[
                {
                    "role": MessageRole.SYSTEM,
                    "content": prompt.task_analyst_prompt(
                        TaskDispatchGeneratorInfoModel
                    ),
                },
                {
                    "role": MessageRole.USER,
                    "content": create_model.to_json_markdown(),
                },
            ],
        )

        async with get_async_tx_session_direct() as session:
            if not response_model.is_splittable:
                # 记录审计日志
                await audits_log_service.create(
                    create_model=AuditCreateTaskLogModel(
                        session_id=create_model.session_id,
                        thinking=response_model.thinking,
                    ).to_audit_log(),
                    session=session,
                )
                return response_model.thinking

            # 1. 先创建工作空间, 生成需求 PRD 等.
            workspace = await tasks_workspace_service.create(
                create_model=TaskWorkspaceCreateModel(prd=response_model.prd),
                session=session,
            )

            expect_execute_time = response_model.expect_execute_time.get_utc_datetime(
                create_model.owner_timezone
            )

            # 2. 再创建任务
            task = await tasks_service.create(
                create_model=TaskCreateModel(
                    workspace_id=workspace.id,
                    session_id=create_model.session_id,
                    owner=create_model.owner,
                    owner_timezone=create_model.owner_timezone,
                    original_user_input=create_model.original_user_input,
                    name=response_model.name,
                    expect_execute_time=expect_execute_time,
                    keywords=response_model.keywords,
                ),
                session=session,
            )

            # 3. 记录审计日志
            await audits_log_service.create(
                create_model=AuditCreateTaskLogModel(
                    session_id=create_model.session_id,
                    thinking=response_model.thinking,
                    task=task,
                ).to_audit_log(),
                session=session,
            )

        # 4. 将任务加入就绪队列
        await call_soon_task(task_id=task.id)
        return task

    async def generator_task_planning(self, task_id: int):
        async with get_async_tx_session_direct() as session:
            task = await tasks_service.get(task_id=task_id, session=session)

            # 若当前轮次, 和上轮次均为空
            if not task.curr_round_id and not task.prev_round_id:
                workspace = await tasks_workspace_service.get(
                    workspace_id=task.workspace_id, session=session
                )
                prd = workspace.prd

                # 拆解执行计划
                response_model: TaskDispatchGeneratorPlanningModel = await self.run(
                    output_type=TaskDispatchGeneratorPlanningModel,
                    input=[
                        {
                            "role": MessageRole.SYSTEM,
                            "content": prompt.task_planning_prompt(
                                output_cls=TaskDispatchGeneratorPlanningModel
                            ),
                        },
                        {"role": MessageRole.USER, "content": prd},
                    ],
                )

                await tasks_workspace_service.update(
                    workspace_id=task.workspace_id,
                    update_model=TaskWorkspaceUpdateModel(
                        process=response_model.process
                    ),
                    session=session,
                )

    async def execute_task_unit(self, task_id: int):
        """
        开始执行所有执行单元
        """

        async def _execute_unit(
            unit_id: int, prev_units_content: list[dict[str, Any]], prd: str
        ):
            async with get_async_tx_session_direct() as session:
                unit: TasksUnit = await tasks_unit_service.get(
                    unit_id=unit_id, session=session
                )
                unit = await tasks_unit_service.update(
                    unit_id=unit_id,
                    update_model=TaskUnitUpdateModel(state=TaskUnitState.RUNNING),
                    session=session,
                )

                # 运行执行单元
                response_model: TaskDispatchExecuteUnitOutputModel = await self.run(
                    input=[
                        {
                            "role": MessageRole.SYSTEM,
                            "content": prompt.task_run_unit_prompt(
                                output_cls=TaskDispatchExecuteUnitOutputModel,
                                unit_content=prev_units_content,
                                prd=prd,
                            ),
                        },
                        {
                            "role": MessageRole.USER,
                            "content": unit.objective,
                        },
                    ],
                    output_type=TaskDispatchExecuteUnitOutputModel,
                )

                unit = await tasks_unit_service.update(
                    unit_id=unit_id,
                    update_model=TaskUnitUpdateModel(
                        state=TaskUnitState.COMPLETE, output=response_model.output
                    ),
                    session=session,
                )

        # 拿到所有的执行单元
        async with get_async_session_direct() as session:
            task = await tasks_service.get(task_id=task_id, session=session)
            curr_units = await tasks_unit_service.get_round_units_id(
                round_id=task.curr_round_id, session=session
            )

            # 拿到上一次执行完成的所有 Unit 单元.
            prev_units = await tasks_unit_service.get_round_units(
                round_id=task.prev_round_id, session=session
            )

            # 将上次的执行单元做完的 Unit 汇总为 list[JSON].
            prev_units_content = [
                TaskUnitDispatchModel.model_validate(unit).model_dump()
                for unit in prev_units
            ]

            workspace = await tasks_workspace_service.get(
                workspace_id=task.workspace_id, session=session
            )
            prd = workspace.prd

        await asyncio.gather(
            *[
                asyncio.create_task(_execute_unit(unit_id, prev_units_content, prd))
                for unit_id in curr_units
            ]
        )
        # 这一批全部运行完后, 我们会开启下一轮
        await Dispatch.send_to_running_topic(task_id=task_id)

    async def generator_task_unit(self, task_id: int):
        async with get_async_tx_session_direct() as session:
            task = await tasks_service.get(task_id=task_id, session=session)

            workspace = await tasks_workspace_service.get(
                workspace_id=task.workspace_id, session=session
            )

            process = workspace.process

            # 拆解执行单元
            response_model: TaskDispatchGeneratorExecuteUnitModel = await self.run(
                output_type=TaskDispatchGeneratorExecuteUnitModel,
                input=[
                    {
                        "role": MessageRole.SYSTEM,
                        "content": prompt.task_get_unit_prompt(
                            output_cls=TaskDispatchGeneratorExecuteUnitModel
                        ),
                    },
                    {"role": MessageRole.USER, "content": process},
                ],
            )

            # 派发轮次
            task = await tasks_service.update(
                task_id=task.id,
                update_model=TaskUpdateModel(
                    prev_round_id=task.curr_round_id,
                    curr_round_id=uuid.uuid4(),
                ),
                session=session,
            )

            # 创建执行单元
            for unit in response_model.unit_list:
                unit: TaskDispatchExecuteUnitModel

                await tasks_unit_service.create(
                    create_model=TaskUnitCreateModel(
                        task_id=task.id,
                        name=unit.name,
                        objective=unit.objective,
                        round_id=task.curr_round_id,
                    ),
                    session=session,
                )

    async def waiting_task(self, task_id: int, user_message: str):
        # 处理用户反馈的信息. 更新 Process 并将任务重新入队.
        async with get_async_tx_session_direct() as session:
            task = await tasks_service.get(task_id=task_id, session=session)
            notify_user = await tasks_chat_service.get_last_message(
                task_id=task_id, role=MessageRole.ASSISTANT, session=session
            )

            workspace = await tasks_workspace_service.get(
                workspace_id=task.workspace_id, session=session
            )
            prd = workspace.prd

            # 更新执行计划
            response_model: TaskDispatchUpdatePlanningModel = await self.run(
                output_type=TaskDispatchUpdatePlanningModel,
                input=[
                    {
                        "role": MessageRole.SYSTEM,
                        "content": prompt.task_waiting_handle_prompt(
                            output_cls=TaskDispatchUpdatePlanningModel
                        ),
                    },
                    {
                        "role": MessageRole.USER,
                        "content": f"PRD: {prd}, notify_user: {notify_user}, user_message: {user_message}",
                    },
                ],
            )

            await tasks_workspace_service.update(
                workspace_id=task.workspace_id,
                update_model=TaskWorkspaceUpdateModel(process=response_model.process),
                session=session,
            )

            # 加入调度 ..
            await call_soon_task(task_id=task_id)

    async def running_task(self, task_id: int):
        """
        Unit 触发任务继续执行.
        """

        # 获取当前的执行单元的 output, 并根据 output 来更新 process.
        async with get_async_tx_session_direct() as session:
            task = await tasks_service.get(task_id=task_id, session=session)
            workspace = await tasks_workspace_service.get(
                workspace_id=task.workspace_id, session=session
            )
            process = workspace.process

            curr_units = await tasks_unit_service.get_round_units(
                round_id=task.curr_round_id, session=session
            )
            curr_units_content = [
                TaskUnitDispatchModel.model_validate(unit).model_dump()
                for unit in curr_units
            ]

            # 根据 units 的反馈, 来更新当前的 process 以及任务状态
            response_model: TaskDispatchGeneratorNextStateModel = await self.run(
                output_type=TaskDispatchGeneratorNextStateModel,
                input=[
                    {
                        "role": MessageRole.SYSTEM,
                        "content": prompt.task_run_next_prompt(
                            output_cls=TaskDispatchGeneratorNextStateModel,
                            unit_content=curr_units_content,
                        ),
                    },
                    {"role": MessageRole.USER, "content": process},
                ],
            )

            await tasks_workspace_service.update(
                workspace_id=task.workspace_id,
                update_model=TaskWorkspaceUpdateModel(process=response_model.process),
                session=session,
            )

            if response_model.state == TaskState.WAITING:
                # Waiting 需要用户补充信息
                await tasks_chat_service.create(
                    create_model=TaskChatCreateModel(
                        role=MessageRole.ASSISTANT,
                        task_id=task.id,
                        message=typing.cast("str", response_model.notify_user),
                    ),
                    session=session,
                )
                # 清理其他同批次的执行单元
            elif response_model.state == TaskState.ACTIVATING:
                # 生成执行单元
                await self.generator_task_unit(task_id=task_id)
                # 运行执行单元
                await self.execute_task_unit(task_id=task_id)
            else:
                return

            if response_model.state != task.state:
                # 清理当前的 round, 因为我们要派发新一轮的了
                await tasks_unit_service.clear_round_units(
                    round_id=task.curr_round_id, session=session
                )

                # 设置为新状态
                await tasks_service.update(
                    task_id=task.id,
                    update_model=TaskUpdateModel(
                        state=TaskState(response_model.state.value)
                    ),
                    session=session,
                )

    async def execute_task(self, task_id: int):
        logger.info(f"就绪队列消费: {task_id}")

        async with get_async_tx_session_direct() as session:
            task = await tasks_service.get(task_id=task_id, session=session)
            if task.state != TaskState.QUEUING:
                match task.state:
                    case state if state in [TaskState.CANCELLED]:
                        logger.info("任务非正常出队, 已被用户取消. 放弃该任务")
                        return
                    case state if state in [TaskState.FAILED, TaskState.FINISHED]:
                        logger.info("任务非正常出队, 已进入结束态. 放弃该任务")
                        return
                    case _:
                        logger.info("任务非正常出队, 状态可恢复. 尝试恢复中...")
                        await call_soon_task(task_id=task_id)
                        return

            await tasks_service.update(
                task_id=task_id,
                update_model=TaskUpdateModel(state=TaskState.ACTIVATING),
                session=session,
            )

        # 生成执行计划
        await self.generator_task_planning(task_id=task_id)

        # 生成执行单元
        await self.generator_task_unit(task_id=task_id)

        # 运行执行单元
        await self.execute_task_unit(task_id=task_id)


async def call_soon_task(task_id: int):
    """
    尝试将任务添加到调度中
    """

    async with get_async_tx_session_direct() as session:
        task = await tasks_service.get(task_id=task_id, session=session)
        expect_execute_time = task.expect_execute_time.replace(tzinfo=timezone.utc)

        if expect_execute_time <= datetime.now(timezone.utc):
            # 记录审计日志
            await audits_log_service.create(
                session=session,
                create_model=AuditCreateModel(
                    session_id=task.session_id,
                    message=f"Task: {task.id} call soon task. add to dispatch topic.",
                ),
            )
            await tasks_service.update(
                task_id=task_id,
                update_model=TaskUpdateModel(state=TaskState.QUEUING),
                session=session,
            )

    if expect_execute_time <= datetime.now(timezone.utc):
        await Dispatch.send_to_ready_topic(task_id=task.id)


async def create_task(create_model: TaskDispatchCreateModel) -> Tasks | str:
    """
    创建任务
    """
    agent = TaskAgent(name="Task-Analyst-Agent", instructions="任务分析 Agent.")
    return await agent.create_task(create_model=create_model)


async def execute_task(task_id: int):
    """
    开始执行任务
    """
    agent = TaskAgent(name="Task-Dispatch-Agent", instructions="任务调度 Agent.")
    return await agent.execute_task(task_id=task_id)


async def running_task(task_id: int):
    """
    继续运行任务
    """
    agent = TaskAgent(name="Task-Dispatch-Agent", instructions="任务调度 Agent.")
    return await agent.running_task(task_id=task_id)


async def add_user_message(task_id: int, user_message: str) -> None:
    """
    用户补充任务信息
    """
    agent = TaskAgent(name="Task-Dispatch-Agent", instructions="任务调度 Agent.")
    asyncio.create_task(agent.waiting_task(task_id=task_id, user_message=user_message))
