"""
Parse agent/tools/*.py to build a canonical, skill-eval-facing tool catalog.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from .config import TOOL_SOURCE_DIR, TOOL_FILES


@dataclass
class ToolMeta:
    canonical_name: str
    toolkit: str
    description: str = ""
    parameters: list[dict] = field(default_factory=list)
    input_types: list[str] = field(default_factory=list)
    output_types: list[str] = field(default_factory=list)
    task_tags: list[str] = field(default_factory=list)
    modality_tags: list[str] = field(default_factory=list)
    cost_hint: str = "medium"
    stage_hint: str = "transform"
    aliases: list[str] = field(default_factory=list)

    def short_description(self, max_len: int = 200) -> str:
        desc = self.description.strip().split("\n")[0]
        return desc[:max_len]

    def to_prompt_entry(self) -> str:
        params = ", ".join(
            f"{p['name']}: {p.get('type','any')}" + (f" = {p['default']}" if 'default' in p else "")
            for p in self.parameters
        )
        lines = [
            f"- **{self.canonical_name}**({params})",
            f"  Toolkit: {self.toolkit} | Tags: {', '.join(self.task_tags)} | Modality: {', '.join(self.modality_tags)}",
            f"  {self.short_description(180)}",
        ]
        return "\n".join(lines)


_TAG_RULES: list[tuple[str, list[str], list[str], list[str]]] = [
    (r"ndvi|ndwi|ndbi|evi|nbr|fvc|wri|ndti|ndsi|frp", ["spectral_index"], ["spectral"], ["tif"]),
    (r"lst|temperature|emissivity|split.window|ttm|modis.*lst", ["inversion", "temperature"], ["product"], ["tif"]),
    (r"tvdi", ["inversion", "drought"], ["product", "temporal"], ["tif"]),
    (r"turbidity|pwv|water.vapor|band.ratio", ["inversion"], ["product"], ["tif"]),
    (r"sea.ice|polarization|microwave|bt|brightness", ["inversion"], ["product"], ["tif"]),
    (r"ati|thermal.inertia", ["inversion", "thermal"], ["product"], ["tif"]),
    (r"MSCN|RemoteCLIP|classif", ["classification"], ["RGB"], ["jpg", "png"]),
    (r"Strip.R.CNN|SM3Det|detect", ["detection"], ["RGB"], ["jpg", "png"]),
    (r"RemoteSAM|grounding", ["grounding"], ["RGB"], ["jpg", "png"]),
    (r"InstructSAM|count.*prompt", ["counting"], ["RGB"], ["jpg", "png"]),
    (r"SAM2|segment", ["segmentation"], ["RGB"], ["jpg", "png"]),
    (r"ChangeOS|change", ["change_detection"], ["RGB"], ["jpg", "png"]),
    (r"threshold.*seg|hotspot.*tif|binary", ["thresholding"], ["spectral", "product"], ["tif"]),
    (r"trend|mann.kendall|sens.slope|stl|seasonality|autocorrelation|spike", ["temporal_analysis"], ["temporal"], ["scalar"]),
    (r"getis|hotspot.*direction", ["spatial_analysis"], ["geospatial"], ["tif"]),
    (r"change.point", ["temporal_analysis"], ["temporal"], ["scalar"]),
    (r"mean|std|median|min|max|sum|skewness|kurtosis|cv|coefficient", ["aggregation", "statistics"], ["any"], ["tif", "scalar"]),
    (r"difference|division|subtract|multiply|percentage|ceil|kelvin|celsius", ["arithmetic"], ["any"], ["scalar"]),
    (r"filelist|get_filelist", ["discovery"], ["any"], ["path"]),
    (r"bbox.*centroid|centroid.*distance|bbox.*area|bbox.*expansion", ["geometry"], ["any"], ["bbox"]),
    (r"colormap|visuali", ["visualization"], ["any"], ["tif"]),
    (r"cloud.mask|radiometric", ["preprocessing"], ["spectral"], ["tif"]),
    (r"fire|frp", ["fire_analysis"], ["spectral", "product"], ["tif"]),
    (r"snow|ndsi", ["snow_analysis"], ["spectral"], ["tif"]),
    (r"intersection|ratio.*threshold|band.*condition|count.*pixel", ["thresholding", "aggregation"], ["spectral", "product"], ["tif"]),
    (r"average.*tif|tif.*average|tif.*difference", ["aggregation"], ["any"], ["tif"]),
]

_STAGE_RULES: dict[str, str] = {
    "discovery": "discovery",
    "spectral_index": "transform",
    "inversion": "transform",
    "classification": "transform",
    "detection": "transform",
    "grounding": "transform",
    "counting": "transform",
    "segmentation": "transform",
    "change_detection": "transform",
    "thresholding": "transform",
    "preprocessing": "transform",
    "temporal_analysis": "aggregate",
    "spatial_analysis": "aggregate",
    "aggregation": "aggregate",
    "statistics": "aggregate",
    "arithmetic": "aggregate",
    "geometry": "aggregate",
    "visualization": "decide",
    "fire_analysis": "transform",
    "snow_analysis": "transform",
}


def _infer_tags(name: str, desc: str) -> tuple[list[str], list[str], list[str], str]:
    combined = f"{name} {desc}".lower()
    task_tags, mod_tags, inp_types = [], [], []
    for pattern, tasks, mods, inps in _TAG_RULES:
        if re.search(pattern, combined, re.IGNORECASE):
            task_tags.extend(tasks)
            mod_tags.extend(mods)
            inp_types.extend(inps)
    task_tags = list(dict.fromkeys(task_tags)) or ["other"]
    mod_tags = list(dict.fromkeys(mod_tags)) or ["any"]
    inp_types = list(dict.fromkeys(inp_types)) or ["any"]
    stage = "transform"
    for t in task_tags:
        if t in _STAGE_RULES:
            stage = _STAGE_RULES[t]
            break
    return task_tags, mod_tags, inp_types, stage


def _parse_params(node: ast.FunctionDef) -> list[dict]:
    params = []
    args = node.args
    defaults_offset = len(args.args) - len(args.defaults)
    for i, arg in enumerate(args.args):
        if arg.arg == "self":
            continue
        p: dict = {"name": arg.arg}
        if arg.annotation:
            p["type"] = ast.unparse(arg.annotation)
        di = i - defaults_offset
        if di >= 0 and di < len(args.defaults):
            try:
                p["default"] = ast.literal_eval(args.defaults[di])
            except Exception:
                p["default"] = ast.unparse(args.defaults[di])
        params.append(p)
    return params


def _extract_decorator_description(node: ast.FunctionDef) -> str:
    for dec in node.decorator_list:
        if isinstance(dec, ast.Call):
            for kw in dec.keywords:
                if kw.arg == "description" and isinstance(kw.value, ast.Constant):
                    return str(kw.value.value).strip()
    return ""


def _is_mcp_tool(node: ast.FunctionDef) -> bool:
    for dec in node.decorator_list:
        src = ast.unparse(dec)
        if "mcp.tool" in src:
            return True
    return False


def parse_tool_file(filepath: Path) -> list[ToolMeta]:
    toolkit = filepath.stem
    source = filepath.read_text(encoding="utf-8")
    tree = ast.parse(source)
    tools = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.FunctionDef):
            continue
        if not _is_mcp_tool(node):
            continue
        desc = _extract_decorator_description(node)
        if not desc:
            desc = ast.get_docstring(node) or ""
        params = _parse_params(node)
        task_tags, mod_tags, inp_types, stage = _infer_tags(node.name, desc)

        out_types = ["raster"] if any(t in ["tif"] for t in inp_types) else ["scalar"]
        if any(t in task_tags for t in ["detection", "grounding"]):
            out_types = ["bbox"]
        elif "counting" in task_tags:
            out_types = ["count"]
        elif "classification" in task_tags:
            out_types = ["label"]
        elif "segmentation" in task_tags or "change_detection" in task_tags:
            out_types = ["mask"]
        elif any(t in task_tags for t in ["aggregation", "statistics", "arithmetic"]):
            out_types = ["scalar"]

        cost = "cheap"
        if any(t in task_tags for t in ["classification", "detection", "grounding", "counting", "segmentation", "change_detection"]):
            cost = "expensive"
        elif any(t in task_tags for t in ["inversion", "spectral_index"]):
            cost = "medium"

        tools.append(
            ToolMeta(
                canonical_name=node.name,
                toolkit=toolkit,
                description=desc,
                parameters=params,
                input_types=inp_types,
                output_types=out_types,
                task_tags=task_tags,
                modality_tags=mod_tags,
                cost_hint=cost,
                stage_hint=stage,
            )
        )
    return tools


_SYNTHETIC_TOOLS = [
    ToolMeta(canonical_name="calculate_ndvi", toolkit="Index", description="Calculate NDVI from NIR and Red band rasters.", parameters=[{"name": "input_nir_path", "type": "str"}, {"name": "input_red_path", "type": "str"}, {"name": "output_path", "type": "str"}], input_types=["tif"], output_types=["raster"], task_tags=["spectral_index"], modality_tags=["spectral"], cost_hint="medium", stage_hint="transform"),
    ToolMeta(canonical_name="calculate_ndwi", toolkit="Index", description="Calculate NDWI from NIR and SWIR band rasters.", parameters=[{"name": "input_nir_path", "type": "str"}, {"name": "input_swir_path", "type": "str"}, {"name": "output_path", "type": "str"}], input_types=["tif"], output_types=["raster"], task_tags=["spectral_index"], modality_tags=["spectral"], cost_hint="medium", stage_hint="transform"),
    ToolMeta(canonical_name="calculate_ndbi", toolkit="Index", description="Calculate NDBI from SWIR and NIR band rasters.", parameters=[{"name": "input_swir_path", "type": "str"}, {"name": "input_nir_path", "type": "str"}, {"name": "output_path", "type": "str"}], input_types=["tif"], output_types=["raster"], task_tags=["spectral_index"], modality_tags=["spectral"], cost_hint="medium", stage_hint="transform"),
    ToolMeta(canonical_name="calculate_nbr", toolkit="Index", description="Calculate NBR (Normalized Burn Ratio) from NIR and SWIR.", parameters=[{"name": "input_nir_path", "type": "str"}, {"name": "input_swir_path", "type": "str"}, {"name": "output_path", "type": "str"}], input_types=["tif"], output_types=["raster"], task_tags=["spectral_index", "fire_analysis"], modality_tags=["spectral"], cost_hint="medium", stage_hint="transform"),
    ToolMeta(canonical_name="calculate_ndti", toolkit="Index", description="Calculate NDTI (Normalized Difference Turbidity Index) from Red and Green.", parameters=[{"name": "input_red_path", "type": "str"}, {"name": "input_green_path", "type": "str"}, {"name": "output_path", "type": "str"}], input_types=["tif"], output_types=["raster"], task_tags=["spectral_index"], modality_tags=["spectral"], cost_hint="medium", stage_hint="transform"),
    ToolMeta(canonical_name="calculate_evi", toolkit="Index", description="Calculate EVI from NIR, Red, and Blue band rasters.", parameters=[{"name": "input_nir_path", "type": "str"}, {"name": "input_red_path", "type": "str"}, {"name": "input_blue_path", "type": "str"}, {"name": "output_path", "type": "str"}], input_types=["tif"], output_types=["raster"], task_tags=["spectral_index"], modality_tags=["spectral"], cost_hint="medium", stage_hint="transform"),
    ToolMeta(canonical_name="calculate_fvc", toolkit="Index", description="Calculate Fractional Vegetation Cover from NIR and Red.", parameters=[{"name": "input_nir_path", "type": "str"}, {"name": "input_red_path", "type": "str"}, {"name": "output_path", "type": "str"}], input_types=["tif"], output_types=["raster"], task_tags=["spectral_index"], modality_tags=["spectral"], cost_hint="medium", stage_hint="transform"),
    ToolMeta(canonical_name="calculate_wri", toolkit="Index", description="Calculate WRI (Water Ratio Index) from Green, Red, NIR, SWIR.", parameters=[{"name": "input_green_path", "type": "str"}, {"name": "input_red_path", "type": "str"}, {"name": "input_nir_path", "type": "str"}, {"name": "input_swir_path", "type": "str"}, {"name": "output_path", "type": "str"}], input_types=["tif"], output_types=["raster"], task_tags=["spectral_index"], modality_tags=["spectral"], cost_hint="medium", stage_hint="transform"),
    ToolMeta(canonical_name="calculate_ndsi", toolkit="Index", description="Calculate NDSI (Normalized Difference Snow Index) from Green and SWIR.", parameters=[{"name": "input_green_path", "type": "str"}, {"name": "input_swir_path", "type": "str"}, {"name": "output_path", "type": "str"}], input_types=["tif"], output_types=["raster"], task_tags=["spectral_index", "snow_analysis"], modality_tags=["spectral"], cost_hint="medium", stage_hint="transform"),
    ToolMeta(canonical_name="calculate_frp", toolkit="Index", description="Calculate FRP (Fire Radiative Power) from input raster.", parameters=[{"name": "input_frp_path", "type": "str"}, {"name": "output_path", "type": "str"}], input_types=["tif"], output_types=["raster"], task_tags=["spectral_index", "fire_analysis"], modality_tags=["spectral"], cost_hint="medium", stage_hint="transform"),
    ToolMeta(canonical_name="argmax", toolkit="Statistics", description="Return the index of the maximum value in a list.", parameters=[{"name": "x", "type": "list"}], input_types=["scalar"], output_types=["scalar"], task_tags=["aggregation"], modality_tags=["any"], cost_hint="cheap", stage_hint="aggregate"),
    ToolMeta(canonical_name="index_to_date_range", toolkit="Statistics", description="Convert list index to date range string.", parameters=[{"name": "index", "type": "int"}, {"name": "dates", "type": "list"}], input_types=["scalar"], output_types=["scalar"], task_tags=["aggregation"], modality_tags=["temporal"], cost_hint="cheap", stage_hint="aggregate"),
]


def build_catalog() -> list[ToolMeta]:
    catalog: list[ToolMeta] = []
    for fname in TOOL_FILES:
        fpath = TOOL_SOURCE_DIR / fname
        if fpath.exists():
            catalog.extend(parse_tool_file(fpath))
    existing = {t.canonical_name for t in catalog}
    for syn in _SYNTHETIC_TOOLS:
        if syn.canonical_name not in existing:
            catalog.append(syn)
    return catalog


def catalog_to_prompt_text(catalog: list[ToolMeta]) -> str:
    sections: dict[str, list[str]] = {}
    for t in catalog:
        sections.setdefault(t.toolkit, []).append(t.to_prompt_entry())
    parts = []
    for tk, entries in sections.items():
        parts.append(f"\n### {tk} Toolkit\n")
        parts.extend(entries)
    return "\n".join(parts)
