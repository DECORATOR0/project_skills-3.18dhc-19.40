"""
MCP server launch specifications for the five Earth-Agent toolkits.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .config import DEFAULT_TEMP_DIR, PROJECT_ROOT


@dataclass(frozen=True)
class McpServerSpec:
    name: str
    script_path: Path
    temp_dir: Path

    def command(self) -> list[str]:
        return [
            "python",
            str(self.script_path),
            "--temp_dir",
            str(self.temp_dir),
        ]


def build_mcp_server_specs(temp_dir: Path | None = None) -> list[McpServerSpec]:
    root = PROJECT_ROOT / "agent" / "tools"
    base_temp = temp_dir or DEFAULT_TEMP_DIR
    return [
        McpServerSpec("index", root / "Index.py", base_temp / "index"),
        McpServerSpec("inversion", root / "Inversion.py", base_temp / "inversion"),
        McpServerSpec("perception", root / "Perception.py", base_temp / "perception"),
        McpServerSpec("analysis", root / "Analysis.py", base_temp / "analysis"),
        McpServerSpec("statistics", root / "Statistics.py", base_temp / "statistics"),
    ]
