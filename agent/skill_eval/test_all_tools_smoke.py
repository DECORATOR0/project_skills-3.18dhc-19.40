"""
Simple smoke test for all actual MCP tools in agent/tools.

What this script checks:
1. Each toolkit server file can start under the current Python environment.
2. Every actual `@mcp.tool` discovered from source is importable and callable.
3. A small set of representative runtime calls succeed on synthetic sample data.

This is intentionally a smoke test, not a full correctness benchmark.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.transform import from_origin

from .tool_catalog import parse_tool_file

PROJECT_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = PROJECT_ROOT / "agent" / "tools"
BENCHMARK_DIR = PROJECT_ROOT / "benchmark"
TOOL_FILES = ["Index.py", "Inversion.py", "Perception.py", "Analysis.py", "Statistics.py"]


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    extra: dict[str, Any] | None = None


def _safe_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # pragma: no cover - smoke test helper
        raise RuntimeError(f"{type(exc).__name__}: {exc}") from exc


def load_tool_module(module_file: str, temp_dir: Path):
    module_path = TOOLS_DIR / module_file
    module_name = f"smoke_{module_path.stem.lower()}"
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


def create_sample_raster(path: Path, data: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": data.shape[0],
        "width": data.shape[1],
        "count": 1,
        "dtype": str(data.dtype),
        "crs": "EPSG:4326",
        "transform": from_origin(0, 0, 1, 1),
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(data, 1)
    return path


def build_sample_data(root: Path) -> dict[str, Path]:
    samples = root / "samples"
    base = np.arange(400, dtype=np.float32).reshape(20, 20)
    raster_a = create_sample_raster(samples / "a.tif", base + 1)
    raster_b = create_sample_raster(samples / "b.tif", base + 2)
    red = create_sample_raster(samples / "red.tif", (base % 50 + 10).astype(np.float32))
    green = create_sample_raster(samples / "green.tif", (base % 40 + 5).astype(np.float32))

    # NDVI/LST inputs for compute_tvdi.
    ndvi_scaled = np.linspace(1000, 9000, 400, dtype=np.float32).reshape(20, 20)
    lst_scaled = np.linspace(15000, 17000, 400, dtype=np.float32).reshape(20, 20)
    ndvi = create_sample_raster(samples / "ndvi_scaled.tif", ndvi_scaled)
    lst = create_sample_raster(samples / "lst_scaled.tif", lst_scaled)

    return {
        "samples_dir": samples,
        "raster_a": raster_a,
        "raster_b": raster_b,
        "red": red,
        "green": green,
        "ndvi": ndvi,
        "lst": lst,
    }


def check_server_start(module_file: str, temp_root: Path, timeout_s: float = 2.5) -> CheckResult:
    module_path = TOOLS_DIR / module_file
    server_temp = temp_root / "server_start" / module_path.stem.lower()
    server_temp.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        [sys.executable, str(module_path), "--temp_dir", str(server_temp)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        cwd=str(PROJECT_ROOT),
    )
    try:
        time.sleep(timeout_s)
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
            return CheckResult(
                name=f"server_start:{module_path.stem}",
                ok=True,
                detail="Server stayed alive long enough to indicate successful startup.",
            )

        stdout, stderr = proc.communicate(timeout=3)
        return CheckResult(
            name=f"server_start:{module_path.stem}",
            ok=False,
            detail="Server exited early during startup.",
            extra={"stdout": stdout[-1000:], "stderr": stderr[-1000:], "returncode": proc.returncode},
        )
    finally:
        if proc.poll() is None:
            proc.kill()


def check_registry(temp_root: Path) -> tuple[list[CheckResult], dict[str, Any], dict[str, Any]]:
    results: list[CheckResult] = []
    modules: dict[str, Any] = {}
    actual_tools: dict[str, list[str]] = {}

    total_expected = 0
    total_found = 0
    missing: list[str] = []

    for module_file in TOOL_FILES:
        module = load_tool_module(module_file, temp_root / "imports" / module_file.replace(".py", "").lower())
        modules[module_file] = module
        tool_names = [t.canonical_name for t in parse_tool_file(TOOLS_DIR / module_file)]
        actual_tools[module_file] = tool_names

        missing_here = []
        for tool_name in tool_names:
            total_expected += 1
            attr = getattr(module, tool_name, None)
            if callable(attr):
                total_found += 1
            else:
                missing.append(f"{module_file}:{tool_name}")
                missing_here.append(tool_name)

        results.append(
            CheckResult(
                name=f"registry:{Path(module_file).stem}",
                ok=not missing_here,
                detail=f"Expected {len(tool_names)} tools, found callable objects for {len(tool_names) - len(missing_here)}.",
                extra={"missing": missing_here},
            )
        )

    summary = {
        "expected_actual_mcp_tools": total_expected,
        "callable_tools_found": total_found,
        "missing_tools": missing,
    }
    return results, modules, {"summary": summary, "actual_tools": actual_tools}


def maybe_find_mscn_sample() -> str | None:
    csv_path = BENCHMARK_DIR / "model_results.csv"
    if not csv_path.exists():
        return None
    try:
        import pandas as pd
    except Exception:
        return None

    df = pd.read_csv(csv_path, sep=";")
    rows = df[df["model"] == "MSCN"]
    if rows.empty:
        return None

    for raw_path in rows["file_path"].tolist():
        path = Path(str(raw_path))
        if path.exists():
            return str(path)
        project_relative = PROJECT_ROOT / str(raw_path)
        if project_relative.exists():
            return str(project_relative)
    return None


def check_runtime(temp_root: Path, modules: dict[str, Any], samples: dict[str, Path]) -> list[CheckResult]:
    results: list[CheckResult] = []
    stats = modules["Statistics.py"]
    index = modules["Index.py"]
    inversion = modules["Inversion.py"]
    perception = modules["Perception.py"]
    analysis = modules["Analysis.py"]

    tests: list[tuple[str, Any]] = []

    def add_test(name: str, fn):
        tests.append((name, fn))

    add_test(
        "runtime:Statistics.get_filelist",
        lambda: _safe_call(stats.get_filelist, str(samples["samples_dir"])),
    )
    add_test(
        "runtime:Statistics.mean",
        lambda: _safe_call(stats.mean, [1.0, 2.0, 3.0, 4.0]),
    )
    add_test(
        "runtime:Statistics.calculate_tif_average",
        lambda: _safe_call(
            stats.calculate_tif_average,
            [str(samples["raster_a"]), str(samples["raster_b"])],
            "statistics/avg.tif",
        ),
    )
    add_test(
        "runtime:Statistics.calculate_threshold_ratio",
        lambda: _safe_call(stats.calculate_threshold_ratio, str(samples["raster_a"]), threshold=100, mode="above"),
    )
    add_test(
        "runtime:Index.compute_tvdi",
        lambda: _safe_call(index.compute_tvdi, str(samples["ndvi"]), str(samples["lst"]), "index/tvdi.tif"),
    )
    add_test(
        "runtime:Inversion.calculate_water_turbidity_ntu",
        lambda: _safe_call(inversion.calculate_water_turbidity_ntu, str(samples["red"]), "inversion/ntu.tif"),
    )
    add_test(
        "runtime:Perception.threshold_segmentation",
        lambda: _safe_call(perception.threshold_segmentation, str(samples["raster_a"]), 100, "perception/mask.tif"),
    )
    add_test(
        "runtime:Perception.bbox_expansion",
        lambda: _safe_call(perception.bbox_expansion, [[0, 0, 10, 10], [20, 20, 30, 30]], 5, 1.0),
    )
    add_test(
        "runtime:Perception.bboxes2centroids",
        lambda: _safe_call(perception.bboxes2centroids, [[0, 0, 10, 10], [20, 20, 40, 40]]),
    )
    add_test(
        "runtime:Perception.centroid_distance_extremes",
        lambda: _safe_call(perception.centroid_distance_extremes, [[5, 5], [25, 25], [50, 5]]),
    )
    add_test(
        "runtime:Perception.calculate_bbox_area",
        lambda: _safe_call(perception.calculate_bbox_area, [[0, 0, 10, 20], [10, 10, 20, 20]], 1.0),
    )
    add_test(
        "runtime:Analysis.compute_linear_trend",
        lambda: _safe_call(analysis.compute_linear_trend, [1.0, 2.0, 4.0, 8.0]),
    )
    add_test(
        "runtime:Analysis.mann_kendall_test",
        lambda: _safe_call(analysis.mann_kendall_test, [1.0, 2.0, 4.0, 8.0]),
    )

    mscn_sample = maybe_find_mscn_sample()
    if mscn_sample is not None:
        add_test(
            "runtime:Perception.MSCN(optional)",
            lambda: _safe_call(perception.MSCN, mscn_sample),
        )
    else:
        results.append(
            CheckResult(
                name="runtime:Perception.MSCN(optional)",
                ok=True,
                detail="Skipped optional model-backed test because no usable MSCN sample was found.",
                extra={"skipped": True},
            )
        )

    for name, fn in tests:
        try:
            value = fn()
            detail = f"OK: {type(value).__name__}"
            if isinstance(value, str):
                detail = f"OK: {value[:180]}"
            results.append(CheckResult(name=name, ok=True, detail=detail))
        except Exception as exc:
            results.append(CheckResult(name=name, ok=False, detail=str(exc)))

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test all actual MCP tools.")
    parser.add_argument("--output-dir", type=str, default=None, help="Directory for smoke-test artifacts.")
    args = parser.parse_args()

    if args.output_dir:
        out_root = Path(args.output_dir)
        out_root.mkdir(parents=True, exist_ok=True)
    else:
        out_root = Path(tempfile.mkdtemp(prefix="earth_tools_smoke_", dir=str(PROJECT_ROOT / "benchmark" / "out")))

    samples = build_sample_data(out_root)

    server_results = [check_server_start(fname, out_root) for fname in TOOL_FILES]
    registry_results, modules, registry_meta = check_registry(out_root)
    runtime_results = check_runtime(out_root, modules, samples)

    all_results = server_results + registry_results + runtime_results
    failed = [r for r in all_results if not r.ok]

    summary = {
        "output_dir": str(out_root),
        "server_results": [asdict(r) for r in server_results],
        "registry_results": [asdict(r) for r in registry_results],
        "runtime_results": [asdict(r) for r in runtime_results],
        "registry_meta": registry_meta,
        "failed_count": len(failed),
        "passed_count": len(all_results) - len(failed),
    }

    summary_path = out_root / "smoke_test_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")

    print("=" * 72)
    print("Earth-Agent Tools Smoke Test")
    print("=" * 72)
    print(f"Artifacts: {out_root}")
    print(f"Actual MCP tools discovered: {registry_meta['summary']['expected_actual_mcp_tools']}")
    print(f"Passed checks: {summary['passed_count']}")
    print(f"Failed checks: {summary['failed_count']}")
    print(f"Summary JSON: {summary_path}")

    if failed:
        print("\nFailures:")
        for item in failed:
            print(f"- {item.name}: {item.detail}")
        return 1

    print("\nAll smoke checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
