"""
Sequential parameter-selection worker for skill-based execution.
"""
from __future__ import annotations

import ast
import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any

import httpx
from openai import APIStatusError, OpenAI

from . import config as skill_eval_config
from .config import (
    MAX_WORKER_STEPS,
    PARAMETER_MODEL_API_KEY,
    PARAMETER_MODEL_BACKUP_URL,
    PARAMETER_MODEL_BASE_URL,
    PARAMETER_MODEL_NAME,
    PARAMETER_REQUEST_TIMEOUT,
)
from .llm_gate import sync_llm_slot
from .network_errors import (
    NetworkCallError,
    PRIMARY_RETRY_COUNT,
    compute_retry_wait_seconds,
    is_retryable_network_error,
)
from .prompt_builder import worker_system_prompt
from .tool_catalog import ToolMeta, build_catalog

log = logging.getLogger(__name__)
_MISSING = object()
PARAMETER_MODEL_CONTEXT_WINDOW = getattr(skill_eval_config, "PARAMETER_MODEL_CONTEXT_WINDOW", _MISSING)
PARAMETER_MODEL_ENABLE_THINKING = getattr(skill_eval_config, "PARAMETER_MODEL_ENABLE_THINKING", _MISSING)
_TOOL_MAP: dict[str, ToolMeta] | None = None


class WorkerRequestError(RuntimeError):
    def __init__(self, message: str, *, raw_response: str = "", cleaned_response: str = "") -> None:
        super().__init__(message)
        self.raw_response = raw_response
        self.cleaned_response = cleaned_response


@dataclass
class WorkerDecision:
    tool_name: str
    arguments: dict[str, Any]
    rationale: str
    raw_response: str = ""
    cleaned_response: str = ""
    model: str = ""


def _client(base_url: str) -> OpenAI:
    return OpenAI(
        base_url=base_url,
        api_key=PARAMETER_MODEL_API_KEY,
        timeout=PARAMETER_REQUEST_TIMEOUT,
        http_client=httpx.Client(timeout=PARAMETER_REQUEST_TIMEOUT, trust_env=False),
    )


def _supports_enable_thinking_flag(model: str) -> bool:
    return "qwen" in model.lower()


_primary = _client(PARAMETER_MODEL_BASE_URL)
_backup = _client(PARAMETER_MODEL_BACKUP_URL)


def _strip_qwen_think_blocks(raw: str) -> str:
    raw = raw or ""
    # Qwen-style responses may prepend hidden reasoning in <think>...</think>.
    raw = re.sub(r"(?is)<think>.*?</think>\s*", "", raw).strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        start = 1 if lines and lines[0].strip().startswith("```") else 0
        end = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip() == "```":
                end = i
                break
        raw = "\n".join(lines[start:end]).strip()
    return raw


def _is_bad_request(exc: Exception) -> bool:
    return isinstance(exc, APIStatusError) and exc.status_code == 400


def _primitive_json_schema(type_text: str) -> dict[str, Any]:
    t = type_text.strip().lower()
    if t in {"str", "string"}:
        return {"type": "string"}
    if t in {"int", "integer"}:
        return {"type": "integer"}
    if t in {"float", "number"}:
        return {"type": "number"}
    if t in {"bool", "boolean"}:
        return {"type": "boolean"}
    if t in {"list", "array"}:
        return {"type": "array"}
    return {}


def _json_schema_from_type(type_text: str) -> dict[str, Any]:
    t = (type_text or "any").strip()
    lower = t.lower()
    if "|" in t:
        parts = [p.strip() for p in t.split("|")]
        schemas = [_json_schema_from_type(p) for p in parts if p.strip() and p.strip().lower() != "none"]
        schemas = [s for s in schemas if s]
        if len(schemas) == 1:
            return schemas[0]
        if schemas:
            return {"anyOf": schemas}
        return {}
    if lower.startswith("list[") and lower.endswith("]"):
        inner = t[t.find("[") + 1 : -1].strip()
        item_schema = _json_schema_from_type(inner) or {"type": "string"}
        return {"type": "array", "items": item_schema}
    return _primitive_json_schema(lower)


def _tool_map() -> dict[str, ToolMeta]:
    global _TOOL_MAP
    if _TOOL_MAP is None:
        _TOOL_MAP = {tool.canonical_name: tool for tool in build_catalog()}
    return _TOOL_MAP


def _tool_call_schema(tool_name: str) -> list[dict[str, Any]] | None:
    tool = _tool_map().get(tool_name)
    if tool is None:
        return None
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in tool.parameters:
        name = param["name"]
        schema = _json_schema_from_type(param.get("type", "any"))
        if "default" in param:
            schema["default"] = param["default"]
        else:
            required.append(name)
        properties[name] = schema
    return [
        {
            "type": "function",
            "function": {
                "name": tool.canonical_name,
                "description": tool.short_description(220),
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
    ]


def _build_request_kwargs(
    user_prompt: str,
    model: str,
    *,
    compatibility_mode: bool,
    planned_tool_name: str | None,
    use_tool_calling: bool,
) -> dict[str, Any]:
    system_content = (
        worker_system_prompt()
        + (
            "\n\nUse the provided tool-calling interface to call exactly one tool with well-formed arguments."
            if use_tool_calling
            else "\n\nReturn exactly one next tool decision."
              "\nUse this plain-text format exactly:"
              "\nTOOL_NAME: <tool name>"
              "\nARGUMENTS_JSON:"
              "\n{\"arg_name\": \"arg_value\"}"
              "\nRATIONALE: <short rationale>"
              "\nThe ARGUMENTS_JSON block must be a valid JSON object."
        )
    )
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_content},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
    }
    if PARAMETER_MODEL_ENABLE_THINKING is not _MISSING and _supports_enable_thinking_flag(model):
        kwargs["extra_body"] = {"enable_thinking": PARAMETER_MODEL_ENABLE_THINKING}
    if use_tool_calling and planned_tool_name:
        schema = _tool_call_schema(planned_tool_name)
        if schema:
            kwargs["tools"] = schema
            kwargs["tool_choice"] = {
                "type": "function",
                "function": {"name": planned_tool_name},
            }
    if not compatibility_mode:
        if PARAMETER_MODEL_CONTEXT_WINDOW is not _MISSING and PARAMETER_MODEL_CONTEXT_WINDOW != -1:
            kwargs["max_tokens"] = PARAMETER_MODEL_CONTEXT_WINDOW
    return kwargs


def _parse_tagged_worker_payload(cleaned: str) -> dict[str, Any]:
    block_match = re.search(
        r"(?is)TOOL_NAME:\s*(?P<tool>.+?)\s*[\r\n]+ARGUMENTS_JSON:\s*(?P<args>\{.*?\})\s*[\r\n]+RATIONALE:\s*(?P<rationale>.*?)(?=(?:[\r\n]+TOOL_NAME:)|\Z)",
        cleaned,
    )
    if not block_match:
        raise ValueError("Tagged worker payload missing TOOL_NAME or ARGUMENTS_JSON")

    tool_name = block_match.group("tool").strip()
    arguments_text = block_match.group("args").strip()
    rationale = block_match.group("rationale").strip()

    if not tool_name or not arguments_text:
        raise ValueError("Tagged worker payload missing TOOL_NAME or ARGUMENTS_JSON")

    try:
        arguments = json.loads(arguments_text)
    except Exception:
        arguments = ast.literal_eval(arguments_text)
    if not isinstance(arguments, dict):
        raise TypeError(f"Tagged worker arguments must decode to dict, got {type(arguments).__name__}")

    return {
        "tool_name": tool_name,
        "arguments": arguments,
        "rationale": rationale,
    }


def _parse_tool_call_response(resp: Any, planned_tool_name: str | None) -> dict[str, Any] | None:
    message = resp.choices[0].message
    tool_calls = getattr(message, "tool_calls", None) or []
    if not tool_calls:
        return None
    tc = tool_calls[0]
    function = getattr(tc, "function", None)
    if function is None:
        return None
    tool_name = getattr(function, "name", None) or planned_tool_name or ""
    arguments_text = getattr(function, "arguments", "") or "{}"
    try:
        arguments = json.loads(arguments_text)
    except Exception:
        arguments = ast.literal_eval(arguments_text)
    if not isinstance(arguments, dict):
        raise TypeError(f"Tool-call arguments must be a dict, got {type(arguments).__name__}")
    rationale = getattr(message, "content", "") or ""
    return {
        "tool_name": tool_name,
        "arguments": arguments,
        "rationale": rationale,
        "raw_response": json.dumps(
            {
                "content": getattr(message, "content", "") or "",
                "tool_calls": [
                    {
                        "name": getattr(function, "name", "") or "",
                        "arguments": arguments_text,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        "cleaned_response": arguments_text,
    }


def _parse_worker_payload(raw: str) -> dict[str, Any]:
    cleaned = _strip_qwen_think_blocks(raw)
    if not cleaned:
        raise ValueError("Empty worker response after stripping <think> blocks")
    try:
        return json.loads(cleaned)
    except Exception as json_err:
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            candidate = match.group(0)
            try:
                return json.loads(candidate)
            except Exception:
                pass
            # Common Qwen local-server issue: Windows paths emitted with single
            # backslashes, which breaks strict JSON parsing.
            try:
                return json.loads(candidate.replace("\\", "\\\\"))
            except Exception:
                pass
            try:
                return ast.literal_eval(candidate)
            except Exception:
                pass
        try:
            return _parse_tagged_worker_payload(cleaned)
        except Exception as tagged_err:
            raise ValueError(f"Failed to parse worker payload. Raw cleaned content: {cleaned[:1200]}") from tagged_err


def _normalize_worker_payload(data: dict[str, Any]) -> dict[str, Any]:
    tool_name = data.get("tool_name")
    arguments = data.get("arguments")
    rationale = data.get("rationale", "")

    if tool_name is None:
        tool_name = data.get("name") or data.get("tool") or data.get("action")

    if tool_name is None and isinstance(data.get("function"), dict):
        tool_name = data["function"].get("name")
        if arguments is None:
            arguments = data["function"].get("arguments")

    if tool_name is None and isinstance(data.get("tool_call"), dict):
        tc = data["tool_call"]
        tool_name = tc.get("tool_name") or tc.get("name") or tc.get("tool")
        if arguments is None:
            arguments = tc.get("arguments")
        if not rationale:
            rationale = tc.get("rationale", "") or tc.get("reason", "")

    if arguments is None:
        arguments = data.get("args", {})
    if not rationale:
        rationale = data.get("reason", "") or data.get("why", "")

    if not tool_name:
        raise KeyError(f"tool_name not found in worker payload keys: {sorted(data.keys())}")
    if not isinstance(arguments, dict):
        raise TypeError(f"Worker arguments must be a dict, got {type(arguments).__name__}")

    return {
        "tool_name": tool_name,
        "arguments": arguments,
        "rationale": rationale,
    }


def _request_worker_payload(user_prompt: str, *, model: str, planned_tool_name: str | None = None) -> dict[str, Any]:
    last_err = None
    last_raw = ""
    last_cleaned = ""
    for attempt in range(PRIMARY_RETRY_COUNT):
        raw_content = ""
        cleaned_content = ""
        try:
            kwargs = _build_request_kwargs(
                user_prompt,
                model,
                compatibility_mode=False,
                planned_tool_name=planned_tool_name,
                use_tool_calling=True,
            )
            try:
                with sync_llm_slot():
                    resp = _primary.chat.completions.create(**kwargs)
            except Exception as exc:
                if not _is_bad_request(exc):
                    raise
                log.warning(
                    "Parameter worker got 400 with tool-calling/optional args on primary endpoint; retrying in compatibility mode."
                )
                with sync_llm_slot():
                    resp = _primary.chat.completions.create(
                        **_build_request_kwargs(
                            user_prompt,
                            model,
                            compatibility_mode=True,
                            planned_tool_name=planned_tool_name,
                            use_tool_calling=True,
                        )
                    )
            tool_call_payload = _parse_tool_call_response(resp, planned_tool_name)
            if tool_call_payload is not None:
                normalized = _normalize_worker_payload(tool_call_payload)
                normalized["raw_response"] = tool_call_payload.get("raw_response", "")
                normalized["cleaned_response"] = tool_call_payload.get("cleaned_response", "")
                normalized["model"] = model
                return normalized
            content = resp.choices[0].message.content or ""
            raw_content = content
            cleaned_content = _strip_qwen_think_blocks(content)
            normalized = _normalize_worker_payload(_parse_worker_payload(content))
            normalized["raw_response"] = content
            normalized["cleaned_response"] = cleaned_content
            normalized["model"] = model
            return normalized
        except Exception as exc:
            if raw_content:
                last_raw = raw_content
            if cleaned_content:
                last_cleaned = cleaned_content
            if not is_retryable_network_error(exc):
                raise
            last_err = exc
            log.warning(
                "Parameter worker network attempt %d/%d failed (%s): %s",
                attempt + 1,
                PRIMARY_RETRY_COUNT,
                _primary.base_url,
                exc,
            )
            if attempt < PRIMARY_RETRY_COUNT - 1:
                time.sleep(compute_retry_wait_seconds(exc, attempt))
    raw_content = ""
    cleaned_content = ""
    try:
        kwargs = _build_request_kwargs(
            user_prompt,
            model,
            compatibility_mode=False,
            planned_tool_name=planned_tool_name,
            use_tool_calling=True,
        )
        try:
            with sync_llm_slot():
                resp = _backup.chat.completions.create(**kwargs)
        except Exception as exc:
            if not _is_bad_request(exc):
                raise
            log.warning(
                "Parameter worker got 400 with tool-calling/optional args on backup endpoint; retrying in compatibility mode."
            )
            with sync_llm_slot():
                resp = _backup.chat.completions.create(
                    **_build_request_kwargs(
                        user_prompt,
                        model,
                        compatibility_mode=True,
                        planned_tool_name=planned_tool_name,
                        use_tool_calling=True,
                    )
                )
        tool_call_payload = _parse_tool_call_response(resp, planned_tool_name)
        if tool_call_payload is not None:
            normalized = _normalize_worker_payload(tool_call_payload)
            normalized["raw_response"] = tool_call_payload.get("raw_response", "")
            normalized["cleaned_response"] = tool_call_payload.get("cleaned_response", "")
            normalized["model"] = model
            return normalized
        content = resp.choices[0].message.content or ""
        raw_content = content
        cleaned_content = _strip_qwen_think_blocks(content)
        normalized = _normalize_worker_payload(_parse_worker_payload(content))
        normalized["raw_response"] = content
        normalized["cleaned_response"] = cleaned_content
        normalized["model"] = model
        return normalized
    except Exception as exc:
        if raw_content:
            last_raw = raw_content
        if cleaned_content:
            last_cleaned = cleaned_content
        if is_retryable_network_error(exc):
            raise NetworkCallError(
                stage="parameter-worker",
                model=model,
                base_url=str(_backup.base_url),
                attempts=PRIMARY_RETRY_COUNT,
                last_error=str(exc),
                raw_response=last_raw,
                cleaned_response=last_cleaned,
            ) from exc
        raise WorkerRequestError(
            f"Parameter worker failed after primary retries and backup attempt. Last error: {exc}",
            raw_response=last_raw,
            cleaned_response=last_cleaned,
        ) from exc


def choose_next_tool_call(user_prompt: str, *, model: str = PARAMETER_MODEL_NAME) -> WorkerDecision:
    data = _request_worker_payload(user_prompt, model=model, planned_tool_name=None)
    return WorkerDecision(
        tool_name=data["tool_name"],
        arguments=data.get("arguments", {}),
        rationale=data.get("rationale", ""),
        raw_response=data.get("raw_response", ""),
        cleaned_response=data.get("cleaned_response", ""),
        model=data.get("model", model),
    )


def choose_tool_arguments(
    user_prompt: str,
    *,
    planned_tool_name: str,
    model: str = PARAMETER_MODEL_NAME,
) -> WorkerDecision:
    data = _request_worker_payload(user_prompt, model=model, planned_tool_name=planned_tool_name)
    # The plan is fixed by the skill planner; use the returned args/reason but
    # pin the actual tool name to the planned next step.
    return WorkerDecision(
        tool_name=planned_tool_name,
        arguments=data.get("arguments", {}),
        rationale=data.get("rationale", ""),
        raw_response=data.get("raw_response", ""),
        cleaned_response=data.get("cleaned_response", ""),
        model=data.get("model", model),
    )


def worker_response_schema_text() -> str:
    return json.dumps(
        {
            "tool_name": "exact tool name",
            "arguments": {"arg_name": "arg_value"},
            "rationale": "why this is the next step",
        },
        ensure_ascii=False,
        indent=2,
    )


def max_worker_steps() -> int:
    return MAX_WORKER_STEPS
