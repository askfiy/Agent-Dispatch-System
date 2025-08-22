import logging
import asyncio
from asyncio import Queue
from typing import Any, TypeAlias
from collections.abc import Callable, Coroutine
from datetime import datetime, timezone

from redis.typing import FieldT, EncodableT
from redis.exceptions import ResponseError
from pydantic import BaseModel, Field

from core.shared.database.redis import get_client

RbrokerMessage: TypeAlias = Any


class RbrokerPayloadMetadata(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RbrokerPayloadExcInfo(BaseModel):
    message: str
    type: str
    failed_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class RbrokerPayload(BaseModel):
    metadata: dict[str, Any] = Field(default_factory=dict)
    content: RbrokerMessage
    exc_info: RbrokerPayloadExcInfo | None = Field(default=None)


class RBroker:
    def __init__(self):
        self._consumer_tasks: list[asyncio.Task[None]] = []
        self._dlq_maxlen = 1000

    @property
    def _client(self):
        return get_client()

    async def _consume_listen(
        self,
        topic: str,
        group_id: str,
        consumer_name: str,
        consumer_queue: Queue[Any],
    ):
        while True:
            try:
                response = await self._client.xreadgroup(
                    group_id, consumer_name, {topic: ">"}, count=1, block=10000
                )
                if not response:
                    continue

                _stream_key, messages = response[0]
                message_id, data = messages[0]

                try:
                    rbroker_message = RbrokerPayload.model_validate_json(
                        data["message"]
                    )
                    job = (topic, group_id, message_id, rbroker_message)
                    await consumer_queue.put(job)
                except Exception as e:
                    logging.error(f"Listener error parsing message: {e}", exc_info=True)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(
                    f"Listener '{consumer_name}' loop error: {e}", exc_info=True
                )
                await asyncio.sleep(5)

    async def _consume_works(
        self,
        name: str,
        consumer_queue: Queue[Any],
        callback: Callable[[RbrokerMessage], Coroutine[Any, Any, None]],
    ):
        while True:
            try:
                (
                    topic,
                    group_id,
                    message_id,
                    rbroker_message,
                ) = await consumer_queue.get()

                try:
                    await callback(rbroker_message.content)
                except Exception as exc:
                    rbroker_message.exc_info = RbrokerPayloadExcInfo(
                        message=str(exc), type=exc.__class__.__name__
                    )
                    await self._client.xadd(
                        f"{topic}-dlq",
                        {"message": rbroker_message.model_dump_json()},
                        maxlen=self._dlq_maxlen,
                    )
                    logging.error(
                        f"Worker error on message {message_id}: {exc}", exc_info=True
                    )
                finally:
                    await self._client.xack(topic, group_id, message_id)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logging.error(
                    f"Worker '{name}' caught an exception: {e}", exc_info=True
                )
            finally:
                if "consumer_queue" in locals():
                    consumer_queue.task_done()

    async def send(self, topic: str, message: RbrokerMessage) -> str:
        rbroker_message = RbrokerPayload(content=message)
        message_payload: dict[FieldT, EncodableT] = {
            "message": rbroker_message.model_dump_json()
        }
        message_id = await self._client.xadd(topic, message_payload)
        return message_id

    async def consumer(
        self,
        topic: str,
        callback: Callable[[RbrokerMessage], Coroutine[Any, Any, None]],
        group_id: str | None = None,
        count: int = 1,
        max_workers: int = 10,
        *args: Any,
        **kwargs: Any,
    ):
        group_id = group_id or topic + "_group"
        consumer_queue = Queue[Any](maxsize=max_workers * 2)

        try:
            await self._client.xgroup_create(topic, group_id, mkstream=True)
        except ResponseError as e:
            if "BUSYGROUP" not in str(e):
                raise

        for i in range(count):
            consumer_name = f"{group_id}-listener-{i + 1}"
            task = asyncio.create_task(
                self._consume_listen(topic, group_id, consumer_name, consumer_queue)
            )
            self._consumer_tasks.append(task)

            for j in range(max_workers):
                worker_name = f"{consumer_name}-worker-{j + 1}"
                task = asyncio.create_task(
                    self._consume_works(worker_name, consumer_queue, callback)
                )
                self._consumer_tasks.append(task)

    async def shutdown(self):
        for task in self._consumer_tasks:
            task.cancel()
        await asyncio.gather(*self._consumer_tasks, return_exceptions=True)
