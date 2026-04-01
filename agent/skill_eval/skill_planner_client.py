"""
OpenAI-compatible LLM client dedicated to skill_eval planning.

This intentionally avoids reusing agent/planner/config.py so the skill planning
stage can be configured independently inside agent/skill_eval/config.py.
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Optional

from openai import AsyncOpenAI, OpenAI

from .network_errors import NetworkCallError, PRIMARY_RETRY_COUNT, RETRY_WAIT_SECONDS, is_retryable_network_error
from .config import (
    SKILL_PLANNER_API_KEY,
    SKILL_PLANNER_BACKUP_BASE_URL,
    SKILL_PLANNER_MAX_TOKENS,
    SKILL_PLANNER_MODEL_NAME,
    SKILL_PLANNER_PRIMARY_BASE_URL,
    SKILL_PLANNER_REQUEST_TIMEOUT,
)

log = logging.getLogger(__name__)


def _build_client(base_url: str) -> OpenAI:
    return OpenAI(
        base_url=base_url,
        api_key=SKILL_PLANNER_API_KEY,
        timeout=SKILL_PLANNER_REQUEST_TIMEOUT,
    )


def _build_async_client(base_url: str) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=base_url,
        api_key=SKILL_PLANNER_API_KEY,
        timeout=SKILL_PLANNER_REQUEST_TIMEOUT,
    )


_primary = _build_client(SKILL_PLANNER_PRIMARY_BASE_URL)
_backup = _build_client(SKILL_PLANNER_BACKUP_BASE_URL)
_async_primary = _build_async_client(SKILL_PLANNER_PRIMARY_BASE_URL)
_async_backup = _build_async_client(SKILL_PLANNER_BACKUP_BASE_URL)


def chat_completion(
    messages: list[dict],
    temperature: float = 0.0,
    response_format: Optional[dict] = None,
    max_tokens: int = SKILL_PLANNER_MAX_TOKENS,
    model: str = SKILL_PLANNER_MODEL_NAME,
) -> str:
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    last_err = None
    for attempt in range(PRIMARY_RETRY_COUNT):
        try:
            resp = _primary.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as exc:
            if not is_retryable_network_error(exc):
                raise
            last_err = exc
            log.warning(
                "Skill planner network attempt %d/%d failed (%s): %s",
                attempt + 1,
                PRIMARY_RETRY_COUNT,
                _primary.base_url,
                exc,
            )
            if attempt < PRIMARY_RETRY_COUNT - 1:
                time.sleep(RETRY_WAIT_SECONDS)
    try:
        resp = _backup.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""
    except Exception as exc:
        if not is_retryable_network_error(exc):
            raise
        raise NetworkCallError(
            stage="planner",
            model=model,
            base_url=str(_backup.base_url),
            attempts=PRIMARY_RETRY_COUNT,
            last_error=str(exc),
        ) from exc


async def async_chat_completion(
    messages: list[dict],
    temperature: float = 0.0,
    response_format: Optional[dict] = None,
    max_tokens: int = SKILL_PLANNER_MAX_TOKENS,
    model: str = SKILL_PLANNER_MODEL_NAME,
) -> str:
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_format:
        kwargs["response_format"] = response_format

    last_err = None
    for attempt in range(PRIMARY_RETRY_COUNT):
        try:
            resp = await _async_primary.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as exc:
            if not is_retryable_network_error(exc):
                raise
            last_err = exc
            log.warning(
                "Async skill planner network attempt %d/%d failed (%s): %s",
                attempt + 1,
                PRIMARY_RETRY_COUNT,
                _async_primary.base_url,
                exc,
            )
            if attempt < PRIMARY_RETRY_COUNT - 1:
                await asyncio.sleep(RETRY_WAIT_SECONDS)
    try:
        resp = await _async_backup.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""
    except Exception as exc:
        if not is_retryable_network_error(exc):
            raise
        raise NetworkCallError(
            stage="planner",
            model=model,
            base_url=str(_async_backup.base_url),
            attempts=PRIMARY_RETRY_COUNT,
            last_error=str(exc),
        ) from exc
