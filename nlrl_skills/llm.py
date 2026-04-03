from __future__ import annotations

import json
import os
import random
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from threading import BoundedSemaphore, Lock
from typing import Any

import httpx

from .config import LLMConfig
from .schemas import LLMMessage
from .utils import ensure_dir, extract_json_object, utc_timestamp, write_json


@dataclass
class LLMCallResult:
    text: str
    raw_response: dict[str, Any]
    request_payload: dict[str, Any]


_SEMAPHORE_LOCK = Lock()
_REQUEST_SEMAPHORES: dict[int, BoundedSemaphore] = {}


def _shared_request_semaphore(limit: int) -> BoundedSemaphore:
    with _SEMAPHORE_LOCK:
        semaphore = _REQUEST_SEMAPHORES.get(limit)
        if semaphore is None:
            semaphore = BoundedSemaphore(limit)
            _REQUEST_SEMAPHORES[limit] = semaphore
        return semaphore


class OpenAICompatibleLLM:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.disable_keepalive = os.environ.get("NLRL_LLM_DISABLE_KEEPALIVE", "").strip().lower() in {"1", "true", "yes", "on"}
        self.client = self._build_client()
        self.max_retries = int(os.environ.get("NLRL_LLM_MAX_RETRIES", "6"))
        self.retry_delay_seconds = float(os.environ.get("NLRL_LLM_RETRY_DELAY_SECONDS", "6"))
        concurrency_raw = os.environ.get("NLRL_LLM_MAX_CONCURRENT_REQUESTS", "").strip()
        self.request_semaphore = _shared_request_semaphore(int(concurrency_raw)) if concurrency_raw else None

    def _build_client(self) -> httpx.Client:
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        if self.disable_keepalive:
            headers["Connection"] = "close"
        limits = None
        if self.disable_keepalive:
            limits = httpx.Limits(max_connections=100, max_keepalive_connections=0)
        return httpx.Client(
            base_url=self.config.base_url.rstrip("/") + "/",
            timeout=self.config.timeout_seconds,
            trust_env=False,
            headers=headers,
            limits=limits,
        )

    def _rebuild_client(self) -> None:
        try:
            self.client.close()
        except Exception:  # pragma: no cover - defensive cleanup
            pass
        self.client = self._build_client()

    @contextmanager
    def _request_slot(self):
        if self.request_semaphore is None:
            yield
            return
        self.request_semaphore.acquire()
        try:
            yield
        finally:
            self.request_semaphore.release()

    def _should_retry(self, exc: Exception) -> bool:
        if isinstance(exc, httpx.HTTPStatusError):
            return exc.response.status_code in {408, 409, 429, 500, 502, 503, 504}
        if isinstance(
            exc,
            (
                httpx.TimeoutException,
                httpx.NetworkError,
                httpx.ReadError,
                httpx.WriteError,
                httpx.ConnectError,
                httpx.RemoteProtocolError,
            ),
        ):
            return True
        message = str(exc).lower()
        retry_markers = (
            "408",
            "409",
            "429",
            "500",
            "502",
            "503",
            "504",
            "bad gateway",
            "timeout",
            "timed out",
            "connection",
            "network",
            "temporarily unavailable",
            "bad_response_status_code",
            "server disconnected without sending a response",
            "remoteprotocolerror",
            "disconnected",
        )
        return any(marker in message for marker in retry_markers)

    def _supports_enable_thinking_flag(self) -> bool:
        return "qwen" in self.config.model.lower()

    def chat(self, messages: list[LLMMessage], *, temperature: float | None = None, max_tokens: int | None = None) -> LLMCallResult:
        payload = {
            "model": self.config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.config.temperature if temperature is None else temperature,
        }
        if self._supports_enable_thinking_flag():
            payload["enable_thinking"] = False
        resolved_max_tokens = self.config.max_tokens if max_tokens is None else max_tokens
        if resolved_max_tokens is not None:
            payload["max_tokens"] = resolved_max_tokens
        last_error: Exception | None = None
        response: httpx.Response | None = None
        with self._request_slot():
            for attempt in range(1, self.max_retries + 1):
                try:
                    response = self.client.post("chat/completions", json=payload)
                    response.raise_for_status()
                    break
                except Exception as exc:  # pragma: no cover - network dependent
                    last_error = exc
                    if attempt >= self.max_retries or not self._should_retry(exc):
                        raise
                    self._rebuild_client()
                    backoff = self.retry_delay_seconds * attempt
                    jitter = random.uniform(0, max(0.5, self.retry_delay_seconds / 2))
                    time.sleep(backoff + jitter)
        if response is None:  # pragma: no cover - defensive
            raise RuntimeError(f"LLM call failed: {last_error}")
        raw_response = response.json()
        text = ""
        choices = raw_response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            text = message.get("content") or ""
        return LLMCallResult(text=text, raw_response=raw_response, request_payload=payload)

    def chat_json(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any], LLMCallResult]:
        result = self.chat(messages, temperature=temperature, max_tokens=max_tokens)
        return extract_json_object(result.text), result


def log_llm_call(log_dir: Path, role_name: str, result: LLMCallResult) -> None:
    ensure_dir(log_dir)
    stamp = utc_timestamp()
    write_json(
        log_dir / f"{stamp}_{role_name}_request.json",
        result.request_payload,
    )
    write_json(
        log_dir / f"{stamp}_{role_name}_response.json",
        {
            "text": result.text,
            "raw_response": result.raw_response,
        },
    )
