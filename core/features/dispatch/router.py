import fastapi
from fastapi import Depends


from core.shared.database.session import (
    get_async_tx_session,
    AsyncTxSession,
)
from core.shared.models.http import ResponseModel

from ..tasks.models import TaskInCrudModel
from ..tasks_chat import service as tasks_chat_service
from ..tasks_chat.models import TaskChatInCrudModel, TaskChatCreateModel
from ..tasks.scheme import Tasks
from .models import TaskDispatchCreateModel, TaskDispatchRefactorModel
from . import service


controller = fastapi.APIRouter(prefix="/task-dispatch", tags=["Dispatch"])


@controller.post(
    path="/create",
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
    path="/refactor",
    name="重构任务",
    status_code=fastapi.status.HTTP_200_OK,
    response_model=ResponseModel[bool],
)
async def reactor_task(
    update_model: TaskDispatchRefactorModel,
):
    await service.refactor_task(update_model=update_model)
    return ResponseModel(result=True)


@controller.post(
    path="/chat",
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
