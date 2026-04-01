"""
Route Earth-Bench Autonomous Planning questions to a compact skill set.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import re

from .tool_catalog import ToolMeta


@dataclass(frozen=True)
class SkillSpec:
    skill_id: str
    display_name: str
    description: str
    question_range: tuple[int, int]
    tool_allowlist: tuple[str, ...]
    trigger_keywords: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()


COMMON_DISCOVERY_AND_TAIL = (
    "get_filelist",
    "calc_batch_image_mean",
    "calc_batch_image_mean_mean",
    "mean",
    "difference",
    "percentage_change",
    "max_value_and_index",
    "min_value_and_index",
    "compute_linear_trend",
)


SKILL_SPECS: dict[str, SkillSpec] = {
    "earth-spectrum-thermal-retrieval": SkillSpec(
        skill_id="earth-spectrum-thermal-retrieval",
        display_name="Spectrum Thermal Retrieval",
        description=(
            "Handles thermal-band spectrum tasks that retrieve or transform LST and then "
            "apply thresholding, period comparison, or summary statistics. Use for split-window, "
            "single-channel, multi-channel, TES, MODIS day/night, TTM, PWV, or emissivity questions."
        ),
        question_range=(1, 100),
        tool_allowlist=COMMON_DISCOVERY_AND_TAIL
        + (
            "split_window",
            "lst_single_channel",
            "lst_multi_channel",
            "temperature_emissivity_separation",
            "modis_day_night_lst",
            "ttm_lst",
            "band_ratio",
            "count_images_exceeding_threshold_ratio",
            "count_images_exceeding_mean_multiplier",
            "count_pixels_satisfying_conditions",
            "calculate_band_mean_by_condition",
            "calculate_threshold_ratio",
            "calc_batch_image_mean_threshold",
            "calc_batch_image_max",
            "calc_threshold_value_mean",
            "average_ratio_exceeding_threshold",
            "image_division_mean",
            "calc_batch_image_mean_max_min",
        ),
        trigger_keywords=(
            "split-window",
            "single-channel",
            "multi-channel",
            "tes",
            "emissivity",
            "band 31",
            "band 32",
            "lst",
            "temperature",
            "modis",
        ),
        notes=(
            "Prefer specialized threshold/count tools over generic mean chains when the benchmark "
            "dialogue clearly uses a dedicated family tool.",
        ),
    ),
    "earth-spectrum-drought-stress": SkillSpec(
        skill_id="earth-spectrum-drought-stress",
        display_name="Spectrum Drought Stress",
        description=(
            "Handles drought or dryness indicator questions built from thermal and vegetation "
            "signals. Use for TVDI, ATI, drought severity bins, dryness trend, or stress-threshold analysis."
        ),
        question_range=(1, 100),
        tool_allowlist=COMMON_DISCOVERY_AND_TAIL
        + (
            "compute_tvdi",
            "ATI",
            "calculate_tif_average",
            "calculate_threshold_ratio",
            "calc_threshold_value_mean",
            "calc_batch_image_mean_threshold",
            "calc_batch_image_mean_max_min",
            "mann_kendall_test",
        ),
        trigger_keywords=("tvdi", "dryness", "drought", "ati", "thermal inertia", "ndvi", "evi"),
        notes=(
            "Treat TVDI as a dedicated family: pair LST with vegetation index inputs, then do "
            "thresholding, annual averaging, or trend analysis on the resulting dryness rasters.",
        ),
    ),
    "earth-product-timeseries": SkillSpec(
        skill_id="earth-product-timeseries",
        display_name="Product Time-Series Analysis",
        description=(
            "Handles non-RGB Earth observation product questions centered on generic multi-date raster "
            "statistics, trends, averages, minima, maxima, kurtosis, coefficient of variation, and period-to-period comparisons."
        ),
        question_range=(101, 188),
        tool_allowlist=COMMON_DISCOVERY_AND_TAIL
        + (
            "calculate_batch_ndsi",
            "calculate_ndwi",
            "calculate_ndti",
            "calculate_nbr",
            "calculate_water_turbidity_ntu",
            "apply_cloud_mask",
            "calculate_tif_average",
            "calculate_tif_difference",
            "calc_batch_image_sum",
            "calc_batch_image_hotspot_percentage",
            "coefficient_of_variation",
            "kurtosis",
            "kelvin_to_celsius",
            "division",
            "multiply",
            "mann_kendall_test",
            "calc_batch_fire_pixels",
            "identify_fire_prone_areas",
            "index_to_date_range",
        ),
        trigger_keywords=(
            "rainfall",
            "nighttime light",
            "ndsi",
            "snow",
            "ndwi",
            "ndti",
            "nbr",
            "turbidity",
            "hotspot",
            "trend",
        ),
        notes=(
            "Use this generic product skill only when the question is mainly about raster statistics or trends, "
            "not when it requires repeated index derivation or multi-product arithmetic templates.",
        ),
    ),
    "earth-product-derived-index-change": SkillSpec(
        skill_id="earth-product-derived-index-change",
        display_name="Product Derived Index Change",
        description=(
            "Handles product-domain questions that derive NDVI, NDWI, NDTI, NBR, turbidity, or cloud-masked water products "
            "before doing hotspot, direction, percentage, or period-comparison analysis."
        ),
        question_range=(101, 188),
        tool_allowlist=COMMON_DISCOVERY_AND_TAIL
        + (
            "calculate_ndvi",
            "calculate_ndwi",
            "calculate_ndti",
            "calculate_nbr",
            "calculate_water_turbidity_ntu",
            "apply_cloud_mask",
            "calculate_tif_average",
            "calc_batch_image_hotspot_percentage",
            "calc_batch_image_hotspot_tif",
            "analyze_hotspot_direction",
            "max_value_and_index",
            "multiply",
            "sens_slope",
            "mann_kendall_test",
        ),
        trigger_keywords=(
            "ndvi",
            "ndwi",
            "ndti",
            "nbr",
            "cloud-masked",
            "turbidity",
            "water body",
            "hotspot",
            "fire risk",
        ),
        notes=(
            "Prefer canonical repeated transform patterns when the benchmark computes a derived index separately for multiple dates or periods.",
        ),
    ),
    "earth-product-raster-arithmetic": SkillSpec(
        skill_id="earth-product-raster-arithmetic",
        display_name="Product Raster Arithmetic",
        description=(
            "Handles product-domain questions whose benchmark trajectory relies on arithmetic over multiple raster products, "
            "such as built-volume subtraction, energy-saving ratios, multi-image sums, division chains, and percentage change."
        ),
        question_range=(101, 188),
        tool_allowlist=COMMON_DISCOVERY_AND_TAIL
        + (
            "calculate_tif_average",
            "calc_batch_image_sum",
            "division",
            "percentage_change",
            "subtract",
            "compute_linear_trend",
            "calc_batch_image_mean",
        ),
        trigger_keywords=(
            "built_volume",
            "volume",
            "commercial energy saving",
            "residential volume",
            "non-residential",
            "percentage of change",
        ),
        notes=(
            "Use when the target reasoning is dominated by arithmetic composition across several raster products rather than a single index family.",
        ),
    ),
    "earth-rgb-perception-change": SkillSpec(
        skill_id="earth-rgb-perception-change",
        display_name="RGB Perception And Change",
        description=(
            "Handles RGB Earth observation image tasks including scene classification, counting, "
            "visual grounding, geometry, segmentation-based area measurement, and before/after change analysis."
        ),
        question_range=(189, 248),
        tool_allowlist=(
            "get_filelist",
            "MSCN",
            "InstructSAM",
            "RemoteSAM",
            "SM3Det",
            "SAM2",
            "ChangeOS",
            "bboxes2centroids",
            "centroid_distance_extremes",
            "calculate_area",
            "calculate_bbox_area",
            "count_skeleton_contours",
            "get_list_object_via_indexes",
            "difference",
            "division",
            "multiply",
            "ceil_number",
        ),
        trigger_keywords=(
            "image",
            "images",
            "building",
            "airport",
            "industrial",
            "centroid",
            "distance",
            "disaster",
            "destroyed",
            "restoration",
        ),
        notes=(
            "Use classification for scene labels, InstructSAM for counted object classes, "
            "RemoteSAM for phrase grounding, SM3Det for box geometry, and ChangeOS/SAM2/calculate_area "
            "for building-region area or damage questions.",
        ),
    ),
}


def route_skill(question_id: str, question: str, file_list: list[str] | None = None) -> SkillSpec:
    q_lower = question.lower()
    files_lower = " ".join((file_list or [])).lower()
    combined = f"{q_lower} {files_lower}"

    # Prefer semantic routing so skills still generalize outside the fixed
    # benchmark id ranges. Question id is used only as a fallback prior.
    if re.search(r"\.(png|jpg|jpeg)\b", combined) or any(
        k in q_lower
        for k in (
            "scene",
            "airport",
            "industrial",
            "building area",
            "built-up area",
            "centroid",
            "distance",
            "closest",
            "westernmost",
            "destroyed",
            "disaster",
            "restor",
            "harbor",
        )
    ):
        return SKILL_SPECS["earth-rgb-perception-change"]

    if any(
        k in q_lower
        for k in ("tvdi", "dryness", "drought", "thermal inertia")
    ) or re.search(r"\bati\b", q_lower):
        return SKILL_SPECS["earth-spectrum-drought-stress"]
    if "ndvi" in q_lower and "lst" in q_lower and any(k in q_lower for k in ("severity", "stress", "dry")):
        return SKILL_SPECS["earth-spectrum-drought-stress"]

    if any(
        k in q_lower
        for k in (
            "split-window",
            "split window",
            "single-channel",
            "single channel",
            "multi-channel",
            "tes",
            "emissivity",
            "band 31",
            "band 32",
            "ttm",
            "modis day",
            "modis night",
            "lst",
        )
    ):
        return SKILL_SPECS["earth-spectrum-thermal-retrieval"]

    if any(
        k in q_lower
        for k in (
            "built_volume",
            "residential volume",
            "non-residential",
            "commercial energy saving",
            "percentage of change",
        )
    ):
        return SKILL_SPECS["earth-product-raster-arithmetic"]
    if any(
        k in q_lower
        for k in (
            "ndvi",
            "ndwi",
            "ndti",
            "nbr",
            "cloud-masked",
            "cloud masked",
            "turbidity",
            "water body",
            "fire risk",
            "hotspot",
        )
    ):
        return SKILL_SPECS["earth-product-derived-index-change"]

    qid = int(question_id) if str(question_id).isdigit() else None

    if qid is not None and qid <= 100:
        return SKILL_SPECS["earth-spectrum-thermal-retrieval"]

    if qid is not None and qid <= 188:
        return SKILL_SPECS["earth-product-timeseries"]

    return SKILL_SPECS["earth-rgb-perception-change"]


def tools_for_skill(skill: SkillSpec, catalog: list[ToolMeta]) -> list[ToolMeta]:
    allow = set(skill.tool_allowlist)
    selected = [tool for tool in catalog if tool.canonical_name in allow]
    selected.sort(key=lambda t: (t.toolkit, t.canonical_name))
    return selected
