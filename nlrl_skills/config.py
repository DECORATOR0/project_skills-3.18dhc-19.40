from __future__ import annotations

import os
from dataclasses import dataclass, field, fields, replace
from pathlib import Path
from typing import Any

from .utils import read_json


@dataclass
class LLMConfig:
    name: str
    model: str
    base_url: str
    api_key: str
    api_mode: str = "chat_completions"
    temperature: float = 0.2
    max_tokens: int | None = None
    timeout_seconds: int = 180
    enable_thinking: bool | None = None
    stream: bool = False


@dataclass
class PathsConfig:
    workspace_root: str
    prompt_root: str
    run_root: str
    skill_library_root: str
    experience_buffer_path: str
    dataset_path: str
    converted_dataset_path: str


@dataclass
class RuntimeConfig:
    max_executor_steps: int = 20
    iterations_per_batch: int = 5
    max_context_chars: int = 0
    python_executable: str = "python"
    shell_program: str = "/bin/bash"


@dataclass
class SystemConfig:
    actor: LLMConfig
    critic: LLMConfig
    executor: LLMConfig
    paths: PathsConfig
    runtime: RuntimeConfig = field(default_factory=RuntimeConfig)

    @property
    def workspace_root(self) -> Path:
        return Path(self.paths.workspace_root).resolve()

    @property
    def prompt_root(self) -> Path:
        return Path(self.paths.prompt_root).resolve()

    @property
    def run_root(self) -> Path:
        return Path(self.paths.run_root).resolve()

    @property
    def skill_library_root(self) -> Path:
        return Path(self.paths.skill_library_root).resolve()

    @property
    def experience_buffer_path(self) -> Path:
        return Path(self.paths.experience_buffer_path).resolve()

    @property
    def dataset_path(self) -> Path:
        return Path(self.paths.dataset_path).resolve()

    @property
    def converted_dataset_path(self) -> Path:
        return Path(self.paths.converted_dataset_path).resolve()


def clone_system_config(
    config: SystemConfig,
    *,
    paths: dict[str, Any] | None = None,
    runtime: dict[str, Any] | None = None,
    actor: dict[str, Any] | None = None,
    critic: dict[str, Any] | None = None,
    executor: dict[str, Any] | None = None,
) -> SystemConfig:
    path_overrides = {key: str(value) for key, value in (paths or {}).items() if value is not None}
    return replace(
        config,
        actor=replace(config.actor, **(actor or {})),
        critic=replace(config.critic, **(critic or {})),
        executor=replace(config.executor, **(executor or {})),
        paths=replace(config.paths, **path_overrides),
        runtime=replace(config.runtime, **(runtime or {})),
    )


def _env_override(*names: str) -> str | None:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return None


def _llm_from_dict(name: str, data: dict[str, Any]) -> LLMConfig:
    role_prefix = f"NLRL_{name.upper()}"
    shared_model = _env_override("NLRL_LLM_MODEL")
    shared_base_url = _env_override("NLRL_LLM_BASE_URL")
    shared_api_key = _env_override("NLRL_LLM_API_KEY")
    shared_api_mode = _env_override("NLRL_LLM_API_MODE")
    shared_temperature = _env_override("NLRL_LLM_TEMPERATURE")
    shared_timeout = _env_override("NLRL_LLM_TIMEOUT_SECONDS")
    shared_max_tokens = _env_override("NLRL_LLM_MAX_TOKENS")
    shared_enable_thinking = _env_override("NLRL_LLM_ENABLE_THINKING")
    shared_stream = _env_override("NLRL_LLM_STREAM")

    raw_max_tokens = data.get("max_tokens")
    max_tokens_override = _env_override(f"{role_prefix}_MAX_TOKENS")
    max_tokens: int | None = None
    resolved_max_tokens = max_tokens_override or shared_max_tokens
    if resolved_max_tokens is not None:
        max_tokens = int(resolved_max_tokens)
    elif raw_max_tokens is not None:
        max_tokens = int(raw_max_tokens)
    enable_thinking_raw = _env_override(f"{role_prefix}_ENABLE_THINKING") or shared_enable_thinking
    enable_thinking: bool | None = None
    if enable_thinking_raw is not None:
        enable_thinking = enable_thinking_raw.strip().lower() in {"1", "true", "yes", "on"}
    elif "enable_thinking" in data:
        value = data.get("enable_thinking")
        enable_thinking = None if value is None else bool(value)
    stream_raw = _env_override(f"{role_prefix}_STREAM") or shared_stream
    if stream_raw is not None:
        stream = stream_raw.strip().lower() in {"1", "true", "yes", "on"}
    else:
        stream = bool(data.get("stream", False))
    return LLMConfig(
        name=name,
        model=_env_override(f"{role_prefix}_MODEL") or shared_model or data["model"],
        base_url=_env_override(f"{role_prefix}_BASE_URL") or shared_base_url or data["base_url"],
        api_key=_env_override(f"{role_prefix}_API_KEY") or shared_api_key or data["api_key"],
        api_mode=_env_override(f"{role_prefix}_API_MODE") or shared_api_mode or str(data.get("api_mode", "chat_completions")),
        temperature=float(_env_override(f"{role_prefix}_TEMPERATURE") or shared_temperature or data.get("temperature", 0.2)),
        max_tokens=max_tokens,
        timeout_seconds=int(_env_override(f"{role_prefix}_TIMEOUT_SECONDS") or shared_timeout or data.get("timeout_seconds", 180)),
        enable_thinking=enable_thinking,
        stream=stream,
    )


def load_system_config(path: str | Path) -> SystemConfig:
    raw = read_json(Path(path))
    runtime_raw = dict(raw.get("runtime", {}))
    runtime_env_overrides = {
        "max_executor_steps": _env_override("NLRL_RUNTIME_MAX_EXECUTOR_STEPS"),
        "iterations_per_batch": _env_override("NLRL_RUNTIME_ITERATIONS_PER_BATCH"),
        "max_context_chars": _env_override("NLRL_RUNTIME_MAX_CONTEXT_CHARS"),
        "python_executable": _env_override("NLRL_RUNTIME_PYTHON_EXECUTABLE"),
        "shell_program": _env_override("NLRL_RUNTIME_SHELL_PROGRAM"),
    }
    for key, value in runtime_env_overrides.items():
        if value is None:
            continue
        if key in {
            "max_executor_steps",
            "iterations_per_batch",
            "max_context_chars",
        }:
            runtime_raw[key] = int(value)
        else:
            runtime_raw[key] = value
    valid_runtime_keys = {field_info.name for field_info in fields(RuntimeConfig)}
    runtime_raw = {key: value for key, value in runtime_raw.items() if key in valid_runtime_keys}
    return SystemConfig(
        actor=_llm_from_dict("actor", raw["actor"]),
        critic=_llm_from_dict("critic", raw["critic"]),
        executor=_llm_from_dict("executor", raw["executor"]),
        paths=PathsConfig(**raw["paths"]),
        runtime=RuntimeConfig(**runtime_raw),
    )
