"""
OpenAI-compatible LLM client dedicated to skill_eval.

This client is intentionally local to skill_eval so that skill evaluation can
use its own answer-selector model configuration.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Optional

import httpx
from openai import AsyncOpenAI, OpenAI

from .llm_gate import async_llm_slot, sync_llm_slot
from .network_errors import (
    NetworkCallError,
    PRIMARY_RETRY_COUNT,
    compute_retry_wait_seconds,
    is_retryable_network_error,
)
from .config import (
    ANSWER_SELECTOR_API_KEY,
    ANSWER_SELECTOR_BACKUP_BASE_URL,
    ANSWER_SELECTOR_MAX_RETRIES,
    ANSWER_SELECTOR_MAX_TOKENS,
    ANSWER_SELECTOR_MODEL_NAME,
    ANSWER_SELECTOR_PRIMARY_BASE_URL,
    ANSWER_SELECTOR_REQUEST_TIMEOUT,
)

log = logging.getLogger(__name__)


class LlmJsonResponseError(RuntimeError):
    def __init__(self, message: str, *, raw_response: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response


def _supports_enable_thinking_flag(model: str) -> bool:
    return "qwen" in model.lower()


def _build_client(base_url: str) -> OpenAI:
    return OpenAI(
        base_url=base_url,
        api_key=ANSWER_SELECTOR_API_KEY,
        timeout=ANSWER_SELECTOR_REQUEST_TIMEOUT,
        http_client=httpx.Client(timeout=ANSWER_SELECTOR_REQUEST_TIMEOUT, trust_env=False),
    )


def _build_async_client(base_url: str) -> AsyncOpenAI:
    return AsyncOpenAI(
        base_url=base_url,
        api_key=ANSWER_SELECTOR_API_KEY,
        timeout=ANSWER_SELECTOR_REQUEST_TIMEOUT,
        http_client=httpx.AsyncClient(timeout=ANSWER_SELECTOR_REQUEST_TIMEOUT, trust_env=False),
    )


_primary = _build_client(ANSWER_SELECTOR_PRIMARY_BASE_URL)
_backup = _build_client(ANSWER_SELECTOR_BACKUP_BASE_URL)
_async_primary = _build_async_client(ANSWER_SELECTOR_PRIMARY_BASE_URL)
_async_backup = _build_async_client(ANSWER_SELECTOR_BACKUP_BASE_URL)


def chat_completion(
    messages: list[dict],
    temperature: float = 0.0,
    max_tokens: int = ANSWER_SELECTOR_MAX_TOKENS,
    response_format: Optional[dict] = None,
    model: str = ANSWER_SELECTOR_MODEL_NAME,
) -> str:
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if _supports_enable_thinking_flag(model):
        kwargs["extra_body"] = {"enable_thinking": False}
    if response_format:
        kwargs["response_format"] = response_format

    last_err = None
    for attempt in range(PRIMARY_RETRY_COUNT):
        try:
            with sync_llm_slot():
                resp = _primary.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as exc:
            if not is_retryable_network_error(exc):
                raise
            last_err = exc
            log.warning(
                "Answer selector network attempt %d/%d failed (%s): %s",
                attempt + 1,
                PRIMARY_RETRY_COUNT,
                _primary.base_url,
                exc,
            )
            if attempt < PRIMARY_RETRY_COUNT - 1:
                time.sleep(compute_retry_wait_seconds(exc, attempt))
    try:
        with sync_llm_slot():
            resp = _backup.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""
    except Exception as exc:
        if not is_retryable_network_error(exc):
            raise
        raise NetworkCallError(
            stage="answer-selector",
            model=model,
            base_url=str(_backup.base_url),
            attempts=PRIMARY_RETRY_COUNT,
            last_error=str(exc),
        ) from exc


async def async_chat_completion(
    messages: list[dict],
    temperature: float = 0.0,
    max_tokens: int = ANSWER_SELECTOR_MAX_TOKENS,
    response_format: Optional[dict] = None,
    model: str = ANSWER_SELECTOR_MODEL_NAME,
) -> str:
    kwargs: dict = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if _supports_enable_thinking_flag(model):
        kwargs["extra_body"] = {"enable_thinking": False}
    if response_format:
        kwargs["response_format"] = response_format

    last_err = None
    for attempt in range(PRIMARY_RETRY_COUNT):
        try:
            async with async_llm_slot():
                resp = await _async_primary.chat.completions.create(**kwargs)
            return resp.choices[0].message.content or ""
        except Exception as exc:
            if not is_retryable_network_error(exc):
                raise
            last_err = exc
            log.warning(
                "Async answer selector network attempt %d/%d failed (%s): %s",
                attempt + 1,
                PRIMARY_RETRY_COUNT,
                _async_primary.base_url,
                exc,
            )
            if attempt < PRIMARY_RETRY_COUNT - 1:
                await asyncio.sleep(compute_retry_wait_seconds(exc, attempt))
    try:
        async with async_llm_slot():
            resp = await _async_backup.chat.completions.create(**kwargs)
        return resp.choices[0].message.content or ""
    except Exception as exc:
        if not is_retryable_network_error(exc):
            raise
        raise NetworkCallError(
            stage="answer-selector",
            model=model,
            base_url=str(_async_backup.base_url),
            attempts=PRIMARY_RETRY_COUNT,
            last_error=str(exc),
        ) from exc


def chat_json(
    messages: list[dict],
    temperature: float = 0.0,
    max_tokens: int = ANSWER_SELECTOR_MAX_TOKENS,
    model: str = ANSWER_SELECTOR_MODEL_NAME,
) -> dict:
    data, _ = chat_json_with_raw(
        messages,
        temperature=temperature,
        max_tokens=max_tokens,
        model=model,
    )
    return data


def chat_json_with_raw(
    messages: list[dict],
    temperature: float = 0.0,
    max_tokens: int = ANSWER_SELECTOR_MAX_TOKENS,
    model: str = ANSWER_SELECTOR_MODEL_NAME,
) -> tuple[dict, str]:
    last_err = None
    last_raw = ""
    for _ in range(ANSWER_SELECTOR_MAX_RETRIES):
        raw = chat_completion(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            model=model,
        )
        last_raw = raw
        try:
            return _parse_json(raw), raw
        except Exception as exc:
            last_err = exc
            log.warning("Skill-eval answer selector JSON parse failed, retrying generation: %s", exc)
            time.sleep(1)
    raise LlmJsonResponseError(
        f"Failed to parse skill-eval answer selector JSON after retries. Last error: {last_err}",
        raw_response=last_raw,
    )


async def async_chat_json_with_raw(
    messages: list[dict],
    temperature: float = 0.0,
    max_tokens: int = ANSWER_SELECTOR_MAX_TOKENS,
    model: str = ANSWER_SELECTOR_MODEL_NAME,
) -> tuple[dict, str]:
    last_err = None
    last_raw = ""
    for _ in range(ANSWER_SELECTOR_MAX_RETRIES):
        raw = await async_chat_completion(
            messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"},
            model=model,
        )
        last_raw = raw
        try:
            return _parse_json(raw), raw
        except Exception as exc:
            last_err = exc
            log.warning("Skill-eval answer selector async JSON parse failed, retrying generation: %s", exc)
            await asyncio.sleep(1)
    raise LlmJsonResponseError(
        f"Failed to parse async skill-eval answer selector JSON after retries. Last error: {last_err}",
        raw_response=last_raw,
    )


def _parse_json(raw: str) -> dict:
    raw = (raw or "").strip()
    if not raw:
        raise ValueError("Empty response content")
    if raw.startswith("```"):
        lines = raw.splitlines()
        start = 1 if lines and lines[0].strip().startswith("```") else 0
        end = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "```":
                end = i
                break
        raw = "\n".join(lines[start:end]).strip()
    try:
        return json.loads(raw)
    except Exception:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise
