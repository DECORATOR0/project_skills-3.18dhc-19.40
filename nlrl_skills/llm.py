from __future__ import annotations

import json
import os
import random
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


@dataclass
class LLMCallResult:
    text: str
    raw_response: dict[str, Any]
    request_payload: dict[str, Any]
    usage: dict[str, Any] | None = None
    elapsed_seconds: float | None = None


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
        self.json_repair_attempts = int(os.environ.get("NLRL_LLM_JSON_REPAIR_ATTEMPTS", "2"))
        role_limit_name = f"NLRL_{self.config.name.upper()}_MAX_CONCURRENT_REQUESTS"
        concurrency_raw = os.environ.get(role_limit_name, "").strip() or os.environ.get("NLRL_LLM_MAX_CONCURRENT_REQUESTS", "").strip()
        self.request_semaphore = _shared_request_semaphore(int(concurrency_raw)) if concurrency_raw else None
        self.stream_include_usage = os.environ.get("NLRL_LLM_STREAM_INCLUDE_USAGE", "").strip().lower() in {"1", "true", "yes", "on"}

    def _uses_responses_sse(self) -> bool:
        return self.config.api_mode.strip().lower() == "responses_sse"

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
            if self._uses_responses_sse() and exc.response.status_code == 400:
                return True
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
            "empty content",
        )
        return any(marker in message for marker in retry_markers)

    def _supports_enable_thinking_flag(self) -> bool:
        return "qwen" in self.config.model.lower()

    def _responses_endpoint(self) -> str:
        endpoint = self.config.base_url.rstrip("/")
        if endpoint.endswith("/responses"):
            return endpoint
        if endpoint.endswith("/v1"):
            return endpoint + "/responses"
        return endpoint

    def _apply_model_limits(self, *, max_tokens: int | None) -> int | None:
        if max_tokens is None:
            return None
        model_name = self.config.model.lower()
        if "qwen3-8b" in model_name and max_tokens > 8192:
            return 8192
        return max_tokens

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
        resolved_max_tokens = self._apply_model_limits(max_tokens=resolved_max_tokens)
        if resolved_max_tokens is not None:
            payload["max_tokens"] = resolved_max_tokens
        return payload

    @staticmethod
    def _canonical_usage(raw_usage: Any) -> dict[str, Any] | None:
        if not isinstance(raw_usage, dict):
            return None
        input_tokens = raw_usage.get("prompt_tokens")
        if input_tokens is None:
            input_tokens = raw_usage.get("input_tokens")
        output_tokens = raw_usage.get("completion_tokens")
        if output_tokens is None:
            output_tokens = raw_usage.get("output_tokens")
        total_tokens = raw_usage.get("total_tokens")
        try:
            input_value = int(input_tokens) if input_tokens is not None else None
        except Exception:
            input_value = None
        try:
            output_value = int(output_tokens) if output_tokens is not None else None
        except Exception:
            output_value = None
        try:
            total_value = int(total_tokens) if total_tokens is not None else None
        except Exception:
            total_value = None
        if total_value is None and input_value is not None and output_value is not None:
            total_value = input_value + output_value
        if input_value is None and output_value is None and total_value is None:
            return None
        return {
            "input_tokens": input_value,
            "output_tokens": output_value,
            "total_tokens": total_value,
            "raw_usage": raw_usage,
        }

    def _extract_usage(self, raw_response: dict[str, Any]) -> dict[str, Any] | None:
        candidates = [
            raw_response.get("usage"),
            raw_response.get("last_chunk", {}).get("usage") if isinstance(raw_response.get("last_chunk"), dict) else None,
        ]
        response_payload = raw_response.get("response")
        if isinstance(response_payload, dict):
            candidates.append(response_payload.get("usage"))
        for candidate in candidates:
            usage = self._canonical_usage(candidate)
            if usage is not None:
                return usage
        return None

    def _inline_system_prompt_in_responses_input(self) -> bool:
        # Current SSSAI `/responses` misroutes actor/critic instructions and can
        # replace them with a Codex-style system prompt. Keep the fix scoped to
        # the JSON-heavy training roles instead of changing every responses user.
        return self.config.name in {"actor", "critic"}

    def _actor_critic_user_only_text(self, messages: list[LLMMessage]) -> str:
        parts: list[str] = [
            "Machine-only contract for this turn.",
            "Ignore any unrelated chat-assistant instructions about preambles, plans, progress updates, tool calls, or conversational tone.",
            "Do not talk to a human. Do not describe what you are about to do.",
            "Return exactly one JSON object. The first character of your reply must be `{` and the last character must be `}`.",
        ]
        for message in messages:
            content = message.content.strip()
            if not content:
                continue
            if message.role == "system":
                parts.append(f"[SYSTEM CONTRACT]\n{content}")
            elif message.role == "assistant":
                parts.append(f"[PREVIOUS INVALID REPLY]\n{content}")
            else:
                parts.append(f"[TASK INPUT]\n{content}")
        return "\n\n".join(parts)

    def _build_responses_payload(self, messages: list[LLMMessage]) -> dict[str, Any]:
        instructions_parts = [m.content for m in messages if m.role == "system" and m.content.strip()]
        if self._inline_system_prompt_in_responses_input():
            conversation_input = [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": self._actor_critic_user_only_text(messages)}],
                }
            ]
        else:
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
        if instructions_parts and not self._inline_system_prompt_in_responses_input():
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
        delta_text_parts: list[str] = []
        done_text_parts: list[str] = []
        observed_event_types: list[str] = []
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
            if payload_type:
                observed_event_types.append(payload_type)
            if payload_type == "response.output_text.delta":
                delta = payload.get("delta")
                if isinstance(delta, str) and delta:
                    delta_text_parts.append(delta)
            elif payload_type == "response.output_text.done":
                text = payload.get("text")
                if isinstance(text, str) and text:
                    done_text_parts.append(text)
            if payload_type == "response.completed":
                completed_payload = payload
                break
            if payload_type in {"error", "response.failed"}:
                raise RuntimeError(self._extract_responses_error(payload))
        if completed_payload is None:
            text = "".join(done_text_parts).strip() or "".join(delta_text_parts).strip()
            if text:
                text = re.sub(r"(?is)^```(?:json)?\s*|\s*```$", "", text).strip()
                synthetic_payload = {
                    "type": "response.completed.synthetic",
                    "incomplete_sse": True,
                    "observed_event_types": observed_event_types[-24:],
                    "last_payload": last_payload,
                }
                return LLMCallResult(
                    text=text,
                    raw_response=synthetic_payload,
                    request_payload=request_payload,
                    usage=None,
                )
            error_message = "No response.completed event found in responses SSE output."
            if last_payload is not None:
                error_message += f" Last payload: {self._extract_responses_error(last_payload)}"
            raise RuntimeError(error_message)
        try:
            text = self._extract_responses_output_text(completed_payload)
        except RuntimeError as exc:
            text = "".join(delta_text_parts).strip() or "".join(done_text_parts).strip()
            if not text:
                event_tail = observed_event_types[-12:]
                raise RuntimeError(
                    f"{exc} Observed event types: {event_tail or ['<none>']}"
                ) from exc
        text = re.sub(r"(?is)^```(?:json)?\s*|\s*```$", "", text or "").strip()
        return LLMCallResult(
            text=text,
            raw_response=completed_payload,
            request_payload=request_payload,
            usage=self._extract_usage(completed_payload),
        )

    def _responses_chat(self, payload: dict[str, Any]) -> LLMCallResult:
        with self._request_slot():
            for attempt in range(1, self.max_retries + 1):
                started = time.monotonic()
                try:
                    with httpx.Client(trust_env=False, timeout=float(self.config.timeout_seconds)) as client:
                        response = client.post(
                            self._responses_endpoint(),
                            headers={
                                "Authorization": f"Bearer {self.config.api_key}",
                                "Accept": "text/event-stream",
                                "Content-Type": "application/json",
                            },
                            json=payload,
                        )
                        response.raise_for_status()
                    result = self._parse_responses_sse_text(response.text, payload)
                    result.elapsed_seconds = round(time.monotonic() - started, 6)
                    return result
                except Exception as exc:  # pragma: no cover - network dependent
                    if attempt >= self.max_retries or not self._should_retry(exc):
                        raise
                    self._rebuild_client()
                    backoff = self.retry_delay_seconds * attempt
                    jitter = random.uniform(0, max(0.5, self.retry_delay_seconds / 2))
                    time.sleep(backoff + jitter)
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
        if self.stream_include_usage:
            stream_payload["stream_options"] = {"include_usage": True}
        chunks: list[dict[str, Any]] = []
        text_parts: list[str] = []
        finish_reason = ""
        usage: dict[str, Any] | None = None
        with self._request_slot():
            for attempt in range(1, self.max_retries + 1):
                response = None
                started = time.monotonic()
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
                            chunk_usage = self._canonical_usage(chunk.get("usage"))
                            if chunk_usage is not None:
                                usage = chunk_usage
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
                        usage=usage,
                        elapsed_seconds=round(time.monotonic() - started, 6),
                    )
                except Exception as exc:  # pragma: no cover - network dependent
                    if response is not None:
                        try:
                            response.close()
                        except Exception:
                            pass
                    if (
                        self.stream_include_usage
                        and stream_payload.get("stream_options")
                        and isinstance(exc, httpx.HTTPStatusError)
                        and exc.response is not None
                        and exc.response.status_code in {400, 422}
                    ):
                        self.stream_include_usage = False
                        stream_payload = dict(payload)
                        stream_payload["stream"] = True
                        continue
                    if attempt >= self.max_retries or not self._should_retry(exc):
                        raise
                    self._rebuild_client()
                    backoff = self.retry_delay_seconds * attempt
                    jitter = random.uniform(0, max(0.5, self.retry_delay_seconds / 2))
                    time.sleep(backoff + jitter)
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
            for attempt in range(1, self.max_retries + 1):
                started = time.monotonic()
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
        if isinstance(text, list):
            text = "".join(
                item.get("text", "")
                for item in text
                if isinstance(item, dict)
            )
        text = re.sub(r"(?is)^```(?:json)?\s*|\s*```$", "", text or "").strip()
        return LLMCallResult(
            text=text,
            raw_response=raw_response,
            request_payload=payload,
            usage=self._extract_usage(raw_response),
            elapsed_seconds=round(time.monotonic() - started, 6),
        )

    def _json_repair_prompt(self, error: Exception | str) -> str:
        return (
            "Your previous reply could not be parsed as a single valid JSON object.\n"
            f"Parser error: {error}\n"
            "Reply again with exactly one valid JSON object and nothing else.\n"
            "Requirements:\n"
            "- Keep the same intended decision/content unless the parser issue itself forces a minimal correction.\n"
            "- If the original instruction specified a JSON schema or required keys, include every required top-level key.\n"
            "- Do not output markdown fences or any prose before/after the JSON.\n"
            "- Do not use placeholders, Python expressions, string concatenation, or pseudo-code inside JSON.\n"
            "- Materialize every list/object/value as concrete JSON.\n"
        )

    def _json_repair_followup_messages(
        self,
        base_conversation: list[LLMMessage],
        *,
        previous_reply: str,
        error: Exception | str,
    ) -> list[LLMMessage]:
        repair_prompt = self._json_repair_prompt(error)
        if self._uses_responses_sse():
            # Some OpenAI-compatible Responses endpoints reject `assistant` turns in `input`.
            # Keep repair as a user-only follow-up and inline the previous invalid reply.
            return [
                *base_conversation,
                LLMMessage(
                    role="user",
                    content=(
                        "Previous invalid reply:\n"
                        f"{previous_reply}\n\n"
                        f"{repair_prompt}"
                    ),
                ),
            ]
        return [
            *base_conversation,
            LLMMessage(role="assistant", content=previous_reply),
            LLMMessage(role="user", content=repair_prompt),
        ]

    def chat_json(
        self,
        messages: list[LLMMessage],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> tuple[dict[str, Any], LLMCallResult]:
        base_conversation = list(messages)
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
                conversation = self._json_repair_followup_messages(
                    base_conversation,
                    previous_reply=result.text,
                    error=exc,
                )

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
            "request_model": str(result.request_payload.get("model", "")),
            "elapsed_seconds": result.elapsed_seconds,
            "usage": result.usage,
            "text": result.text,
            "raw_response": result.raw_response,
        },
    )
