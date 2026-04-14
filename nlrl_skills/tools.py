from __future__ import annotations

import ast
import importlib.util
import inspect
import json
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Union, get_args, get_origin

from .utils import ensure_dir, read_text, safe_relative_path, write_text

EO_TOOL_FILES = ["Index.py", "Inversion.py", "Perception.py", "Analysis.py", "Statistics.py"]
_TOOL_IMPORT_LOCK = threading.Lock()
try:
    from types import UnionType as _NativeUnionType
except ImportError:
    _NativeUnionType = None

_UNION_ORIGINS = {Union}
if _NativeUnionType is not None:
    _UNION_ORIGINS.add(_NativeUnionType)


@dataclass
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]
    callable: Callable[..., Any]
    source: str
    signature: dict[str, str] = field(default_factory=dict)

    def prompt_entry(self) -> str:
        payload = {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
            "source": self.source,
        }
        if self.signature:
            payload["signature"] = self.signature
        return json.dumps(payload, ensure_ascii=False)


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

    def _parse_tool_nodes(self, path: Path) -> list[tuple[str, str]]:
        tree = ast.parse(read_text(path))
        parsed: list[tuple[str, str]] = []
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
            parsed.append((node.name, description))
        return parsed

    def _coerce_untyped_value(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        text = value.strip()
        if not text:
            return value
        lowered = text.lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        if text.startswith("[") or text.startswith("{") or text.startswith("("):
            for loader in (json.loads, ast.literal_eval):
                try:
                    return loader(text)
                except Exception:
                    continue
        try:
            if any(token in text for token in (".", "e", "E")):
                return float(text)
            return int(text)
        except Exception:
            return value

    def _annotation_to_schema(self, annotation: Any) -> dict[str, Any]:
        if annotation in {inspect._empty, Any, None}:
            return {"type": "string"}
        if annotation in {list, tuple, set}:
            return {
                "type": "array",
                "items": {"type": "string"},
            }
        if annotation is dict:
            return {"type": "object"}
        origin = get_origin(annotation)
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if origin in _UNION_ORIGINS and args:
            variants = [self._annotation_to_schema(arg) for arg in args]
            if len(variants) == 1:
                return variants[0]
            return {"oneOf": variants}
        if origin in {list, tuple, set}:
            item_annotation = args[0] if args else Any
            return {
                "type": "array",
                "items": self._annotation_to_schema(item_annotation),
            }
        if origin is dict:
            return {"type": "object"}
        if origin is not None and args:
            return self._annotation_to_schema(args[0])
        if annotation is bool:
            return {"type": "boolean"}
        if annotation is int:
            return {"type": "integer"}
        if annotation is float:
            return {"type": "number"}
        return {"type": "string"}

    def _annotation_to_text(self, annotation: Any) -> str:
        if annotation in {inspect._empty, Any, None}:
            return "string"
        if annotation is str:
            return "str"
        if annotation is bool:
            return "bool"
        if annotation is int:
            return "int"
        if annotation is float:
            return "float"
        if annotation in {list, tuple, set}:
            return "list[string]"
        if annotation is dict:
            return "object"
        origin = get_origin(annotation)
        args = [arg for arg in get_args(annotation) if arg is not type(None)]
        if origin in _UNION_ORIGINS and args:
            return " | ".join(self._annotation_to_text(arg) for arg in args)
        if origin in {list, tuple, set}:
            item_annotation = args[0] if args else Any
            return f"list[{self._annotation_to_text(item_annotation)}]"
        if origin is dict:
            return "object"
        return "string"

    def _schema_from_signature(self, func: Callable[..., Any]) -> dict[str, Any]:
        params: dict[str, Any] = {"type": "object", "properties": {}, "required": []}
        sig = inspect.signature(func)
        for arg_name, param in sig.parameters.items():
            if arg_name == "self":
                continue
            schema = self._annotation_to_schema(param.annotation)
            schema["description"] = f"Argument {arg_name}"
            params["properties"][arg_name] = schema
            if param.default is inspect._empty and param.kind in (
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                params["required"].append(arg_name)
        return params

    def _signature_from_callable(self, func: Callable[..., Any]) -> dict[str, str]:
        signature: dict[str, str] = {}
        sig = inspect.signature(func)
        for arg_name, param in sig.parameters.items():
            if arg_name == "self":
                continue
            signature[arg_name] = self._annotation_to_text(param.annotation)
        return signature

    def _coerce_argument(self, value: Any, annotation: Any) -> Any:
        if value is None:
            return value
        if annotation in {inspect._empty, Any, None}:
            return self._coerce_untyped_value(value)
        if annotation in {list, tuple, set}:
            annotation = annotation[Any]
        elif annotation is dict:
            annotation = dict[str, Any]
        origin = get_origin(annotation)
        args = [arg for arg in get_args(annotation) if arg is not type(None)]

        if origin in _UNION_ORIGINS and args:
            if isinstance(value, (list, tuple, set)):
                for candidate in args:
                    if get_origin(candidate) in {list, tuple, set}:
                        return self._coerce_argument(value, candidate)
            if isinstance(value, dict):
                for candidate in args:
                    if get_origin(candidate) is dict or candidate is dict:
                        return self._coerce_argument(value, candidate)
            if isinstance(value, str):
                text = value.strip()
                if text.startswith("[") or text.startswith("("):
                    for candidate in args:
                        if get_origin(candidate) in {list, tuple, set}:
                            return self._coerce_argument(value, candidate)
                if text.startswith("{"):
                    for candidate in args:
                        if get_origin(candidate) is dict or candidate is dict:
                            return self._coerce_argument(value, candidate)
            scalar_candidates = [
                candidate
                for candidate in args
                if get_origin(candidate) not in {list, tuple, set, dict}
            ]
            if scalar_candidates:
                return self._coerce_argument(value, scalar_candidates[0])
            return self._coerce_argument(value, args[0])

        if origin in {list, tuple, set}:
            item_annotation = args[0] if args else Any
            parsed = value
            if isinstance(value, str):
                text = value.strip()
                if text.startswith("[") or text.startswith("("):
                    try:
                        parsed = json.loads(text)
                    except Exception:
                        try:
                            parsed = ast.literal_eval(text)
                        except Exception:
                            parsed = [item.strip() for item in text.split(",") if item.strip()]
                else:
                    parsed = [item.strip() for item in text.split(",") if item.strip()]
            if not isinstance(parsed, (list, tuple, set)):
                return parsed
            coerced = [self._coerce_argument(item, item_annotation) for item in parsed]
            if origin is tuple:
                return tuple(coerced)
            if origin is set:
                return set(coerced)
            return coerced

        if origin is dict:
            if isinstance(value, str):
                text = value.strip()
                for loader in (json.loads, ast.literal_eval):
                    try:
                        parsed = loader(text)
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        continue
            return value

        if origin is not None and args:
            return self._coerce_argument(value, args[0])

        if annotation is bool and isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "y"}:
                return True
            if lowered in {"false", "0", "no", "n"}:
                return False
            return value

        if annotation is int and isinstance(value, str):
            try:
                return int(float(value.strip()))
            except Exception:
                return value

        if annotation is float and isinstance(value, str):
            try:
                return float(value.strip())
            except Exception:
                return value

        return value

    def _load_module(self, module_file: str) -> Any:
        module_path = self.tools_dir / module_file
        module_name = f"nlrl_runtime_{module_path.stem.lower()}_{abs(hash(str(module_path))) % 100000}"
        with _TOOL_IMPORT_LOCK:
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
            parsed = {name: desc for name, desc in self._parse_tool_nodes(source_path)}
            module = self._load_module(module_file)
            for name, value in vars(module).items():
                if name not in parsed:
                    continue
                # fastmcp>=2 decorates tools into FunctionTool objects instead of
                # leaving plain functions bound on the module.
                callable_obj = value if inspect.isfunction(value) else getattr(value, "fn", None)
                if not inspect.isfunction(callable_obj):
                    continue
                desc = parsed[name]
                self._registry[name] = ToolSpec(
                    name=name,
                    description=desc or f"EO tool {name}",
                    parameters=self._schema_from_signature(callable_obj),
                    callable=callable_obj,
                    source=f"agent/tools/{module_file}",
                    signature=self._signature_from_callable(callable_obj),
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
                    accepted[param_name] = self._coerce_argument(arguments[param_name], param.annotation)
        return func(**accepted)


@dataclass
class ToolContext:
    workspace_root: Path
    skill_library_root: Path
    temp_root: Path
    python_executable: str = "python"
    shell_program: str = "/bin/bash"
    eo_runtime: EOToolRuntime | None = None
    active_skill_dir: Path | None = None
    active_task_data_dir: Path | None = None

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
                signature=spec.signature,
            )

    def _register(self, spec: ToolSpec) -> None:
        self._registry[spec.name] = spec

    def set_active_skill_dir(self, skill_dir: str | Path | None) -> None:
        self.context.active_skill_dir = None if skill_dir is None else Path(skill_dir).resolve()

    def set_active_task_data_dir(self, task_data_dir: str | Path | None) -> None:
        if task_data_dir is None:
            self.context.active_task_data_dir = None
            return
        raw_path = Path(str(task_data_dir))
        self.context.active_task_data_dir = raw_path if raw_path.is_absolute() else (self.context.workspace_root / raw_path)

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
            return self.context.eo_runtime.execute(tool_name, self._normalize_eo_arguments(kwargs))

        return _call

    def _resolve_temp_artifact_path(self, user_path: str) -> str:
        normalized = user_path.replace("\\", "/").strip()
        if not normalized:
            return user_path
        candidate = Path(normalized)
        if candidate.is_absolute():
            return str(candidate) if candidate.exists() else user_path
        try:
            workspace_target = self._resolve_workspace_path(normalized)
        except Exception:
            workspace_target = None
        if workspace_target is not None and workspace_target.exists():
            return str(workspace_target)
        if self.context.active_task_data_dir is not None:
            task_target = self.context.active_task_data_dir / normalized
            if task_target.exists():
                return str(task_target.resolve())
        matches = [
            path.resolve()
            for path in self.context.temp_root.rglob(candidate.name)
            if str(path).replace("\\", "/").endswith(normalized)
        ]
        if matches:
            matches.sort(key=lambda path: (path.stat().st_mtime, -len(str(path))), reverse=True)
            return str(matches[0])
        if "/" not in normalized and self.context.active_task_data_dir is not None:
            direct_match = self.context.active_task_data_dir / candidate.name
            if direct_match.exists():
                return str(direct_match.resolve())
        return user_path

    def _normalize_eo_argument_value(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._resolve_temp_artifact_path(value)
        if isinstance(value, list):
            return [self._normalize_eo_argument_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self._normalize_eo_argument_value(item) for item in value)
        return value

    def _normalize_eo_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        normalized: dict[str, Any] = {}
        for key, value in arguments.items():
            if key in {"output_path", "output_path_list"}:
                normalized[key] = value
                continue
            if key == "path":
                normalized[key] = value
                continue
            if key.endswith("_path") or key.endswith("_paths") or key in {
                "file_path",
                "file_list",
                "image_path",
                "image_paths",
                "dir_path",
                "path1",
                "path2",
            }:
                normalized[key] = self._normalize_eo_argument_value(value)
                continue
            normalized[key] = value
        return normalized

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
                description="Run a Python script under the workspace with optional string arguments and optional stdin payloads.",
                parameters={
                    "type": "object",
                    "properties": {
                        "script_path": {"type": "string", "description": "Workspace-relative script path such as scripts/helper.py."},
                        "args": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional positional arguments passed to the script.",
                        },
                        "stdin_json": {
                            "description": "Optional JSON-serializable payload written to stdin. Use this when the helper script reads json.load(sys.stdin)."
                        },
                        "stdin_text": {
                            "type": "string",
                            "description": "Optional raw text written to stdin.",
                        },
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

    def run_python_script(
        self,
        script_path: str,
        args: list[str] | None = None,
        stdin_json: Any | None = None,
        stdin_text: str | None = None,
    ) -> dict[str, Any]:
        target = self._resolve_workspace_path(script_path)
        if stdin_json is not None and stdin_text is not None:
            raise ValueError("Provide at most one of stdin_json or stdin_text.")

        normalized_args = [
            json.dumps(arg, ensure_ascii=False) if isinstance(arg, (dict, list, tuple)) else str(arg)
            for arg in (args or [])
        ]
        stdin_payload = ""
        script_source = read_text(target)
        expects_json_stdin = "json.load(sys.stdin)" in script_source or "json.loads(sys.stdin.read(" in script_source

        if stdin_json is not None:
            stdin_payload = json.dumps(stdin_json, ensure_ascii=False)
        elif stdin_text is not None:
            stdin_payload = stdin_text
        elif expects_json_stdin and len(normalized_args) == 1:
            candidate = normalized_args[0].strip()
            if candidate[:1] in {"{", "["}:
                try:
                    json.loads(candidate)
                    stdin_payload = normalized_args[0]
                    normalized_args = []
                except Exception:
                    pass

        if expects_json_stdin and not stdin_payload:
            return {
                "returncode": 2,
                "stdout": "",
                "stderr": (
                    "Script expects JSON on stdin. Call run_python_script with stdin_json "
                    "or stdin_text instead of only positional args."
                ),
            }

        cmd = [self.context.python_executable, str(target), *normalized_args]
        completed = subprocess.run(
            cmd,
            cwd=self.context.workspace_root,
            input=stdin_payload,
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
