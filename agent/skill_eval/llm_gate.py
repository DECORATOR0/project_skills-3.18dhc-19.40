"""
Shared LLM-side concurrency gates for skill_eval.
"""
from __future__ import annotations

import asyncio
import threading
from contextlib import asynccontextmanager, contextmanager

from .config import LLM_API_MAX_CONCURRENCY

_SYNC_GATE = threading.BoundedSemaphore(LLM_API_MAX_CONCURRENCY)
_ASYNC_GATES: dict[int, asyncio.Semaphore] = {}
_ASYNC_GATES_LOCK = threading.Lock()


def _get_async_gate() -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    key = id(loop)
    with _ASYNC_GATES_LOCK:
        gate = _ASYNC_GATES.get(key)
        if gate is None:
            gate = asyncio.Semaphore(LLM_API_MAX_CONCURRENCY)
            _ASYNC_GATES[key] = gate
        return gate


@contextmanager
def sync_llm_slot():
    with _SYNC_GATE:
        yield


@asynccontextmanager
async def async_llm_slot():
    gate = _get_async_gate()
    async with gate:
        yield
