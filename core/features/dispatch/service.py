import json
import logging
from collections.abc import Sequence


from core.shared.globals import Agent
from core.shared.database.session import (
    get_async_tx_session_direct,
)
from core.shared.enums import TaskState, MessageRole
from ..tasks.scheme import Tasks
from ..tasks.models import TaskCreateModel
from ..audits_log.models import AuditCreateModel, AuditCreateTaskLogModel
from ..tasks_workspace.models import TaskWorkspaceCreateModel
from ..tasks import service as tasks_service
from ..tasks_workspace import service as tasks_workspace_service
from ..audits_log import service as audits_log_service
from .models import TaskDispatchCreateModel, TaskDispatchGeneratorInfoModel
from . import prompt


logger = logging.getLogger("Dispatch-Task")


async def get_dispatch_tasks_id() -> Sequence[int]:
    """
    获取调度任务
    """
    async with get_async_tx_session_direct() as session:
        return await tasks_service.get_dispatch_tasks_id(session=session)


async def create_task(create_model: TaskDispatchCreateModel) -> Tasks | str:
    """
    创建任务
    """

    agent = Agent(name="Task-Analyst-Agent", instructions="任务分析器", model="gpt-4.1")
    response_model: TaskDispatchGeneratorInfoModel = await agent.run(
        output_type=TaskDispatchGeneratorInfoModel,
        input=[
            {
                "role": MessageRole.SYSTEM,
                "content": prompt.task_analyst_prompt(TaskDispatchGeneratorInfoModel),
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
        return task
