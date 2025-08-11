import fastapi
from fastapi import Depends


from core.shared.database.session import (
    get_async_session,
    get_async_tx_session,
    AsyncSession,
    AsyncTxSession,
)
from core.shared.models.http import ResponseModel

from ..tasks.models import TaskInCrudModel
from ..tasks_chat import service as tasks_chat_service
from ..tasks_chat.models import TaskChatInCrudModel, TaskChatCreateModel
from ..tasks.scheme import Tasks
from .models import TaskDispatchCreateModel
from . import service


controller = fastapi.APIRouter(prefix="/task-dispatch", tags=["Dispatch"])


@controller.post(
    path="",
    name="创建任务并使其加入调度",
    status_code=fastapi.status.HTTP_201_CREATED,
    response_model=ResponseModel[TaskInCrudModel | str],
)
async def create_task(create_model: TaskDispatchCreateModel):
    task_or_str = await service.create_task(create_model=create_model)

    if isinstance(task_or_str, Tasks):
        return ResponseModel(result=TaskInCrudModel.model_validate(task_or_str))

    return ResponseModel(result=task_or_str)


@controller.post(
    path="/run/{task_id}",
    name="运行任务",
    status_code=fastapi.status.HTTP_200_OK,
    response_model=ResponseModel[bool],
)
async def run_task(task_id: int = fastapi.Path(description="立即运行任务")):
    await service.Dispatch.send_to_running_topic(task_id=task_id)
    return ResponseModel(result=True)


@controller.post(
    path="/add-user-message",
    name="为任务添加用户信息",
    status_code=fastapi.status.HTTP_200_OK,
    response_model=ResponseModel[TaskChatInCrudModel],
)
async def add_user_message(
    create_model: TaskChatCreateModel,
    session: AsyncTxSession = Depends(get_async_tx_session),
) -> ResponseModel[TaskChatInCrudModel]:
    chat = await tasks_chat_service.create(create_model=create_model, session=session)
    await service.add_user_message(
        task_id=create_model.task_id, user_message=create_model.message
    )
    return ResponseModel(result=TaskChatInCrudModel.model_validate(chat))
