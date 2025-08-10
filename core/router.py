import fastapi

from .features.dispatch.router import controller as dispatch_controller
from .features.tasks.router import controller as tasks_controller
from .features.tasks_unit.router import controller as units_controller
from .features.tasks_chat.router import controller as chats_controller
from .features.tasks_history.router import controller as histories_controller
from .features.tasks_workspace.router import controller as workspaces_controller

from .features.audits_log.router import controller as audits_log_controller


api_router = fastapi.APIRouter()
api_router.include_router(dispatch_controller)
api_router.include_router(tasks_controller)
api_router.include_router(units_controller)
api_router.include_router(chats_controller)
api_router.include_router(histories_controller)
api_router.include_router(workspaces_controller)
api_router.include_router(audits_log_controller)
