from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .utils import ensure_dir, read_text, safe_relative_path, write_text

EO_TOOL_FILES = ["Index.py", "Inversion.py", "Perception.py", "Analysis.py", "Statistics.py"]


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    callable: Callable[..., Any]
    source: str

    def prompt_entry(self) -> str:
        return json.dumps(
            {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
                "source": self.source,
            },
            ensure_ascii=False,
        )


def _truncate(value: Any, limit: int = 4000) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return text[:limit]


class EOToolRuntime:
    def __init__(self, workspace_root: Path, temp_root: Path):
        self.workspace_root = workspace_root
        self.tools_dir = workspace_root / "agent" / "tools"
        self.temp_root = ensure_dir(temp_root)
        self._registry: dict[str, ToolSpec] = {}
        self._load_all()

    def _parse_tool_nodes(self, path: Path) -> list[tuple[str, str, dict[str, Any]]]:
        tree = ast.parse(read_text(path))
        parsed: list[tuple[str, str, dict[str, Any]]] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            decorator_src = " ".join(ast.unparse(d) for d in node.decorator_list)
            if "mcp.tool" not in decorator_src:
                continue
            description = ""
            for dec in node.decorator_list:
                if isinstance(dec, ast.Call):
                    for kw in dec.keywords:
                        if kw.arg == "description" and isinstance(kw.value, ast.Constant):
                            description = str(kw.value.value).strip()
            params: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
            for arg in node.args.args:
                if arg.arg == "self":
                    continue
                params["properties"][arg.arg] = {
                    "type": "string",
                    "description": f"Argument {arg.arg}",
                }
            default_count = len(node.args.defaults)
            required_count = len(node.args.args) - default_count
            for idx, arg in enumerate(node.args.args):
                if arg.arg == "self":
                    continue
                if idx < required_count:
                    params["required"].append(arg.arg)
            parsed.append((node.name, description, params))
        return parsed

    def _load_module(self, module_file: str) -> Any:
        module_path = self.tools_dir / module_file
        module_name = f"nlrl_runtime_{module_path.stem.lower()}_{abs(hash(str(module_path))) % 100000}"
        old_argv = sys.argv[:]
        try:
            sys.argv = [str(module_path), "--temp_dir", str(self.temp_root / module_path.stem.lower())]
            spec = importlib.util.spec_from_file_location(module_name, module_path)
            if spec is None or spec.loader is None:
                raise RuntimeError(f"Cannot load {module_path}")
            module = importlib.util.module_from_spec(spec)
            if str(self.tools_dir) not in sys.path:
                sys.path.insert(0, str(self.tools_dir))
            spec.loader.exec_module(module)
            return module
        finally:
            sys.argv = old_argv

    def _load_all(self) -> None:
        for module_file in EO_TOOL_FILES:
            source_path = self.tools_dir / module_file
            if not source_path.exists():
                continue
            parsed = {name: (desc, schema) for name, desc, schema in self._parse_tool_nodes(source_path)}
            module = self._load_module(module_file)
            for name, value in vars(module).items():
                if name not in parsed or not inspect.isfunction(value):
                    continue
                desc, schema = parsed[name]
                self._registry[name] = ToolSpec(
                    name=name,
                    description=desc or f"EO tool {name}",
                    parameters=schema,
                    callable=value,
                    source=f"agent/tools/{module_file}",
                )

    def specs(self) -> list[ToolSpec]:
        return [self._registry[name] for name in sorted(self._registry)]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name not in self._registry:
            raise KeyError(f"Unknown EO tool: {tool_name}")
        func = self._registry[tool_name].callable
        accepted: dict[str, Any] = {}
        sig = inspect.signature(func)
        for param_name, param in sig.parameters.items():
            if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
                if param_name in arguments:
                    accepted[param_name] = arguments[param_name]
        return func(**accepted)


@dataclass
class ToolContext:
    workspace_root: Path
    skill_library_root: Path
    temp_root: Path
    python_executable: str = "python"
    shell_program: str = "powershell"
    eo_runtime: EOToolRuntime | None = None
    active_skill_dir: Path | None = None

    def __post_init__(self) -> None:
        ensure_dir(self.workspace_root)
        ensure_dir(self.skill_library_root)
        ensure_dir(self.temp_root)
        if self.eo_runtime is None:
            self.eo_runtime = EOToolRuntime(self.workspace_root, self.temp_root / "eo_runtime")


class Toolbox:
    def __init__(self, context: ToolContext):
        self.context = context
        self._registry: dict[str, ToolSpec] = {}
        self._register_builtin_tools()
        for spec in self.context.eo_runtime.specs():
            self._registry[spec.name] = ToolSpec(
                name=spec.name,
                description=spec.description,
                parameters=spec.parameters,
                callable=self._wrap_eo_tool(spec.name),
                source=spec.source,
            )

    def _register(self, spec: ToolSpec) -> None:
        self._registry[spec.name] = spec

    def set_active_skill_dir(self, skill_dir: str | Path | None) -> None:
        self.context.active_skill_dir = None if skill_dir is None else Path(skill_dir).resolve()

    def _resolve_workspace_path(self, user_path: str, *, prefer_existing: bool = True) -> Path:
        normalized = user_path.replace("\\", "/").strip()
        skill_dir = self.context.active_skill_dir
        skill_relative_prefixes = ("scripts", "references", "assets", "SKILL.md")
        if skill_dir is not None and (
            normalized == "SKILL.md"
            or normalized in {"scripts", "references", "assets"}
            or normalized.startswith("scripts/")
            or normalized.startswith("references/")
            or normalized.startswith("assets/")
        ):
            skill_target = safe_relative_path(skill_dir, normalized)
            if skill_target.exists() or not prefer_existing:
                return skill_target
        return safe_relative_path(self.context.workspace_root, normalized)

    def _wrap_eo_tool(self, tool_name: str) -> Callable[..., Any]:
        def _call(**kwargs: Any) -> Any:
            return self.context.eo_runtime.execute(tool_name, kwargs)

        return _call

    def _register_builtin_tools(self) -> None:
        self._register(
            ToolSpec(
                name="list_dir",
                description="List files and directories under a workspace-relative path.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": [],
                },
                callable=self.list_dir,
                source="built_in",
            )
        )
        self._register(
            ToolSpec(
                name="read_file",
                description="Read a UTF-8 text file from the workspace.",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                callable=self.read_file,
                source="built_in",
            )
        )
        self._register(
            ToolSpec(
                name="write_file",
                description="Write a UTF-8 text file under the workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
                callable=self.write_file,
                source="built_in",
            )
        )
        self._register(
            ToolSpec(
                name="replace_in_file",
                description="Replace one exact string occurrence in a workspace file.",
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
                callable=self.replace_in_file,
                source="built_in",
            )
        )
        self._register(
            ToolSpec(
                name="glob_search",
                description="Glob files under the workspace using a relative pattern like benchmark/**/*.json.",
                parameters={
                    "type": "object",
                    "properties": {"pattern": {"type": "string"}},
                    "required": ["pattern"],
                },
                callable=self.glob_search,
                source="built_in",
            )
        )
        self._register(
            ToolSpec(
                name="run_shell",
                description="Run a non-interactive shell command from the workspace root.",
                parameters={
                    "type": "object",
                    "properties": {"command": {"type": "string"}},
                    "required": ["command"],
                },
                callable=self.run_shell,
                source="built_in",
            )
        )
        self._register(
            ToolSpec(
                name="run_python_script",
                description="Run a Python script under the workspace with optional string arguments.",
                parameters={
                    "type": "object",
                    "properties": {
                        "script_path": {"type": "string"},
                        "args": {"type": "array"},
                    },
                    "required": ["script_path"],
                },
                callable=self.run_python_script,
                source="built_in",
            )
        )

    def specs(self, allowed_tools: list[str] | None = None) -> list[ToolSpec]:
        if not allowed_tools:
            return [self._registry[name] for name in sorted(self._registry)]
        allowed = set(allowed_tools)
        return [spec for name, spec in sorted(self._registry.items()) if name in allowed]

    def execute(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if tool_name not in self._registry:
            raise KeyError(f"Unknown tool: {tool_name}")
        return self._registry[tool_name].callable(**arguments)

    def tool_prompt(self, allowed_tools: list[str] | None = None) -> str:
        return "\n".join(spec.prompt_entry() for spec in self.specs(allowed_tools))

    def list_dir(self, path: str = ".") -> list[str]:
        target = self._resolve_workspace_path(path)
        if not target.exists():
            raise FileNotFoundError(f"Path does not exist: {path}")
        return sorted(p.name for p in target.iterdir())

    def read_file(self, path: str) -> str:
        target = self._resolve_workspace_path(path)
        return read_text(target)

    def write_file(self, path: str, content: str) -> str:
        target = self._resolve_workspace_path(path, prefer_existing=False)
        write_text(target, content)
        return f"Wrote {target}"

    def replace_in_file(self, path: str, old_text: str, new_text: str) -> str:
        target = self._resolve_workspace_path(path)
        source = read_text(target)
        if old_text not in source:
            raise ValueError("old_text was not found in file.")
        updated = source.replace(old_text, new_text, 1)
        write_text(target, updated)
        return f"Updated {target}"

    def glob_search(self, pattern: str) -> list[str]:
        return sorted(
            str(path.relative_to(self.context.workspace_root)).replace("\\", "/")
            for path in self.context.workspace_root.glob(pattern)
        )

    def _shell_command(self, command: str) -> list[str]:
        shell = self.context.shell_program.strip() or "/bin/bash"
        shell_name = Path(shell).name.lower()
        if "powershell" in shell_name or shell_name == "pwsh":
            return [shell, "-Command", command]
        return [shell, "-lc", command]

    def run_shell(self, command: str) -> dict[str, Any]:
        completed = subprocess.run(
            self._shell_command(command),
            cwd=self.context.workspace_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout": _truncate(completed.stdout),
            "stderr": _truncate(completed.stderr),
        }

    def run_python_script(self, script_path: str, args: list[str] | None = None) -> dict[str, Any]:
        target = self._resolve_workspace_path(script_path)
        cmd = [self.context.python_executable, str(target), *(args or [])]
        completed = subprocess.run(
            cmd,
            cwd=self.context.workspace_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        return {
            "returncode": completed.returncode,
            "stdout": _truncate(completed.stdout),
            "stderr": _truncate(completed.stderr),
        }
