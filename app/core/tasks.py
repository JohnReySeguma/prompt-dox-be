"""Fire-and-forget asyncio tasks that are kept referenced until they finish."""

import asyncio
from collections.abc import Coroutine
from typing import Any

_running: set[asyncio.Task[Any]] = set()


def spawn(coro: Coroutine[Any, Any, Any], name: str | None = None) -> asyncio.Task[Any]:
    task = asyncio.create_task(coro, name=name)
    _running.add(task)
    task.add_done_callback(_running.discard)
    return task


async def cancel_all() -> None:
    for task in list(_running):
        task.cancel()
    await asyncio.gather(*_running, return_exceptions=True)
