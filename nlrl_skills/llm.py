from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openai import OpenAI

from .config import LLMConfig
from .schemas import LLMMessage
from .utils import ensure_dir, extract_json_object, utc_timestamp, write_json


@dataclass
class LLMCallResult:
    text: str
    raw_response: dict[str, Any]
    request_payload: dict[str, Any]


class OpenAICompatibleLLM:
    def __init__(self, config: LLMConfig):
        self.config = config
        self.client = OpenAI(base_url=config.base_url, api_key=config.api_key, timeout=config.timeout_seconds)
        self.max_retries = 5
        self.retry_delay_seconds = 6

    def _should_retry(self, exc: Exception) -> bool:
        message = str(exc).lower()
        retry_markers = (
            "400",
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
        )
        return any(marker in message for marker in retry_markers)

    def chat(self, messages: list[LLMMessage], *, temperature: float | None = None, max_tokens: int | None = None) -> LLMCallResult:
        payload = {
            "model": self.config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.config.temperature if temperature is None else temperature,
        }
        resolved_max_tokens = self.config.max_tokens if max_tokens is None else max_tokens
        if resolved_max_tokens is not None:
            payload["max_tokens"] = resolved_max_tokens
        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                response = self.client.chat.completions.create(**payload)
                break
            except Exception as exc:  # pragma: no cover - network dependent
                last_error = exc
                if attempt >= self.max_retries or not self._should_retry(exc):
                    raise
                time.sleep(self.retry_delay_seconds)
        else:  # pragma: no cover - defensive
            raise RuntimeError(f"LLM call failed: {last_error}")
        text = ""
        if response.choices:
            message = response.choices[0].message
            text = message.content or ""
        raw_response = json.loads(response.model_dump_json())
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
