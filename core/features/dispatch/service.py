import typing
import json
import uuid
import asyncio
import logging
from collections.abc import Sequence, AsyncGenerator
from datetime import datetime, timezone
from typing import Any, override
from dataclasses import dataclass
import traceback

from agents import Model, RunContextWrapper, function_tool
from agents.mcp import MCPServer, MCPServerSse, MCPServerSseParams
from contextlib import asynccontextmanager, AsyncExitStack

from core.shared.database.session import AsyncTxSession
from core.shared.base.models import LLMTimeField
from core.shared.components.openai.agent import (
    Tokens,
    get_mcp_servers,
    close_mcp_servers,
)
from core.shared.globals import broker, Agent, RSession
from core.shared.components.openai.agent import OutputSchemaType
from core.shared.database.session import (
    get_async_session_direct,
    get_async_tx_session_direct,
)
from core.shared.enums import TaskState, MessageRole, TaskUnitState, AgentTaskState
from ..tasks.scheme import Tasks
from ..tasks_unit.scheme import TasksUnit
from ..tasks.models import TaskCreateModel, TaskUpdateModel
from ..tasks_chat.models import TaskChatCreateModel, TaskChatInCrudModel
from ..tasks_unit.models import TaskUnitCreateModel, TaskUnitUpdateModel
from ..tasks_history.models import TaskHistoryCreateModel
from ..audits_log.models import AuditLLMlogModel
from ..tasks_workspace.models import TaskWorkspaceCreateModel, TaskWorkspaceUpdateModel
from ..tasks import service as tasks_service
from ..tasks_unit import service as tasks_unit_service
from ..tasks_chat import service as tasks_chat_service
from ..tasks_history import service as tasks_history_service
from ..tasks_workspace import service as tasks_workspace_service
from ..audits_log import service as audits_log_service
from .models import (
    TaskDispatchLLMModel,
    TaskDispatchCreateModel,
    TaskDispatchRefactorModel,
    TaskDispatchGeneratorInfoOutput,
    TaskDispatchGeneratorPlanningOutput,
    TaskDispatchUpdatePlanningOutput,
    TaskDispatchUpdatePlanningInput,
    TaskDispatchExecuteUnitInput,
    TaskDispatchGeneratorExecuteUnitOutput,
    TaskDispatchExecuteUnitOutput,
    TaskUnitDispatchInput,
    TaskDispatchGeneratorNextStateOutput,
    TaskDispatchGeneratorResultOutput,
    TaskDispatchGeneratorResultInput,
    TaskDispatchRefactorInfoOutput,
)
from . import prompt

from multi_agent_centre.core.model_provider import ModelAdapter
from multi_agent_centre.core._a2a.tools import send_a2a_message
from multi_agent_centre.api.xyz_platform import XyzPlatformServer
from multi_agent_centre.core.utils import store_usage_by_session


model_adapter = ModelAdapter()

logger = logging.getLogger("Dispatch-Task")


async def get_llm_model(session_id: str) -> Model:
    model_info = await XyzPlatformServer.get_model_info_by_session_id(
        session_id=session_id
    )
    model_data = TaskDispatchLLMModel.model_validate(model_info)
    return model_adapter.get_model(
        model_name=model_data.model_name, api_key=model_data.api_key
    )


# ---- 调度器核心方法
class Dispatch:
    # 正常调度器驱动任务
    ready_tasks_topic = "ready-tasks"
    # 异常调度器检查任务
    review_tasks_topic = "review-tasks"
    # Unit 核心驱动任务
    running_tasks_topic = "running-tasks"

    @classmethod
    async def start_ready_producer(cls):
        """开始调度就绪任务"""
        while True:
            tasks_id = await get_dispatch_tasks_id()
            for task_id in tasks_id:
                await cls.send_to_ready_topic(task_id=task_id)
            await asyncio.sleep(60)

    @classmethod
    async def start_review_producer(cls):
        """开始调度检查任务"""
        while True:
            tasks_id = await get_review_tasks_id()
            for task_id in tasks_id:
                await cls.send_to_review_topic(task_id=task_id)
            # 10min
            await asyncio.sleep(1200)

    @classmethod
    async def send_to_ready_topic(cls, task_id: int):
        """发送到就绪任务"""
        await broker.send(topic=cls.ready_tasks_topic, message={"task_id": task_id})

    @classmethod
    async def send_to_running_topic(cls, task_id: int):
        """发送到运行任务"""
        await broker.send(topic=cls.running_tasks_topic, message={"task_id": task_id})

    @classmethod
    async def send_to_review_topic(cls, task_id: int):
        """发送到检查队伍"""
        await broker.send(topic=cls.review_tasks_topic, message={"task_id": task_id})

    @classmethod
    async def start_ready_consumer(cls, message: dict[str, int]):
        """消费就绪任务"""
        await execute_task(task_id=message["task_id"])

    @classmethod
    async def start_running_consumer(cls, message: dict[str, int]):
        """消费运行任务"""
        await running_task(task_id=message["task_id"])

    @classmethod
    async def start_review_consumer(cls, message: dict[str, int]):
        """消费检查任务"""
        await review_task(task_id=message["task_id"])

    @classmethod
    async def start(cls):
        """启动调度器"""
        asyncio.create_task(cls.start_ready_producer())
        asyncio.create_task(cls.start_review_producer())

        await broker.consumer(
            topic=cls.ready_tasks_topic, callback=cls.start_ready_consumer, count=5
        )
        await broker.consumer(
            topic=cls.running_tasks_topic, callback=cls.start_running_consumer, count=5
        )

        await broker.consumer(
            topic=cls.review_tasks_topic, callback=cls.start_review_consumer, count=1
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


async def get_review_tasks_id() -> Sequence[int]:
    """
    获取检查任务
    """
    async with get_async_tx_session_direct() as session:
        return await tasks_service.get_review_tasks_id(session=session)


# ---- Task Agent ----


@dataclass
class XyzContext:
    session_id: str
    user_id: str
    agent_id: int


@function_tool
async def get_xyz_contenxt(wrapper: RunContextWrapper[XyzContext]) -> str:
    """
    Before calling any XYZ Platform tool. The tool should be called first to get the necessary information.
    """

    print("get_xyz_contenxt 运行. ..")

    return (
        f"Current conversation user_id: {wrapper.context.user_id}"
        f"Current conversation agent_id: {wrapper.context.agent_id}"
        f"Current conversation session_id: {wrapper.context.session_id}"
    )


class TaskAgent(Agent):
    @override
    async def run(
        self,
        input: str | list[dict[str, Any]],
        session: RSession | None = None,
        output_type: type[OutputSchemaType] | None = None,
        **kwargs: Any,
    ):
        response, tokens = await super().run(
            input=input, session=session, output_type=output_type, **kwargs
        )
        return response, tokens

    async def create_task(self, create_model: TaskDispatchCreateModel) -> Tasks | str:
        """
        创建任务
        """
        try:
            response_model, tokens = await self.run(
                output_type=TaskDispatchGeneratorInfoOutput,
                input=[
                    {
                        "role": MessageRole.SYSTEM,
                        "content": prompt.task_analyst_prompt(
                            TaskDispatchGeneratorInfoOutput
                        ),
                    },
                    {
                        "role": MessageRole.USER,
                        "content": create_model.to_json_markdown(),
                    },
                ],
            )
            response_model: TaskDispatchGeneratorInfoOutput

            asyncio.create_task(
                store_usage_by_session(
                    source="Task-Generator-Prd",
                    model_name=self.model.model,
                    input_token=tokens.input_tokens,
                    output_token=tokens.output_tokens,
                    cache_token=tokens.cached_tokens,
                    session_id=create_model.session_id,
                )
            )

            async with get_async_tx_session_direct() as session:
                if not response_model.is_splittable:
                    # 记录审计日志
                    await audits_log_service.create(
                        create_model=AuditLLMlogModel(
                            session_id=create_model.session_id,
                            thinking=response_model.thinking,
                            message="不创建任务",
                            tokens=tokens.model_dump(),
                        ).to_audit_log(),
                        session=session,
                    )
                    return response_model.thinking

                # 1. 先创建工作空间, 生成需求 PRD 等.
                workspace = await tasks_workspace_service.create(
                    create_model=TaskWorkspaceCreateModel(prd=response_model.prd),
                    session=session,
                )

                expect_execute_time = (
                    response_model.expect_execute_time.get_utc_datetime()
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
                        mcp_server_infos=create_model.mcp_server_infos,
                    ),
                    session=session,
                )

                # 3. 记录审计日志
                await audits_log_service.create(
                    create_model=AuditLLMlogModel(
                        session_id=create_model.session_id,
                        thinking=response_model.thinking,
                        message=f"任务创建成功: {task.id}",
                        tokens=tokens.model_dump(),
                    ).to_audit_log(),
                    session=session,
                )

            # 4. 将任务加入就绪队列
            await call_soon_task(task_id=task.id)
            return task
        except Exception as exc:
            return str(exc)

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
                response_model, tokens = await self.run(
                    output_type=TaskDispatchGeneratorPlanningOutput,
                    input=[
                        {
                            "role": MessageRole.SYSTEM,
                            "content": prompt.task_planning_prompt(
                                output_cls=TaskDispatchGeneratorPlanningOutput
                            ),
                        },
                        {"role": MessageRole.USER, "content": prd},
                    ],
                )
                response_model: TaskDispatchGeneratorPlanningOutput

                await audits_log_service.create(
                    create_model=AuditLLMlogModel(
                        session_id=task.session_id,
                        thinking=response_model.thinking,
                        message="执行计划生成成功",
                        tokens=tokens.model_dump(),
                    ).to_audit_log(),
                    session=session,
                )

                await tasks_workspace_service.update(
                    workspace_id=task.workspace_id,
                    update_model=TaskWorkspaceUpdateModel(
                        process=response_model.process
                    ),
                    session=session,
                )

                self.model: Model
                asyncio.create_task(
                    store_usage_by_session(
                        source="Task-Generator-Planning",
                        model_name=self.model.model,
                        input_token=tokens.input_tokens,
                        output_token=tokens.output_tokens,
                        cache_token=tokens.cached_tokens,
                        session_id=task.session_id,
                    )
                )

    async def execute_task_unit(self, task_id: int):
        """
        开始执行所有执行单元
        """

        async def _execute_unit(
            unit_id: int,
            prev_units_content: list[dict[str, Any]],
            chats: list[dict[str, Any]],
            prd: str,
            prd_created_time: datetime,
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
                response_model, tokens = await self.run(
                    input=[
                        {
                            "role": MessageRole.SYSTEM,
                            "content": prompt.task_run_unit_prompt(
                                output_cls=TaskDispatchExecuteUnitOutput,
                                unit_content=prev_units_content,
                                prd=prd,
                                chats=chats,
                                prd_created_time=prd_created_time,
                            ),
                        },
                        {
                            "role": MessageRole.USER,
                            "content": unit.objective,
                        },
                    ],
                    output_type=TaskDispatchExecuteUnitOutput,
                )
                response_model: TaskDispatchExecuteUnitOutput

                unit = await tasks_unit_service.update(
                    unit_id=unit_id,
                    update_model=TaskUnitUpdateModel(
                        state=TaskUnitState.COMPLETE, output=response_model.output
                    ),
                    session=session,
                )

                await audits_log_service.create(
                    create_model=AuditLLMlogModel(
                        session_id=task.session_id,
                        thinking=response_model.thinking,
                        message=f"执行单元 {unit.name} 运行完成: {unit.output}",
                        tokens=tokens.model_dump(),
                    ).to_audit_log(),
                    session=session,
                )

                asyncio.create_task(
                    store_usage_by_session(
                        source="Task-Executor-Unit",
                        model_name=self.model.model,
                        input_token=tokens.input_tokens,
                        output_token=tokens.output_tokens,
                        cache_token=tokens.cached_tokens,
                        session_id=task.session_id,
                    )
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
                TaskUnitDispatchInput.model_validate(unit).model_dump()
                for unit in prev_units
            ]

            workspace = await tasks_workspace_service.get(
                workspace_id=task.workspace_id, session=session
            )
            prd = workspace.prd
            prd_created_time = workspace.created_at

            chats = [
                TaskChatInCrudModel.model_validate(chat).model_dump()
                for chat in task.chats
            ]

        await asyncio.gather(
            *[
                asyncio.create_task(
                    _execute_unit(
                        unit_id, prev_units_content, chats, prd, prd_created_time
                    )
                )
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
            response_model, tokens = await self.run(
                output_type=TaskDispatchGeneratorExecuteUnitOutput,
                input=[
                    {
                        "role": MessageRole.SYSTEM,
                        "content": prompt.task_get_unit_prompt(
                            output_cls=TaskDispatchGeneratorExecuteUnitOutput
                        ),
                    },
                    {"role": MessageRole.USER, "content": process},
                ],
            )
            response_model: TaskDispatchGeneratorExecuteUnitOutput

            # 派发轮次
            task = await tasks_service.update(
                task_id=task.id,
                update_model=TaskUpdateModel(
                    prev_round_id=task.curr_round_id,
                    curr_round_id=uuid.uuid4(),
                ),
                session=session,
            )

            await audits_log_service.create(
                create_model=AuditLLMlogModel(
                    session_id=task.session_id,
                    thinking=response_model.thinking,
                    message=f"任务执行单元拆解成功, 派发批次 {task.curr_round_id}",
                    tokens=tokens.model_dump(),
                ).to_audit_log(),
                session=session,
            )

            asyncio.create_task(
                store_usage_by_session(
                    source="Task-Generator-Unit",
                    model_name=self.model.model,
                    input_token=tokens.input_tokens,
                    output_token=tokens.output_tokens,
                    cache_token=tokens.cached_tokens,
                    session_id=task.session_id,
                )
            )

            # 创建执行单元
            for unit in response_model.unit_list:
                unit: TaskDispatchExecuteUnitInput

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
        try:
            # 处理用户反馈的信息. 更新 Process 并将任务重新入队.
            async with get_async_tx_session_direct() as session:
                await tasks_service.update(
                    task_id=task_id,
                    update_model=TaskUpdateModel(
                        state=TaskState.SCHEDULING,
                    ),
                    session=session,
                )

                task = await tasks_service.get(task_id=task_id, session=session)
                notify_user = await tasks_chat_service.get_last_message(
                    task_id=task_id, role=MessageRole.ASSISTANT, session=session
                )

                workspace = await tasks_workspace_service.get(
                    workspace_id=task.workspace_id, session=session
                )

                # 更新执行计划
                response_model, tokens = await self.run(
                    output_type=TaskDispatchUpdatePlanningOutput,
                    input=[
                        {
                            "role": MessageRole.SYSTEM,
                            "content": prompt.task_waiting_handle_prompt(
                                output_cls=TaskDispatchUpdatePlanningOutput
                            ),
                        },
                        {
                            "role": MessageRole.USER,
                            "content": TaskDispatchUpdatePlanningInput(
                                process=workspace.process,
                                notify_user=notify_user.message,  # pyright: ignore[reportOptionalMemberAccess]
                                user_message=user_message,
                            ).to_json_markdown(),
                        },
                    ],
                )
                response_model: TaskDispatchUpdatePlanningOutput

                await audits_log_service.create(
                    create_model=AuditLLMlogModel(
                        session_id=task.session_id,
                        thinking=response_model.thinking,
                        message=f"用户反馈信息后成功更新执行计划. 反馈信息: {user_message}",
                        tokens=tokens.model_dump(),
                    ).to_audit_log(),
                    session=session,
                )

                await tasks_workspace_service.update(
                    workspace_id=task.workspace_id,
                    update_model=TaskWorkspaceUpdateModel(
                        process=response_model.process
                    ),
                    session=session,
                )

            asyncio.create_task(
                store_usage_by_session(
                    source="Task-Generator-Unit",
                    model_name=self.model.model,
                    input_token=tokens.input_tokens,
                    output_token=tokens.output_tokens,
                    cache_token=tokens.cached_tokens,
                    session_id=task.session_id,
                )
            )

            await XyzPlatformServer.send_task_refresh(session_id=task.session_id)

            # 加入调度 ..
            await call_soon_task(task_id=task_id)
        except Exception as exc:
            async with get_async_tx_session_direct() as session:
                task = await tasks_service.get(task_id=task_id, session=session)
                await audits_log_service.create(
                    create_model=AuditLLMlogModel(
                        session_id=task.session_id,
                        thinking=f"任务补充信息时报错了, 但我们不重置其状态. {str(exc)}",
                        message=traceback.format_exc(),
                        tokens=Tokens().model_dump(),
                    ).to_audit_log(),
                    session=session,
                )

    async def next_state(
        self, task_id: int, new_state: AgentTaskState, session: AsyncTxSession
    ):
        task = await tasks_service.get(task_id=task_id, session=session)

        # 清理当前的 round, 因为我们要派发新一轮的了
        await tasks_unit_service.clear_round_units(
            round_id=task.curr_round_id, session=session
        )

        # 设置为新状态
        await tasks_service.update(
            task_id=task.id,
            update_model=TaskUpdateModel(state=TaskState(new_state.value)),
            session=session,
        )

        await XyzPlatformServer.send_task_refresh(session_id=task.session_id)

    async def running_task(self, task_id: int):
        """
        Unit 触发任务继续执行.
        """

        try:
            # 获取当前的执行单元的 output, 并根据 output 来更新 process.
            async with get_async_tx_session_direct() as session:
                task = await tasks_service.get(task_id=task_id, session=session)

                # 任务正在被重构. 这里不要再继续了.
                if task.state == TaskState.UPDATING:
                    return

                workspace = await tasks_workspace_service.get(
                    workspace_id=task.workspace_id, session=session
                )

                process = workspace.process

                curr_units = await tasks_unit_service.get_round_units(
                    round_id=task.curr_round_id, session=session
                )

                curr_units_content = [
                    TaskUnitDispatchInput.model_validate(unit).model_dump()
                    for unit in curr_units
                ]

                # 根据 units 的反馈, 来更新当前的 process 以及任务状态
                response_model, tokens = await self.run(
                    output_type=TaskDispatchGeneratorNextStateOutput,
                    input=[
                        {
                            "role": MessageRole.SYSTEM,
                            "content": prompt.task_run_next_prompt(
                                output_cls=TaskDispatchGeneratorNextStateOutput,
                                unit_content=curr_units_content,
                                chats=[
                                    TaskChatInCrudModel.model_validate(
                                        chat
                                    ).model_dump()
                                    for chat in task.chats
                                ],
                            ),
                        },
                        {"role": MessageRole.USER, "content": process},
                    ],
                )
                response_model: TaskDispatchGeneratorNextStateOutput

                asyncio.create_task(
                    store_usage_by_session(
                        source="Task-Execute-Continue",
                        model_name=self.model.model,
                        input_token=tokens.input_tokens,
                        output_token=tokens.output_tokens,
                        cache_token=tokens.cached_tokens,
                        session_id=task.session_id,
                    )
                )

                await tasks_history_service.create(
                    create_model=TaskHistoryCreateModel(
                        task_id=task.id,
                        state=TaskState(response_model.state),
                        thinking=response_model.thinking,
                        process=response_model.process,
                    ),
                    session=session,
                )

                await audits_log_service.create(
                    create_model=AuditLLMlogModel(
                        session_id=task.session_id,
                        thinking=response_model.thinking,
                        message=f"任务的状态和 Process 更新推进, 新状态为: {response_model.state}",
                        tokens=tokens.model_dump(),
                    ).to_audit_log(),
                    session=session,
                )

                await tasks_workspace_service.update(
                    workspace_id=task.workspace_id,
                    update_model=TaskWorkspaceUpdateModel(
                        process=response_model.process
                    ),
                    session=session,
                )

            if response_model.state == AgentTaskState.ACTIVATING:
                async with get_async_tx_session_direct() as session:
                    await self.next_state(
                        task_id, new_state=response_model.state, session=session
                    )

                # 生成执行单元
                await self.generator_task_unit(task_id=task_id)
                # 运行执行单元
                await self.execute_task_unit(task_id=task_id)
            elif response_model.state == AgentTaskState.SCHEDULING:
                async with get_async_tx_session_direct() as session:
                    await tasks_service.update(
                        task_id=task.id,
                        update_model=TaskUpdateModel(
                            expect_execute_time=typing.cast(
                                "LLMTimeField", response_model.next_execute_time
                            ).get_utc_datetime(),
                        ),
                        session=session,
                    )
                    await self.next_state(
                        task_id, new_state=response_model.state, session=session
                    )
            elif response_model.state == AgentTaskState.WAITING:
                async with get_async_tx_session_direct() as session:
                    # waiting 需要用户补充信息
                    await tasks_chat_service.create(
                        create_model=TaskChatCreateModel(
                            role=MessageRole.ASSISTANT,
                            task_id=task.id,
                            message=json.dumps(
                                {
                                    "message": response_model.notify_user,
                                    "replenish": response_model.replenish,
                                },
                            ),
                        ),
                        session=session,
                    )
                    await self.next_state(
                        task_id, new_state=response_model.state, session=session
                    )

                # 调用业务层. 补充信息
                await XyzPlatformServer.send_task_provision(
                    session_id=task.session_id,
                    task_id=str(task.id),
                    description=response_model.notify_user,
                    task_name=task.name,
                    created_at=task.created_at.isoformat(),
                    state=TaskState.WAITING.value,
                    replenish=response_model.replenish,
                )
            else:
                async with get_async_tx_session_direct() as session:
                    all_units = await tasks_unit_service.get_by_task(
                        task_id=task_id, session=session
                    )
                    all_units = [
                        TaskUnitDispatchInput.model_validate(unit) for unit in all_units
                    ]
                    result_model, tokens = await self.run(
                        output_type=TaskDispatchGeneratorResultOutput,
                        input=[
                            {
                                "role": MessageRole.SYSTEM,
                                "content": prompt.task_run_result_prompt(
                                    TaskDispatchGeneratorResultOutput
                                ),
                            },
                            {
                                "role": MessageRole.USER,
                                "content": TaskDispatchGeneratorResultInput(
                                    prd=workspace.prd,
                                    process=workspace.process,
                                    all_units=all_units,
                                ).to_json_markdown(),
                            },
                        ],
                    )
                    result_model: TaskDispatchGeneratorResultOutput

                    asyncio.create_task(
                        store_usage_by_session(
                            source="Task-Generator-Result",
                            model_name=self.model.model,
                            input_token=tokens.input_tokens,
                            output_token=tokens.output_tokens,
                            cache_token=tokens.cached_tokens,
                            session_id=task.session_id,
                        )
                    )

                    await tasks_workspace_service.update(
                        workspace_id=task.workspace_id,
                        update_model=TaskWorkspaceUpdateModel(
                            result=result_model.result
                        ),
                        session=session,
                    )

                    await audits_log_service.create(
                        create_model=AuditLLMlogModel(
                            session_id=task.session_id,
                            thinking=result_model.thinking,
                            message=result_model.result,
                            tokens=Tokens().model_dump(),
                        ).to_audit_log(),
                        session=session,
                    )

                    # 最后一步是先得出 Result 再更新状态
                    await self.next_state(
                        task_id, new_state=response_model.state, session=session
                    )

                    await XyzPlatformServer.send_task_result_notify(
                        task_id=str(task_id),
                        task_name=task.name,
                        state=response_model.state,
                        session_id=task.session_id,
                    )

        except Exception:
            logger.error(f"执行任务时失败: {traceback.format_exc()}")

            async with get_async_tx_session_direct() as session:
                task = await tasks_service.update(
                    task_id=task_id,
                    update_model=TaskUpdateModel(state=TaskState.FAILED),
                    session=session,
                )

                await audits_log_service.create(
                    create_model=AuditLLMlogModel(
                        session_id=task.session_id,
                        thinking="任务执行时报错了, 将其置为 failed.",
                        message=traceback.format_exc(),
                        tokens=Tokens().model_dump(),
                    ).to_audit_log(),
                    session=session,
                )

            await XyzPlatformServer.send_task_result_notify(
                task_id=str(task.id),
                task_name=task.name,
                state=task.state,
                session_id=task.session_id,
            )

            await XyzPlatformServer.send_task_refresh(session_id=task.session_id)

    async def execute_task(self, task_id: int):
        try:
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

            await XyzPlatformServer.send_task_refresh(session_id=task.session_id)

            # 生成执行计划
            await self.generator_task_planning(task_id=task_id)

            # 生成执行单元
            await self.generator_task_unit(task_id=task_id)

            # 运行执行单元
            await self.execute_task_unit(task_id=task_id)
        except Exception:
            logger.error(f"执行任务时失败: {traceback.format_exc()}")

            async with get_async_tx_session_direct() as session:
                task = await tasks_service.update(
                    task_id=task_id,
                    update_model=TaskUpdateModel(state=TaskState.FAILED),
                    session=session,
                )

                await audits_log_service.create(
                    create_model=AuditLLMlogModel(
                        session_id=task.session_id,
                        thinking="任务执行时报错了, 将其置为 failed.",
                        message=traceback.format_exc(),
                        tokens=Tokens().model_dump(),
                    ).to_audit_log(),
                    session=session,
                )

            await XyzPlatformServer.send_task_refresh(session_id=task.session_id)

    async def refactor_task(self, update_model: TaskDispatchRefactorModel) -> Tasks:
        """
        重构任务
        """
        task_id = update_model.task_id

        async with get_async_tx_session_direct() as session:
            task = await tasks_service.update(
                task_id=task_id,
                update_model=TaskUpdateModel(
                    state=TaskState.UPDATING,
                    curr_round_id=None,
                    prev_round_id=None,
                    lasted_execute_time=None,
                ),
                session=session,
            )

        try:
            await XyzPlatformServer.send_task_refresh(session_id=task.session_id)

            # 生成新的 prd
            response_model, tokens = await self.run(
                output_type=TaskDispatchRefactorInfoOutput,
                input=[
                    {
                        "role": MessageRole.SYSTEM,
                        "content": prompt.task_refactor_prompt(
                            TaskDispatchRefactorInfoOutput
                        ),
                    },
                    {
                        "role": MessageRole.USER,
                        "content": update_model.update_user_prompt,
                    },
                ],
            )

            response_model: TaskDispatchGeneratorInfoOutput

            asyncio.create_task(
                store_usage_by_session(
                    source="Task-Refactor-Prd",
                    model_name=self.model.model,
                    input_token=tokens.input_tokens,
                    output_token=tokens.output_tokens,
                    cache_token=tokens.cached_tokens,
                    session_id=task.session_id,
                )
            )

            async with get_async_tx_session_direct() as session:
                task = await tasks_service.refactor(task_id=task_id, session=session)

                await audits_log_service.create(
                    create_model=AuditLLMlogModel(
                        session_id=task.session_id,
                        thinking=response_model.thinking,
                        message=f"用户更新任务信息成功, 任务已被重构: {response_model.thinking}",
                        tokens=tokens.model_dump(),
                    ).to_audit_log(),
                    session=session,
                )

                await tasks_service.update(
                    task_id=task_id,
                    update_model=TaskUpdateModel(
                        name=response_model.name,
                        state=TaskState.SCHEDULING,
                        prev_round_id=None,
                        curr_round_id=None,
                        lasted_execute_time=None,
                        keywords=response_model.keywords,
                        expect_execute_time=response_model.expect_execute_time.get_utc_datetime(),
                    ),
                    session=session,
                )

                await tasks_workspace_service.update(
                    workspace_id=task.workspace_id,
                    update_model=TaskWorkspaceUpdateModel(
                        prd=response_model.prd, process=None, result=None
                    ),
                    session=session,
                )

            await XyzPlatformServer.send_task_refresh(session_id=task.session_id)
            await call_soon_task(task_id=task_id)
            return task

        except Exception as exc:
            async with get_async_tx_session_direct() as session:
                await audits_log_service.create(
                    create_model=AuditLLMlogModel(
                        session_id=task.session_id,
                        thinking=f"任务 {task_id} 重构时报错了, 但不尝试重置其状态",
                        message=str(exc),
                        tokens=Tokens().model_dump(),
                    ).to_audit_log(),
                    session=session,
                )
                return task


async def get_agent_factory(
    task_id: int | None = None,
    session_id: str | None = None,
    mcp_server_infos: dict[str, Any] | None = None,
) -> TaskAgent:
    model = None
    mcp_server_infos = mcp_server_infos or {}

    if not session_id and not task_id:
        raise Exception("缺少 SessionID 和 TaskID. 无法获取模型信息")

    if session_id:
        model = await get_llm_model(session_id=session_id)
    elif task_id:
        async with get_async_session_direct() as session:
            task = await tasks_service.get(task_id=task_id, session=session)
            model = await get_llm_model(task.session_id)
            mcp_server_infos = task.mcp_server_infos
            session_id = task.session_id

    session_id = typing.cast("str", session_id)
    convsess_info = await XyzPlatformServer.get_info_by_session_id(
        session_id=session_id
    )

    agent = TaskAgent(
        name="Task-Dispatch-Agent",
        instructions=prompt.get_instructions(),
        model=model,
        mcp_server_infos=mcp_server_infos,
        tools=[send_a2a_message, get_xyz_contenxt],
        ctx=XyzContext(
            session_id=session_id,
            agent_id=convsess_info["agentId"],
            user_id=convsess_info["userId"],
        ),
    )
    return agent


async def call_soon_task(task_id: int):
    """
    尝试将任务添加到调度中
    """

    async with get_async_tx_session_direct() as session:
        task = await tasks_service.get(task_id=task_id, session=session)
        expect_execute_time = task.expect_execute_time.replace(tzinfo=timezone.utc)

        if expect_execute_time <= datetime.now(timezone.utc):
            await tasks_service.update(
                task_id=task_id,
                update_model=TaskUpdateModel(
                    state=TaskState.QUEUING,
                    lasted_execute_time=datetime.now(timezone.utc),
                ),
                session=session,
            )

    if expect_execute_time <= datetime.now(timezone.utc):
        await XyzPlatformServer.send_task_refresh(session_id=task.session_id)
        await Dispatch.send_to_ready_topic(task_id=task.id)


async def create_task(create_model: TaskDispatchCreateModel) -> Tasks | str:
    """
    创建任务
    """
    agent = await get_agent_factory(
        session_id=create_model.session_id,
        mcp_server_infos=create_model.mcp_server_infos,
    )

    return await agent.create_task(create_model=create_model)


async def refactor_task(update_model: TaskDispatchRefactorModel) -> None:
    """
    重构任务
    """
    agent = await get_agent_factory(task_id=update_model.task_id)
    asyncio.create_task(agent.refactor_task(update_model))


async def execute_task(task_id: int):
    """
    开始执行任务
    """
    agent = await get_agent_factory(task_id=task_id)
    return await agent.execute_task(task_id=task_id)


async def running_task(task_id: int):
    """
    继续运行任务
    """
    agent = await get_agent_factory(task_id=task_id)
    return await agent.running_task(task_id=task_id)


async def add_user_message(task_id: int, user_message: str) -> None:
    """
    用户补充任务信息
    """
    agent = await get_agent_factory(task_id=task_id)
    asyncio.create_task(agent.waiting_task(task_id=task_id, user_message=user_message))


async def review_task(task_id: int):
    """
    检查任务
    """
    async with get_async_tx_session_direct() as session:
        task = await tasks_service.update(
            task_id=task_id,
            update_model=TaskUpdateModel(
                state=TaskState.FAILED,
            ),
            session=session,
        )

        await audits_log_service.create(
            create_model=AuditLLMlogModel(
                session_id=task.session_id,
                thinking="任务补充信息时报错了, 但我们不重置其状态.",
                message=f"任务 {task_id} 调度超过特定时间. 最后进入调度队列的时间: {task.lasted_execute_time}",
                tokens=Tokens().model_dump(),
            ).to_audit_log(),
            session=session,
        )

    await XyzPlatformServer.send_task_refresh(session_id=task.session_id)
