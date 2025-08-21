from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from xyz_databases.dependencies.session import get_async_session

from xyz_databases.dependencies.session import engine_async, AsyncBindSession

engine = engine_async

AsyncSessionLocal = AsyncBindSession

__all__ = ["engine", "AsyncSessionLocal"]
