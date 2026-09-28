"""What carries work between processes.

Inbound events and long-running jobs are handed to background workers rather
than handled inside the request that produced them. This interface is that
handoff.

Two properties callers rely on, which any implementation must preserve:

*Delivery is at-least-once.* A handler may see the same message twice — after a
worker crash, or a redelivery. Handlers must be idempotent; the platform already
de-duplicates inbound events by id for this reason.

*Acknowledgement means done, not received.* A message is only acknowledged after
its handler returns. A handler that raises leaves the message to be retried.

The reference implementation is an in-process queue: publisher and subscriber
are the same program, and the queue is lost on restart. That is the right
default for a single-node deployment and is honest about what it gives up.
Anything running more than one node supplies a durable implementation.
"""

from __future__ import annotations

import asyncio
import logging
from abc import ABC, abstractmethod
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

Handler = Callable[[dict], Awaitable[None]]


class MessageQueue(ABC):
    """Topic-based, at-least-once message passing."""

    @abstractmethod
    async def publish(self, topic: str, payload: dict) -> None:
        """Enqueue ``payload`` on ``topic``. Returns once it is accepted."""

    @abstractmethod
    async def subscribe(self, topic: str, handler: Handler) -> None:
        """Register ``handler`` for ``topic``. May be called before ``start``."""

    @abstractmethod
    async def start(self) -> None:
        """Begin delivering messages."""

    @abstractmethod
    async def stop(self) -> None:
        """Stop delivering and release resources. Must be safe to call twice."""


class InProcessQueue(MessageQueue):
    """An asyncio queue per topic, with one delivery task each.

    Publishing after ``stop`` is a no-op rather than an error, so shutdown
    ordering between the API and its workers does not have to be exact.
    """

    def __init__(self, max_depth: int = 10_000) -> None:
        self._queues: dict[str, asyncio.Queue] = {}
        self._handlers: dict[str, list[Handler]] = {}
        self._tasks: list[asyncio.Task] = []
        self._max_depth = max_depth
        self._running = False

    def _queue_for(self, topic: str) -> asyncio.Queue:
        queue = self._queues.get(topic)
        if queue is None:
            queue = asyncio.Queue(maxsize=self._max_depth)
            self._queues[topic] = queue
        return queue

    async def publish(self, topic: str, payload: dict) -> None:
        if not self._running:
            logger.debug("queue not running; dropping message on %s", topic)
            return
        await self._queue_for(topic).put(payload)

    async def subscribe(self, topic: str, handler: Handler) -> None:
        self._handlers.setdefault(topic, []).append(handler)
        self._queue_for(topic)
        if self._running:
            self._tasks.append(asyncio.create_task(self._drain(topic)))

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for topic in list(self._handlers):
            self._tasks.append(asyncio.create_task(self._drain(topic)))

    async def _drain(self, topic: str) -> None:
        queue = self._queue_for(topic)
        while self._running:
            try:
                payload = await queue.get()
            except asyncio.CancelledError:
                return
            for handler in list(self._handlers.get(topic, ())):
                try:
                    await handler(payload)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # One bad message must not take the subscriber down.
                    logger.exception("handler failed for topic %s", topic)
            queue.task_done()

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
