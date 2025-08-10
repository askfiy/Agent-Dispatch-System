import uuid

import fastapi
from fastapi import Depends

from core.shared.database.session import (
    get_async_session,
    get_async_tx_session,
    AsyncSession,
    AsyncTxSession,
)
from core.shared.models.http import (
    ResponseModel,
    Paginator,
    PaginationRequest,
    PaginationResponse,
)

from . import service
from .models import (
    AuditInCrudModel,
    AuditCreateModel,
)


controller = fastapi.APIRouter(
    prefix="/audits-log",
    tags=["audits"],
)


@controller.get(
    path="/{session_id}",
    name="获取某个会话下面的所有审计记录",
    status_code=fastapi.status.HTTP_200_OK,
    response_model=PaginationResponse,
)
async def get_all(
    session_id: str = fastapi.Path(description="会话 ID"),
    request: PaginationRequest = Depends(PaginationRequest),
    session: AsyncSession = Depends(get_async_session),
) -> PaginationResponse:
    paginator = Paginator(request=request, serializer_cls=AuditInCrudModel)
    paginator = await service.upget_paginator(
        session_id=uuid.UUID(session_id), paginator=paginator, session=session
    )
    return paginator.response


@controller.post(
    path="",
    name="创建审计记录",
    status_code=fastapi.status.HTTP_201_CREATED,
    response_model=ResponseModel[AuditInCrudModel],
)
async def create(
    create_model: AuditCreateModel,
    session: AsyncTxSession = Depends(get_async_tx_session),
) -> ResponseModel[AuditInCrudModel]:
    db_obj = await service.create(create_model=create_model, session=session)
    return ResponseModel(result=AuditInCrudModel.model_validate(db_obj))
