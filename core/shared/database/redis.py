import redis.asyncio as redis
from redis.asyncio.sentinel import Sentinel

from core.config import env_helper


sentinel_hosts = [
    tuple(host.split(":")) for host in env_helper.REDIS_SENTINELS.split(";") if host
]

sentinel_kwargs = {
    "password": env_helper.REDIS_SENTINEL_PASSWORD,
}

sentinel_client = Sentinel(
    sentinels=sentinel_hosts,
    sentinel_kwargs=sentinel_kwargs,
    socket_connect_timeout=30,
)


def get_client():
    return sentinel_client.master_for(  # pyright: ignore[reportUnknownMemberType]
        service_name=env_helper.REDIS_MASTER_NAME,
        password=env_helper.REDIS_PASSWORD,
        db=int(env_helper.REDIS_DB),
        decode_responses=True,
    )


# pool: redis.ConnectionPool = redis.ConnectionPool.from_url(
#     url=env_helper.ASYNC_REDIS_URL, decode_responses=True
# )
#
# client = redis.Redis(connection_pool=pool)

# __all__ = ["pool", "client"]
