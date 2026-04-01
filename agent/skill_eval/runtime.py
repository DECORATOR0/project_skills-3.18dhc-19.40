"""
Direct execution backend for Earth-Agent tool functions.

The benchmark tools are authored as MCP tools, but for local evaluation we call
the same decorated Python functions directly for reliability and easier logging.
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .config import TOOL_FILES

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = PROJECT_ROOT / "agent" / "tools"


def _argmax(x: list) -> int:
    if not x:
        raise ValueError("argmax requires a non-empty list")
    return max(range(len(x)), key=lambda i: x[i])


def _index_to_date_range(index: int, dates: list) -> str:
    if not dates:
        raise ValueError("dates must not be empty")
    if index < 0 or index >= len(dates):
        raise IndexError(f"index {index} out of range for {len(dates)} dates")
    return str(dates[index])


SYNTHETIC_RUNTIME_ADAPTERS: dict[str, Callable[..., Any]] = {
    "argmax": _argmax,
    "index_to_date_range": _index_to_date_range,
}


@dataclass
class RuntimeTool:
    name: str
    func: Callable[..., Any]
    module_name: str


def _load_module(module_file: str, temp_dir: Path):
    module_path = TOOLS_DIR / module_file
    module_name = f"runtime_{module_path.stem.lower()}_{abs(hash(str(temp_dir))) % 100000}"
    if str(TOOLS_DIR) not in sys.path:
        sys.path.insert(0, str(TOOLS_DIR))

    old_argv = sys.argv[:]
    try:
        sys.argv = [str(module_path), "--temp_dir", str(temp_dir)]
        spec = importlib.util.spec_from_file_location(module_name, module_path)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Failed to build import spec for {module_file}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.argv = old_argv


class ToolRuntime:
    def __init__(self, temp_root: Path):
        self.temp_root = temp_root
        self._registry: dict[str, RuntimeTool] = {}
        self._load_all()

    def _load_all(self) -> None:
        for module_file in TOOL_FILES:
            module_temp = self.temp_root / Path(module_file).stem.lower()
            module_temp.mkdir(parents=True, exist_ok=True)
            module = _load_module(module_file, module_temp)
            for name, value in vars(module).items():
                if name.startswith("_"):
                    continue
                if inspect.isfunction(value):
                    self._registry.setdefault(
                        name,
                        RuntimeTool(name=name, func=value, module_name=Path(module_file).stem),
                    )
        for name, func in SYNTHETIC_RUNTIME_ADAPTERS.items():
            self._registry.setdefault(name, RuntimeTool(name=name, func=func, module_name="synthetic"))

    def has_tool(self, name: str) -> bool:
        return name in self._registry

    def available_tools(self) -> list[str]:
        return sorted(self._registry)

    def execute(self, name: str, arguments: dict[str, Any]) -> Any:
        if name not in self._registry:
            raise KeyError(f"Unknown runtime tool: {name}")
        func = self._registry[name].func
        if name == "compute_tvdi" and isinstance(arguments.get("ndvi_path"), list) and isinstance(arguments.get("lst_path"), list):
            ndvi_paths = arguments["ndvi_path"]
            lst_paths = arguments["lst_path"]
            out_spec = arguments.get("output_path")
            if len(ndvi_paths) != len(lst_paths):
                raise ValueError("compute_tvdi batch mode requires equal-length ndvi_path and lst_path lists")
            if isinstance(out_spec, list):
                output_paths = out_spec
            else:
                output_paths = [f"batch_tvdi_{i}.tif" for i in range(len(ndvi_paths))]
            results = []
            for ndvi_path, lst_path, output_path in zip(ndvi_paths, lst_paths, output_paths):
                results.append(func(ndvi_path=ndvi_path, lst_path=lst_path, output_path=output_path))
            return results
        sig = inspect.signature(func)
        accepted = {}
        for param_name, param in sig.parameters.items():
            if param.kind in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY):
                if param_name in arguments:
                    accepted[param_name] = arguments[param_name]
        return func(**accepted)


def summarize_result(result: Any, max_chars: int = 400) -> str:
    if isinstance(result, str):
        return result[:max_chars]
    if isinstance(result, (int, float, bool)) or result is None:
        return repr(result)
    if isinstance(result, dict):
        keys = list(result.keys())[:10]
        return f"dict(keys={keys})"
    if isinstance(result, tuple):
        return f"tuple(len={len(result)}): {repr(result)[:max_chars]}"
    if isinstance(result, list):
        head = result[:3]
        return f"list(len={len(result)}, head={head})"
    return repr(result)[:max_chars]
