from xyz_databases.dependencies.session import engine_async, AsyncBindSession

engine = engine_async

AsyncSessionLocal =  AsyncBindSession

__all__ = ["engine", "AsyncSessionLocal"]
