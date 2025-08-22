from typing import TypeAlias
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from .connection import AsyncSessionLocal

AsyncTxSession: TypeAlias = AsyncSession


async def get_async_session() -> AsyncGenerator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        yield session


async def get_async_tx_session() -> AsyncGenerator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        async with session.begin():
            yield session


@asynccontextmanager
async def get_async_session_direct():
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def get_async_tx_session_direct():
    async with AsyncSessionLocal() as session:
        async with session.begin():
            yield session


__all__ = [
    "get_async_session",
    "get_async_tx_session",
    "get_async_session_direct",
    "get_async_tx_session_direct",
    "AsyncSession",
    "AsyncTxSession",
]
