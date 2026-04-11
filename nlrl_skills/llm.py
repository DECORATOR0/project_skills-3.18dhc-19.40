from __future__ import annotations

import json
import logging
import os
import re
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

_llm_logger = logging.getLogger("nlrl_skills.llm")


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
        self.max_retries = int(os.environ.get("NLRL_LLM_MAX_RETRIES", "5"))
        self.retry_delay_seconds = float(os.environ.get("NLRL_LLM_RETRY_DELAY_SECONDS", "6"))
        self.json_repair_attempts = int(os.environ.get("NLRL_LLM_JSON_REPAIR_ATTEMPTS", "2"))
        concurrency_raw = os.environ.get("NLRL_LLM_MAX_CONCURRENT_REQUESTS", "").strip()
        self.request_semaphore = _shared_request_semaphore(int(concurrency_raw)) if concurrency_raw else None

    def _uses_responses_sse(self) -> bool:
        return self.config.api_mode.strip().lower() == "responses_sse"

    def _build_client(self) -> httpx.Client:
        headers = {"Content-Type": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        if self.disable_keepalive:
            headers["Connection"] = "close"
        limits = None
        if self.disable_keepalive:
            limits = httpx.Limits(max_connections=100, max_keepalive_connections=0)
        client_kwargs = {
            "base_url": self.config.base_url.rstrip("/") + "/",
            "timeout": self.config.timeout_seconds,
            "trust_env": False,
            "headers": headers,
        }
        if limits is not None:
            client_kwargs["limits"] = limits
        return httpx.Client(**client_kwargs)

    def _rebuild_client(self) -> None:
        try:
            self.client.close()
        except Exception:  # pragma: no cover - defensive cleanup
            pass
        self.client = self._build_client()

    def _retry_sleep(self) -> None:
        time.sleep(self.retry_delay_seconds)

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
        return True

    def _format_last_error(self, exc: Exception) -> str:
        detail = f"{type(exc).__name__}: {exc}"
        if isinstance(exc, httpx.HTTPStatusError):
            try:
                body = exc.response.text[:2000]
                detail += f"\nHTTP status: {exc.response.status_code}\nResponse body:\n{body}"
            except Exception:
                pass
        return detail

    def _raise_exhausted(self, mode: str, exc: Exception) -> None:
        detail = self._format_last_error(exc)
        _llm_logger.error(
            "[%s] %s LLM call FAILED after %d attempts.\nLast error detail:\n%s",
            self.config.name, mode, self.max_retries + 1, detail,
            exc_info=True,
        )
        raise RuntimeError(
            f"[{self.config.name}] {mode} LLM call failed after {self.max_retries + 1} attempts. "
            f"Last error: {detail}"
        ) from exc

    def _supports_enable_thinking_flag(self) -> bool:
        return "qwen" in self.config.model.lower()

    def _responses_endpoint(self) -> str:
        endpoint = self.config.base_url.rstrip("/")
        if endpoint.endswith("/responses"):
            return endpoint
        if endpoint.endswith("/v1"):
            return endpoint + "/responses"
        return endpoint

    def _build_payload(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        payload = {
            "model": self.config.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "temperature": self.config.temperature if temperature is None else temperature,
        }
        if self._supports_enable_thinking_flag() and self.config.enable_thinking is not None:
            payload["enable_thinking"] = self.config.enable_thinking
        resolved_max_tokens = self.config.max_tokens if max_tokens is None else max_tokens
        if resolved_max_tokens is not None:
            payload["max_tokens"] = resolved_max_tokens
        return payload

    def _build_responses_payload(self, messages: list[LLMMessage]) -> dict[str, Any]:
        instructions_parts = [m.content for m in messages if m.role == "system" and m.content.strip()]
        conversation_input = [
            {
                "role": m.role,
                "content": [{"type": "input_text", "text": m.content}],
            }
            for m in messages
            if m.role != "system"
        ]
        if not conversation_input:
            conversation_input = [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Follow the provided instructions."}],
                }
            ]
        payload: dict[str, Any] = {
            "model": self.config.model,
            "stream": True,
            "input": conversation_input,
        }
        if instructions_parts:
            payload["instructions"] = "\n\n".join(instructions_parts)
        return payload

    def _extract_responses_output_text(self, payload: dict[str, Any]) -> str:
        response = payload.get("response", {})
        output_text = response.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text.strip()
        for item in response.get("output", []):
            if item.get("type") != "message":
                continue
            for content in item.get("content", []):
                if content.get("type") == "output_text":
                    text = str(content.get("text", "")).strip()
                    if text:
                        return text
        raise RuntimeError("No output_text found in responses SSE response.")

    def _extract_responses_event_text(self, payload: dict[str, Any]) -> str:
        payload_type = str(payload.get("type", ""))
        if payload_type == "response.output_text.done":
            text = payload.get("text")
            if isinstance(text, str) and text.strip():
                return text.strip()
        if payload_type == "response.content_part.done":
            part = payload.get("part", {})
            if isinstance(part, dict) and part.get("type") == "output_text":
                text = part.get("text")
                if isinstance(text, str) and text.strip():
                    return text.strip()
        if payload_type == "response.output_item.done":
            item = payload.get("item", {})
            if isinstance(item, dict):
                for content in item.get("content", []):
                    if not isinstance(content, dict):
                        continue
                    if content.get("type") != "output_text":
                        continue
                    text = content.get("text")
                    if isinstance(text, str) and text.strip():
                        return text.strip()
        return ""

    def _extract_responses_error(self, payload: dict[str, Any]) -> str:
        error = payload.get("error")
        if isinstance(error, dict):
            message = error.get("message")
            if isinstance(message, str) and message.strip():
                return message
        response = payload.get("response", {})
        response_error = response.get("error")
        if isinstance(response_error, dict):
            message = response_error.get("message")
            if isinstance(message, str) and message.strip():
                return message
        return json.dumps(payload, ensure_ascii=False)

    def _parse_responses_sse_text(self, raw_text: str, request_payload: dict[str, Any]) -> LLMCallResult:
        last_payload: dict[str, Any] | None = None
        completed_payload: dict[str, Any] | None = None
        streamed_text_parts: list[str] = []
        event_text_candidates: list[str] = []
        for block in raw_text.split("\n\n"):
            lines = block.strip().splitlines()
            if not lines:
                continue
            data_lines = [line[6:] for line in lines if line.startswith("data: ")]
            if not data_lines:
                continue
            try:
                payload = json.loads("\n".join(data_lines))
            except json.JSONDecodeError:
                continue
            last_payload = payload
            payload_type = str(payload.get("type", ""))
            if payload_type == "response.output_text.delta":
                delta = payload.get("delta")
                if isinstance(delta, str) and delta:
                    streamed_text_parts.append(delta)
            else:
                candidate_text = self._extract_responses_event_text(payload)
                if candidate_text:
                    event_text_candidates.append(candidate_text)
            if payload_type == "response.completed":
                completed_payload = payload
                break
            if payload_type in {"error", "response.failed"}:
                raise RuntimeError(self._extract_responses_error(payload))
        if completed_payload is None:
            error_message = "No response.completed event found in responses SSE output."
            if last_payload is not None:
                error_message += f" Last payload: {self._extract_responses_error(last_payload)}"
            raise RuntimeError(error_message)
        try:
            text = self._extract_responses_output_text(completed_payload)
        except RuntimeError:
            text = ""
            for candidate in reversed(event_text_candidates):
                if candidate:
                    text = candidate
                    break
            if not text:
                text = "".join(streamed_text_parts).strip()
            if not text:
                raise
        text = re.sub(r"(?is)^```(?:json)?\s*|\s*```$", "", text or "").strip()
        return LLMCallResult(text=text, raw_response=completed_payload, request_payload=request_payload)

    def _responses_chat(self, payload: dict[str, Any]) -> LLMCallResult:
        with self._request_slot():
            for attempt in range(self.max_retries + 1):
                try:
                    headers = {
                        "Accept": "text/event-stream",
                        "Content-Type": "application/json",
                    }
                    if self.config.api_key:
                        headers["Authorization"] = f"Bearer {self.config.api_key}"
                    with httpx.Client(trust_env=False, timeout=float(self.config.timeout_seconds)) as client:
                        response = client.post(
                            self._responses_endpoint(),
                            headers=headers,
                            json=payload,
                        )
                        response.raise_for_status()
                    return self._parse_responses_sse_text(response.text, payload)
                except Exception as exc:
                    if attempt >= self.max_retries:
                        self._raise_exhausted("Responses-SSE", exc)
                    _llm_logger.warning(
                        "[%s] Responses-SSE attempt %d/%d failed: %s — retrying in %.1fs",
                        self.config.name, attempt + 1, self.max_retries + 1, exc, self.retry_delay_seconds,
                    )
                    self._rebuild_client()
                    self._retry_sleep()
        raise RuntimeError("Responses SSE LLM call exhausted retries without returning a response.")

    def _extract_delta_text(self, chunk: dict[str, Any]) -> str:
        choices = chunk.get("choices", [])
        if not choices:
            return ""
        choice = choices[0]
        delta = choice.get("delta", {})
        if not isinstance(delta, dict):
            delta = {}
        fragments: list[str] = []
        content = delta.get("content")
        if isinstance(content, str):
            fragments.append(content)
        elif isinstance(content, list):
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        fragments.append(text)
        if not fragments:
            message = choice.get("message", {})
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    fragments.append(content)
                elif isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict):
                            text = item.get("text")
                            if isinstance(text, str):
                                fragments.append(text)
        return "".join(fragments)

    def _chat_stream(self, payload: dict[str, Any]) -> LLMCallResult:
        stream_payload = dict(payload)
        stream_payload["stream"] = True
        chunks: list[dict[str, Any]] = []
        text_parts: list[str] = []
        finish_reason = ""
        with self._request_slot():
            for attempt in range(self.max_retries + 1):
                response = None
                try:
                    with self.client.stream("POST", "chat/completions", json=stream_payload) as response:
                        response.raise_for_status()
                        for raw_line in response.iter_lines():
                            if raw_line is None:
                                continue
                            line = raw_line.strip()
                            if not line:
                                continue
                            if isinstance(line, bytes):
                                line = line.decode("utf-8", errors="replace")
                            if not line.startswith("data:"):
                                continue
                            data = line[5:].strip()
                            if not data:
                                continue
                            if data == "[DONE]":
                                break
                            chunk = json.loads(data)
                            chunks.append(chunk)
                            text = self._extract_delta_text(chunk)
                            if text:
                                text_parts.append(text)
                            choices = chunk.get("choices", [])
                            if choices and isinstance(choices[0], dict):
                                reason = choices[0].get("finish_reason")
                                if isinstance(reason, str) and reason:
                                    finish_reason = reason
                    assembled_text = "".join(text_parts).strip()
                    if not assembled_text:
                        raise ValueError("Streaming response produced empty content.")
                    return LLMCallResult(
                        text=assembled_text,
                        raw_response={
                            "stream": True,
                            "chunk_count": len(chunks),
                            "finish_reason": finish_reason,
                            "last_chunk": chunks[-1] if chunks else {},
                        },
                        request_payload=stream_payload,
                    )
                except Exception as exc:
                    if response is not None:
                        try:
                            response.close()
                        except Exception:
                            pass
                    if attempt >= self.max_retries:
                        self._raise_exhausted("Streaming", exc)
                    _llm_logger.warning(
                        "[%s] Streaming attempt %d/%d failed: %s — retrying in %.1fs",
                        self.config.name, attempt + 1, self.max_retries + 1, exc, self.retry_delay_seconds,
                    )
                    self._rebuild_client()
                    self._retry_sleep()
        raise RuntimeError("Streaming LLM call exhausted retries without returning a response.")

    def chat(self, messages: list[LLMMessage], *, temperature: float | None = None, max_tokens: int | None = None) -> LLMCallResult:
        if self._uses_responses_sse():
            payload = self._build_responses_payload(messages)
            return self._responses_chat(payload)
        payload = self._build_payload(messages, temperature=temperature, max_tokens=max_tokens)
        if self.config.stream:
            return self._chat_stream(payload)
        last_error: Exception | None = None
        response: httpx.Response | None = None
        with self._request_slot():
            for attempt in range(self.max_retries + 1):
                try:
                    response = self.client.post("chat/completions", json=payload)
                    response.raise_for_status()
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt >= self.max_retries:
                        self._raise_exhausted("Non-streaming", exc)
                    _llm_logger.warning(
                        "[%s] Non-streaming attempt %d/%d failed: %s — retrying in %.1fs",
                        self.config.name, attempt + 1, self.max_retries + 1, exc, self.retry_delay_seconds,
                    )
                    self._rebuild_client()
                    self._retry_sleep()
        if response is None:  # pragma: no cover - defensive
            raise RuntimeError(f"LLM call failed: {last_error}")
        raw_response = response.json()
        text = ""
        choices = raw_response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            text = message.get("content") or ""
        if isinstance(text, list):
            text = "".join(
                item.get("text", "")
                for item in text
                if isinstance(item, dict)
            )
        text = re.sub(r"(?is)^```(?:json)?\s*|\s*```$", "", text or "").strip()
        return LLMCallResult(text=text, raw_response=raw_response, request_payload=payload)

    def _json_repair_prompt(self, error: Exception | str) -> str:
        return (
            "Your previous reply could not be parsed as a single valid JSON object.\n"
            f"Parser error: {error}\n"
            "Reply again with exactly one valid JSON object and nothing else.\n"
            "Requirements:\n"
            "- Keep the same intended decision/content unless the parser issue itself forces a minimal correction.\n"
            "- Do not output markdown fences or any prose before/after the JSON.\n"
            "- Do not use placeholders, Python expressions, string concatenation, or pseudo-code inside JSON.\n"
            "- Materialize every list/object/value as concrete JSON.\n"
        )

    def chat_json(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any], LLMCallResult]:
        conversation = list(messages)
        last_error: Exception | None = None

        for repair_attempt in range(self.json_repair_attempts + 1):
            result = self.chat(conversation, temperature=temperature, max_tokens=max_tokens)
            try:
                payload = extract_json_object(result.text)
                if repair_attempt and isinstance(result.raw_response, dict):
                    result.raw_response["json_repair_attempts"] = repair_attempt
                    result.raw_response["json_repair_last_error"] = "" if last_error is None else str(last_error)
                return payload, result
            except Exception as exc:
                last_error = exc
                if repair_attempt >= self.json_repair_attempts:
                    raise
                conversation = [
                    *conversation,
                    LLMMessage(role="assistant", content=result.text),
                    LLMMessage(role="user", content=self._json_repair_prompt(exc)),
                ]

        raise RuntimeError("JSON repair loop exited unexpectedly.")


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
