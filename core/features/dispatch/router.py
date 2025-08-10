import fastapi

from core.shared.models.http import ResponseModel

from ..tasks.models import TaskInCrudModel
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
