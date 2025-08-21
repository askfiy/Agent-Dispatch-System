from typing import TypeAlias
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from .connection import AsyncSessionLocal

AsyncTxSession: TypeAlias = AsyncSession


async def get_async_session():
    async with AsyncSessionLocal() as session:
        yield session


async def get_async_tx_session():
    async with AsyncSessionLocal() as session:
        async with session.begin():
            yield session


get_async_session_direct = asynccontextmanager(get_async_session)
get_async_tx_session_direct = asynccontextmanager(get_async_tx_session)

__all__ = [
    "get_async_session",
    "get_async_tx_session",
    "get_async_session_direct",
    "get_async_tx_session_direct",
    "AsyncSession",
    "AsyncTxSession",
]
