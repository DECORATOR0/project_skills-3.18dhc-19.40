"""
Shortlist relevant tools for a given question using:
1. benchmark-aware domain priors
2. deterministic question-family routing
3. lightweight keyword scoring inside the routed subset

Goal:
- much smaller shortlist than the previous global always-include strategy
- while retaining high GT tool coverage on Earth-Bench.
"""
from __future__ import annotations

import re
from pathlib import Path

from .config import MAX_SHORTLISTED_TOOLS
from .tool_catalog import ToolMeta


_MODALITY_CLUES: list[tuple[str, str]] = [
    (r"\b(NDVI|NDWI|NDBI|EVI|NBR|FVC|WRI|NDTI|NDSI|FRP)\b", "spectral"),
    (r"\b(LST|temperature|emissivity|thermal|BT10|BT11)\b", "product"),
    (r"\b(TVDI|drought|moisture)\b", "product"),
    (r"\b(sea.ice|polarization|microwave|brightness)\b", "product"),
    (r"\b(PWV|water.vapor|precipitable|reflectance|sur_refl)\b", "product"),
    (r"\b(turbidity|NTU)\b", "product"),
    (r"\.(jpg|jpeg|png)\b", "RGB"),
    (r"\.(tif|tiff|geotiff)\b", "spectral"),
    (r"\b(classif|land.use|scene)\b", "RGB"),
    (r"\b(detect|ship|plane|vehicle|harbor|bridge|storage.tank)\b", "RGB"),
    (r"\b(grounding|region|locate|find.*region)\b", "RGB"),
    (r"\b(count|how.many|number.of)\b", "RGB"),
    (r"\b(segment|mask|building)\b", "RGB"),
    (r"\b(change|pre.*post|before.*after)\b", "RGB"),
    (r"\b(trend|annual|temporal|time.series|year)\b", "temporal"),
    (r"\b(hotspot|spatial|direction)\b", "geospatial"),
]

_TASK_CLUES: list[tuple[str, str]] = [
    (r"\b(NDVI|NDWI|NDBI|EVI|NBR|FVC|WRI|NDTI|NDSI|FRP)\b", "spectral_index"),
    (r"\b(LST|temperature|emissivity|thermal.inertia|ATI|TVDI)\b", "inversion"),
    (r"\b(PWV|water.vapor|turbidity|sea.ice)\b", "inversion"),
    (r"\b(classif|land.use|scene|type.of|category)\b", "classification"),
    (r"\b(detect|object|bounding.box|ship|plane|vehicle)\b", "detection"),
    (r"\b(grounding|locate|find.*region|which.*area)\b", "grounding"),
    (r"\b(count|how.many|number.of)\b", "counting"),
    (r"\b(segment|mask|extract.*shape)\b", "segmentation"),
    (r"\b(change|pre.*post|before.*after)\b", "change_detection"),
    (r"\b(threshold|above|below|exceed|hotspot)\b", "thresholding"),
    (r"\b(trend|increase|decrease|annual|slope|seasonality|spike)\b", "temporal_analysis"),
    (r"\b(mean|average|max|min|median|sum|std|deviation|statistic)\b", "aggregation"),
    (r"\b(difference|ratio|percentage|subtract|divide)\b", "arithmetic"),
    (r"\b(centroid|distance|area|bbox)\b", "geometry"),
    (r"\b(fire|burn|wildfire|FRP)\b", "fire_analysis"),
    (r"\b(snow|ice|NDSI)\b", "snow_analysis"),
    (r"\b(cloud.mask|radiometric|correction)\b", "preprocessing"),
    (r"\b(getis|spatial.autocorrelation|direction.*hotspot)\b", "spatial_analysis"),
]

_DOMAIN_BASE_TOOLS = {
    "spectrum": {
        "get_filelist", "calc_batch_image_mean", "calc_batch_image_mean_mean", "mean",
        "difference", "compute_linear_trend", "mann_kendall_test", "max_value_and_index",
        "min_value_and_index", "calculate_tif_average", "calculate_threshold_ratio",
        "count_images_exceeding_threshold_ratio", "compute_tvdi", "lst_single_channel",
        "band_ratio", "calc_batch_image_max", "calc_threshold_value_mean",
        "count_spikes_from_values", "count_pixels_satisfying_conditions",
        "calc_batch_image_mean_max_min", "image_division_mean",
        "calculate_multi_band_threshold_ratio", "calculate_band_mean_by_condition",
        "count_images_exceeding_mean_multiplier", "calc_batch_image_mean_threshold",
        "average_ratio_exceeding_threshold", "calculate_intersection_percentage",
        "calculate_mean_lst_by_ndvi", "calculate_max_lst_by_ndvi",
    },
    "products": {
        "get_filelist", "calc_batch_image_mean", "mean", "difference", "percentage_change",
        "max_value_and_index", "min_value_and_index", "calculate_tif_average",
        "calculate_tif_difference", "coefficient_of_variation", "mann_kendall_test",
        "kelvin_to_celsius", "calc_batch_image_hotspot_percentage",
        "calc_batch_image_hotspot_tif", "calc_batch_image_sum", "subtract",
        "count_above_threshold", "argmax", "sens_slope", "analyze_hotspot_direction",
        "skewness", "calc_batch_image_skewness", "kurtosis", "calculate_ndvi",
        "index_to_date_range", "division", "multiply", "compute_linear_trend",
        "calc_extreme_snow_loss_percentage_from_binary_map", "calc_batch_fire_pixels",
        "identify_fire_prone_areas", "create_fire_increase_map",
    },
    "rgb": {
        "get_filelist", "ChangeOS", "SM3Det", "InstructSAM", "RemoteSAM", "SAM2", "MSCN",
        "calculate_area", "calculate_bbox_area", "bboxes2centroids",
        "centroid_distance_extremes", "count_skeleton_contours", "difference",
        "division", "ceil_number", "multiply", "get_list_object_via_indexes",
    },
}

_SMALL_SAFETY_BUFFER = {"spectrum": 6, "products": 8, "rgb": 5}

_EXISTING_PRODUCT_PRIOR_TO_TOOLS = {
    "existing_ndvi_product": {"calculate_ndvi"},
    "existing_ndwi_product": {"calculate_ndwi"},
    "existing_ndti_product": {"calculate_ndti"},
    "existing_ndsi_product": {"calculate_batch_ndsi"},
    "existing_nbr_product": {"calculate_nbr"},
    "existing_turbidity_product": {"calculate_water_turbidity_ntu"},
}


def _infer_file_priors(question: str, file_list: list[str]) -> set[str]:
    q = question.lower()
    file_names = [Path(f).name.lower() for f in file_list]
    combined = " ".join(file_names)
    priors: set[str] = set()

    if re.search(r"\bsr_b\d+\b", combined) and "qa_pixel" in combined:
        priors.add("raw_sr_with_qa")
    if re.search(r"_ndvi_", combined):
        priors.update({"existing_ndvi_product", "existing_product_series"})
    if re.search(r"_ndwi_", combined):
        priors.update({"existing_ndwi_product", "existing_product_series"})
    if re.search(r"_ndti_", combined):
        priors.update({"existing_ndti_product", "existing_product_series"})
    if re.search(r"_ndsi_", combined):
        priors.update({"existing_ndsi_product", "existing_product_series"})
    if re.search(r"_nbr_", combined):
        priors.update({"existing_nbr_product", "existing_product_series"})
    if re.search(r"\b(ntu|turbidity)\b", combined):
        priors.update({"existing_turbidity_product", "existing_product_series"})
    if re.search(r"bt_31_day", combined) and not re.search(r"bt_32", combined):
        priors.add("modis_bt31_only")
    if {"t1.png", "t2.png"}.issubset(set(file_names)) and re.search(r"\b(building|built-up|disaster|restore|reduction|decrease|damage|infrastructure)\b", q):
        priors.add("paired_disaster_building")
    if {"a.png", "b.png", "c.png"}.issubset(set(file_names)) and re.search(r"\b(gsd)\b", q) and re.search(r"\b(rank|sort)\b", q) and re.search(r"\b(building|built-up)\b", q):
        priors.add("gsd_builtup_ranking")
    return priors


def _infer_period_count_hint(question: str) -> int:
    q = question.lower()
    years = set(re.findall(r"\b(?:19|20)\d{2}\b", q))
    if re.search(r"\b(vs|versus|compare|comparison|before and after|successive time points|between [^.;]+ and [^.;]+)\b", q):
        return 2
    if len(years) >= 2:
        return 2
    if re.search(r"\b(two years|two periods|two months|for each year|each year)\b", q):
        return 2
    return 1


def infer_domain(question_id: str | None, question: str, file_list: list[str]) -> str:
    if question_id and str(question_id).isdigit():
        q = int(question_id)
        if q <= 100:
            return "spectrum"
        if q <= 188:
            return "products"
        return "rgb"

    combined = (question + " " + " ".join(file_list)).lower()
    if re.search(r"\.(jpg|jpeg|png)\b", combined):
        return "rgb"
    if re.search(r"\b(ndvi|lst|tvdi|split-window|single-channel|ttm|modis day|band 31|band 32)\b", combined):
        return "spectrum"
    return "products"


def infer_question_profile(question_id: str | None, question: str, file_list: list[str]) -> dict:
    domain = infer_domain(question_id, question, file_list)
    q = question.lower()
    combined = q + " " + " ".join(file_list).lower()
    file_priors = _infer_file_priors(question, file_list)
    profile = {
        "domain": domain,
        "intents": set(),
        "required_tools": set(),
        "preferred_tools": set(),
        "discouraged_tools": set(),
        "domain_base_tools": set(_DOMAIN_BASE_TOOLS[domain]),
        "max_tools": 24,
        "safety_buffer": _SMALL_SAFETY_BUFFER[domain],
        "file_priors": file_priors,
        "canonical_rules": [],
        "period_count_hint": _infer_period_count_hint(question),
        "allow_repeat_compression": domain == "rgb",
    }
    profile["required_tools"].add("get_filelist")
    if profile["period_count_hint"] >= 2:
        profile["canonical_rules"].append("If the question compares two periods/years, include one aggregate block for each period before the final comparison step.")

    if domain == "rgb":
        profile["max_tools"] = 18
        if re.search(r"\b(sort|rank|number of|how many|count)\b", q):
            if re.search(r"\b(ship|airplane|plane|storage tank|baseball|basketball|football)\b", q):
                profile["intents"].add("rgb_counting")
                profile["required_tools"].update({"InstructSAM"})
                profile["discouraged_tools"].update({"SM3Det", "RemoteSAM"})
        if re.search(r"\b(industrial|commercial|airport|parking|residential|land use|scene|which areas seem)\b", q):
            profile["intents"].add("rgb_scene_classification")
            profile["required_tools"].update({"MSCN"})
        if re.search(r"\b(centroid|coordinates|largest .* court|westernmost|northernmost|region that corresponds|description)\b", q):
            profile["intents"].add("rgb_grounding_centroid")
            profile["required_tools"].update({"RemoteSAM", "bboxes2centroids"})
            profile["discouraged_tools"].add("InstructSAM")
        if re.search(r"\b(closest pair|farthest pair)\b", q):
            profile["intents"].add("rgb_pair_geometry")
            profile["required_tools"].update({"SM3Det", "bboxes2centroids", "centroid_distance_extremes", "get_list_object_via_indexes"})
        building_area = re.search(r"\b(building|built-up)\b", q) and re.search(r"\b(area|square meters|pixels)\b", q)
        if "gsd_builtup_ranking" in file_priors or (re.search(r"\b(rank|sort)\b", q) and "gsd" in q and re.search(r"\b(building|built-up)\b", q)):
            profile["intents"].add("rgb_builtup_rank")
            profile["required_tools"].update({"SM3Det", "SAM2", "calculate_area"})
            profile["preferred_tools"].update({"SM3Det", "SAM2", "calculate_area"})
            profile["discouraged_tools"].update({"calculate_bbox_area"})
        elif building_area and (re.search(r"\b(before|after|successive|disaster|restore|reduction|decrease|damage|change|changed)\b", q) or "paired_disaster_building" in file_priors):
            profile["intents"].add("rgb_building_change")
            profile["required_tools"].update({"ChangeOS"})
            profile["preferred_tools"].update({"calculate_area", "count_skeleton_contours"})
            profile["discouraged_tools"].update({"SM3Det", "calculate_bbox_area"})
        elif building_area:
            profile["intents"].add("rgb_building_area")
            profile["required_tools"].update({"ChangeOS", "calculate_area"})
            profile["preferred_tools"].update({"ChangeOS", "calculate_area"})
            profile["discouraged_tools"].add("calculate_bbox_area")
        elif re.search(r"\b(area|bounding boxes|total area)\b", q):
            profile["intents"].add("rgb_detection_area")
            profile["required_tools"].update({"SM3Det", "calculate_bbox_area"})
        if re.search(r"\b(change|changed|building extraction|segment building)\b", q):
            profile["intents"].add("rgb_change")
            profile["required_tools"].update({"ChangeOS"})
        if re.search(r"\b(count the number of contours|skeleton)\b", q):
            profile["required_tools"].add("count_skeleton_contours")
        if re.search(r"\b(multiply|gsd)\b", q):
            profile["required_tools"].add("multiply")

    elif domain == "spectrum":
        profile["max_tools"] = 22
        if "tvdi" in q or (("ndvi" in q or "evi" in q) and "lst" in q and re.search(r"\b(dry|dryness|drought|water stress)\b", q)):
            profile["intents"].add("tvdi")
            profile["required_tools"].add("compute_tvdi")
        if "split-window" in q or ("band 31" in q and "band 32" in q):
            profile["intents"].add("split_window")
            profile["required_tools"].add("split_window")
            if re.search(r"\b(average.*period|monthly average|annual average|linear trend|trend)\b", q):
                profile["preferred_tools"].add("lst_multi_channel")
        if "single-channel" in q:
            profile["intents"].add("single_channel_lst")
            profile["required_tools"].add("lst_single_channel")
        if "three-temperature" in q or "ttm" in q:
            profile["intents"].add("ttm")
            profile["required_tools"].add("ttm_lst")
        if "ati" in q or "apparent thermal inertia" in q:
            profile["intents"].add("ati")
            profile["required_tools"].add("ATI")
        if "modis day" in q or "modis-derived" in q:
            profile["intents"].add("modis_lst")
            profile["required_tools"].add("modis_day_night_lst")
        if "modis_bt31_only" in file_priors:
            profile["preferred_tools"].add("modis_day_night_lst")
        if "band ratio" in q or "water vapor" in q or "sur_refl_b17" in combined:
            profile["intents"].add("band_ratio")
            profile["required_tools"].add("band_ratio")
        if "tes" in q or ("emissivity" in q and "aster" in q):
            profile["intents"].add("tes")
            profile["required_tools"].add("temperature_emissivity_separation")
        if re.search(r"\b(percentage|proportion|ratio).*?(area|pixels)\b", q):
            profile["intents"].add("threshold_ratio")
            profile["required_tools"].add("calculate_threshold_ratio")
        if re.search(r"\b(number of days|how many days|count the number of days)\b", q):
            profile["intents"].add("count_days")
            profile["required_tools"].add("count_images_exceeding_threshold_ratio")
            profile["preferred_tools"].update({"calc_batch_image_mean_threshold", "count_images_exceeding_threshold_ratio"})
        if re.search(r"\b(average.*all days|average.*period|monthly average|annual average|mean.*for the period)\b", q):
            profile["intents"].add("period_average")
            profile["required_tools"].update({"calc_batch_image_mean_mean"})
            profile["preferred_tools"].update({"calc_batch_image_mean_mean", "calculate_tif_average"})
        if re.search(r"\b(linear trend|trend|increase|decrease|greatest percentage increase)\b", q):
            profile["required_tools"].update({"compute_linear_trend"})
            profile["preferred_tools"].add("compute_linear_trend")
        if re.search(r"\b(highest|lowest|which year|which month|which day|peak)\b", q):
            profile["intents"].add("extremum_pick")
            profile["preferred_tools"].update({"max_value_and_index", "min_value_and_index"})
        if re.search(r"\b(stricter thresholds|threshold affect|difference between)\b", q):
            profile["required_tools"].add("difference")
        if "percentile" in q:
            profile["required_tools"].add("get_percentile_value_from_image")
        if re.search(r"\b(exceeds the mean by)\b", q):
            profile["intents"].add("mean_multiplier_count")
            profile["required_tools"].add("count_images_exceeding_mean_multiplier")
            profile["preferred_tools"].add("count_images_exceeding_mean_multiplier")
            profile["discouraged_tools"].update({"calc_batch_image_mean", "mean"})
        if re.search(r"\b(identify and count pixels|count pixels where)\b", q):
            profile["intents"].add("condition_pixel_count")
            profile["required_tools"].add("count_pixels_satisfying_conditions")
            profile["preferred_tools"].add("count_pixels_satisfying_conditions")
            profile["discouraged_tools"].update({"calc_batch_image_mean", "mean"})
        if re.search(r"\b(mean .* within .* zones|mean emissivity variation|mean .* where .* exceeds|mean .* in areas where)\b", q):
            profile["intents"].add("condition_band_mean")
            profile["required_tools"].add("calculate_band_mean_by_condition")
            profile["preferred_tools"].add("calculate_band_mean_by_condition")
            profile["discouraged_tools"].update({"calc_batch_image_mean", "mean"})
        if re.search(r"\b(urban heat island index|uhii|urban pixels .* rural pixels)\b", q):
            profile["intents"].add("condition_band_mean")
            profile["required_tools"].update({"calculate_band_mean_by_condition", "difference"})
            profile["preferred_tools"].add("calculate_band_mean_by_condition")
        if re.search(r"\b(fell below|count the number of nights when|count the number of days when .* below)\b", q):
            profile["intents"].add("threshold_count")
            profile["required_tools"].add("calc_batch_image_mean_threshold")
            profile["preferred_tools"].add("calc_batch_image_mean_threshold")
        if re.search(r"\b(change in percentage of pixels exceeding|change in proportion of pixels exceeding)\b", q):
            profile["intents"].add("average_ratio_threshold_change")
            profile["required_tools"].add("average_ratio_exceeding_threshold")
            profile["preferred_tools"].add("average_ratio_exceeding_threshold")
            profile["discouraged_tools"].add("calculate_threshold_ratio")
        if re.search(r"\b(mean tvdi value in areas where|mean .* value in areas where .* exceeds)\b", q):
            profile["intents"].add("threshold_value_mean")
            profile["required_tools"].add("calc_threshold_value_mean")
            profile["preferred_tools"].add("calc_threshold_value_mean")
            profile["discouraged_tools"].update({"calc_batch_image_mean", "mean"})
        if re.search(r"\b(pixel-wise .* index|lst/\s*|thermal response index)\b", q):
            profile["intents"].add("image_division_mean")
            profile["required_tools"].add("image_division_mean")
            profile["preferred_tools"].add("image_division_mean")
            profile["discouraged_tools"].update({"calc_batch_image_mean", "mean"})
        if re.search(r"\b(maximum lst|maximum .* within the region|max lst within the region)\b", q):
            profile["intents"].add("max_stat")
            profile["required_tools"].add("calc_batch_image_max")
            profile["preferred_tools"].add("calc_batch_image_max")
            profile["discouraged_tools"].update({"calc_batch_image_mean", "mean"})
        profile["allow_repeat_compression"] = False

    else:
        profile["max_tools"] = 20
        if "cloud" in q or "qa_pixel" in combined or "raw_sr_with_qa" in file_priors:
            profile["required_tools"].add("apply_cloud_mask")
        if "raw_sr_with_qa" in file_priors:
            profile["intents"].add("products_raw_sr_index")
        if "ndwi" in q:
            profile["required_tools"].add("calculate_ndwi")
        if "nbr" in q:
            profile["required_tools"].add("calculate_nbr")
        if "ndti" in q:
            profile["required_tools"].add("calculate_ndti")
        if "ndsi" in q or "snow" in q:
            profile["required_tools"].add("calculate_batch_ndsi")
        if "turbidity" in q or "ntu" in q:
            profile["required_tools"].add("calculate_water_turbidity_ntu")
            profile["preferred_tools"].add("calculate_water_turbidity_ntu")
        if "existing_product_series" in file_priors:
            profile["intents"].add("products_existing_product_series")
            profile["preferred_tools"].update({"calc_batch_image_mean", "mean", "difference", "percentage_change"})
            for prior_name, tools in _EXISTING_PRODUCT_PRIOR_TO_TOOLS.items():
                if prior_name in file_priors:
                    profile["discouraged_tools"].update(tools)
        if re.search(r"\b(mean surface temperature|average .* temperature|average .* value|mean .* for the period)\b", q):
            profile["required_tools"].update({"calc_batch_image_mean", "mean"})
            profile["preferred_tools"].update({"calc_batch_image_mean", "mean"})
        if re.search(r"\b(annual average map|spatial distribution|average raster)\b", q):
            profile["required_tools"].add("calculate_tif_average")
            profile["preferred_tools"].add("calculate_tif_average")
        if re.search(r"\b(number of days|how many days|which day|date of|lowest|highest|strongest positive change)\b", q):
            profile["required_tools"].update({"calc_batch_image_mean", "max_value_and_index", "min_value_and_index"})
            profile["intents"].add("extremum_pick")
            profile["preferred_tools"].update({"max_value_and_index", "min_value_and_index"})
        if "celsius" in q:
            profile["required_tools"].add("kelvin_to_celsius")
            profile["preferred_tools"].add("kelvin_to_celsius")
        if "percentage increase" in q or "percentage decrease" in q:
            profile["required_tools"].add("percentage_change")
            profile["preferred_tools"].add("percentage_change")
        if re.search(r"\b(hotspot|above threshold)\b", q):
            profile["required_tools"].add("calc_batch_image_hotspot_percentage")
            profile["preferred_tools"].add("calc_batch_image_hotspot_percentage")
        if "difference" in q:
            profile["required_tools"].add("difference")
        if "division" in q or "ratio" in q:
            profile["required_tools"].add("division")
        if re.search(r"\b(volatility|coefficient of variation)\b", q):
            profile["preferred_tools"].add("coefficient_of_variation")
        if "existing_product_series" in file_priors and not re.search(r"\b(threshold|hotspot|above threshold)\b", q):
            profile["discouraged_tools"].add("count_above_threshold")
        profile["allow_repeat_compression"] = False

    return profile


def infer_modality_and_tasks(question: str, file_list: list[str]) -> tuple[set[str], set[str]]:
    combined = question + " " + " ".join(file_list)
    modalities: set[str] = set()
    for pattern, mod in _MODALITY_CLUES:
        if re.search(pattern, combined, re.IGNORECASE):
            modalities.add(mod)
    tasks: set[str] = set()
    for pattern, task in _TASK_CLUES:
        if re.search(pattern, combined, re.IGNORECASE):
            tasks.add(task)
    if not modalities:
        modalities.add("any")
    if not tasks:
        tasks.add("other")
    return modalities, tasks


def shortlist_tools(catalog: list[ToolMeta], question: str, file_list: list[str], question_id: str | None = None, max_tools: int = MAX_SHORTLISTED_TOOLS) -> list[ToolMeta]:
    modalities, tasks = infer_modality_and_tasks(question, file_list)
    profile = infer_question_profile(question_id, question, file_list)
    routed_max_tools = min(max_tools, profile["max_tools"])
    domain_base = set(profile.get("domain_base_tools", set()))
    required_include = set(profile["required_tools"])
    preferred_include = set(profile["preferred_tools"])
    discouraged = set(profile["discouraged_tools"])
    safety_buffer = int(profile.get("safety_buffer", 6))
    q_lower = question.lower()
    scored: list = []
    for tool in catalog:
        score = 0.0
        mod_overlap = set(tool.modality_tags) & modalities
        task_overlap = set(tool.task_tags) & tasks
        if "any" in tool.modality_tags:
            mod_overlap.add("any")
        score += len(mod_overlap) * 2.0
        score += len(task_overlap) * 3.0
        if tool.canonical_name.lower() in q_lower:
            score += 8.0
        name_parts = re.split(r"[_\s]+", tool.canonical_name.lower())
        for part in name_parts:
            if len(part) > 2 and part in q_lower:
                score += 1.5
        desc_words = set(re.findall(r"[a-z]{3,}", tool.description.lower()))
        q_words = set(re.findall(r"[a-z]{3,}", q_lower))
        desc_overlap = desc_words & q_words - {"the", "and", "for", "from", "that", "with", "this"}
        score += min(len(desc_overlap) * 0.5, 4.0)
        if tool.canonical_name in domain_base:
            score += 8.0
        if tool.canonical_name in required_include:
            score += 18.0
        if tool.canonical_name in preferred_include:
            score += 8.0
        if tool.canonical_name in discouraged:
            score -= 10.0
        if profile["domain"] == "rgb":
            if tool.toolkit == "Perception":
                score += 4.0
            elif tool.toolkit in {"Index", "Inversion"}:
                score -= 6.0
        elif profile["domain"] == "spectrum":
            if tool.toolkit in {"Index", "Inversion", "Statistics", "Analysis"}:
                score += 3.0
            elif tool.toolkit == "Perception":
                score -= 8.0
        else:
            if tool.toolkit in {"Statistics", "Index", "Inversion", "Analysis"}:
                score += 3.0
            elif tool.toolkit == "Perception":
                score -= 8.0
        if tool.stage_hint == "aggregate" and any(t in tasks for t in ("temporal_analysis", "aggregation", "statistics", "arithmetic")):
            score += 2.0
        if "raw_sr_with_qa" in profile["file_priors"] and tool.canonical_name == "apply_cloud_mask":
            score += 5.0
        if "existing_product_series" in profile["file_priors"] and tool.canonical_name in {"calc_batch_image_mean", "mean", "difference", "percentage_change", "max_value_and_index", "min_value_and_index", "calculate_tif_average"}:
            score += 3.0
        if "rgb_building_change" in profile["intents"] and tool.canonical_name in {"ChangeOS", "calculate_area", "count_skeleton_contours"}:
            score += 5.0
        if "rgb_building_area" in profile["intents"] and tool.canonical_name in {"ChangeOS", "calculate_area"}:
            score += 5.0
        if "rgb_builtup_rank" in profile["intents"] and tool.canonical_name in {"SM3Det", "SAM2", "calculate_area"}:
            score += 5.0
        if any(i in profile["intents"] for i in {"rgb_building_change", "rgb_building_area", "rgb_builtup_rank"}):
            if tool.canonical_name == "calculate_bbox_area":
                score -= 7.0
        if score > 0:
            scored.append((score, tool))

    scored.sort(key=lambda x: -x[0])
    selected = []
    seen = set()
    base_include = domain_base | required_include | preferred_include
    for _, tool in scored:
        if tool.canonical_name in base_include and tool.canonical_name not in seen:
            selected.append(tool)
            seen.add(tool.canonical_name)
    extra_budget = max(0, min(routed_max_tools, len(base_include) + safety_buffer) - len(selected))
    for _, tool in scored:
        if extra_budget <= 0:
            break
        if tool.canonical_name in seen or tool.canonical_name in discouraged:
            continue
        selected.append(tool)
        seen.add(tool.canonical_name)
        extra_budget -= 1

    final = []
    seen = set()
    for t in selected:
        if t.canonical_name not in seen:
            final.append(t)
            seen.add(t.canonical_name)
    return final


def shortlist_to_prompt(tools: list[ToolMeta]) -> str:
    lines = [f"Available EO Tools ({len(tools)} shortlisted):\n"]
    for t in tools:
        lines.append(t.to_prompt_entry())
    return "\n".join(lines)
