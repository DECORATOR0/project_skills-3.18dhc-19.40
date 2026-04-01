"""
Skill-specific planning heuristics distilled from question_sync families.

This planner is intentionally distinct from the older debate/reflection pipeline.
It produces a family-aware tool chain per skill, then the parameter worker fills
arguments step by step for the planned tools.
"""
from __future__ import annotations

import re
from typing import Iterable

from .schemas import PlannedSkillStep
from .schemas import BenchmarkItem
from .skill_router import SkillSpec


def _extract_years(text: str) -> list[int]:
    return [int(y) for y in re.findall(r"\b(19\d{2}|20\d{2})\b", text)]


def _period_block_count(question: str) -> int:
    years = _extract_years(question)
    if len(years) >= 2 and years[-1] >= years[0]:
        span = years[-1] - years[0] + 1
        if 2 <= span <= 8:
            return span
    # fallback for two-period compare language
    q = question.lower()
    if any(k in q for k in ("between two", "compare", "difference", "before and after", "summer and autumn")):
        return 2
    return 1


def _file_count(item: BenchmarkItem) -> int:
    return max(1, len(item.file_list))


def _repeat(tool_name: str, n: int, rationale: str) -> list[PlannedSkillStep]:
    return [PlannedSkillStep(tool_name=tool_name, rationale=rationale) for _ in range(max(1, n))]


def _choose_spectrum_transform(question: str) -> str:
    q = question.lower()
    trend_like = any(k in q for k in ("trend", "mann", "sen", "annual", "yearly"))
    if ("split-window" in q or "split window" in q) and trend_like:
        return "lst_multi_channel"
    if "split-window" in q or "split window" in q:
        return "split_window"
    if "single-channel" in q or "single channel" in q:
        return "lst_single_channel"
    if "tes" in q or "temperature-emissivity separation" in q or "emissivity variation" in q:
        return "temperature_emissivity_separation"
    if "modis" in q and ("day and night" in q or "daytime" in q or "nighttime" in q):
        return "modis_day_night_lst"
    if "pwv" in q or "water vapor" in q or "precipitable water vapor" in q:
        return "band_ratio"
    if "ttm" in q:
        return "ttm_lst"
    return "lst_multi_channel"


def _summarize(plan: Iterable[PlannedSkillStep]) -> list[PlannedSkillStep]:
    return list(plan)


def _plan_spectrum_drought(item: BenchmarkItem) -> list[PlannedSkillStep]:
    q = item.question_text.lower()
    years = _period_block_count(item.question_text)
    transform = "ATI" if (re.search(r"\bati\b", q) or "thermal inertia" in q) else "compute_tvdi"

    plan: list[PlannedSkillStep] = [PlannedSkillStep("get_filelist", "Discover available rasters first.")]
    plan.append(PlannedSkillStep(transform, "Derive the drought or dryness indicator."))

    if "trend" in q or "annual" in q:
        plan.extend(_repeat("calculate_tif_average", years, "Aggregate dryness indicator by year."))
        plan.append(PlannedSkillStep("calc_batch_image_mean", "Convert yearly rasters into yearly scalar summaries."))
        plan.append(PlannedSkillStep("compute_linear_trend", "Estimate the linear annual trend."))
        return _summarize(plan)

    if "severity" in q or "drought class" in q or "threshold" in q or "proportion" in q:
        plan.append(PlannedSkillStep("calculate_threshold_ratio", "Measure the drought-threshold proportion."))
        return _summarize(plan)

    if "mean" in q and ("where" in q or "exceeds" in q or "above" in q):
        plan.append(PlannedSkillStep("calc_threshold_value_mean", "Compute mean dryness under a threshold condition."))
        return _summarize(plan)

    if "difference" in q or "compare" in q:
        plan.extend(_repeat("calculate_threshold_ratio", 2, "Compute period-specific drought ratios."))
        plan.append(PlannedSkillStep("difference", "Compare the two drought summaries."))
        return _summarize(plan)

    plan.append(PlannedSkillStep("calc_batch_image_mean", "Summarize the indicator over the relevant files."))
    return _summarize(plan)


def _plan_spectrum_thermal(item: BenchmarkItem) -> list[PlannedSkillStep]:
    q = item.question_text.lower()
    transform = _choose_spectrum_transform(item.question_text)
    period_count = _period_block_count(item.question_text)
    years = _extract_years(item.question_text)
    annual_blocks = max(1, years[-1] - years[0] + 1) if len(years) >= 2 and years[-1] >= years[0] else 1

    plan: list[PlannedSkillStep] = [PlannedSkillStep("get_filelist", "Discover available rasters first.")]

    if "difference" in q or "compare" in q:
        plan.extend(_repeat(transform, period_count, "Retrieve the target thermal product for each period."))
        if any(k in q for k in ("average", "monthly average", "seasonal average", "annual average", "mean")):
            plan.extend(_repeat("calc_batch_image_mean_mean", period_count, "Summarize each period."))
        elif "ratio" in q or "percentage" in q or "proportion" in q:
            plan.extend(_repeat("calculate_threshold_ratio", period_count, "Compute each period's threshold ratio."))
        else:
            plan.extend(_repeat("calc_batch_image_mean", period_count, "Summarize each period's outputs."))
        plan.append(PlannedSkillStep("difference", "Compare the two periods."))
        return _summarize(plan)

    plan.append(PlannedSkillStep(transform, "Retrieve the target thermal product."))

    if "how many" in q or "count" in q:
        if "mean multiplier" in q:
            plan.append(PlannedSkillStep("count_images_exceeding_mean_multiplier", "Count images exceeding the mean multiplier."))
        else:
            plan.append(PlannedSkillStep("count_images_exceeding_threshold_ratio", "Count images exceeding the threshold ratio."))
        return _summarize(plan)

    if "proportion" in q or "percentage" in q or "ratio" in q:
        plan.append(PlannedSkillStep("calculate_threshold_ratio", "Measure the threshold proportion."))
        return _summarize(plan)

    if "mean" in q and ("where" in q or "condition" in q or "exceeds" in q):
        plan.append(PlannedSkillStep("calc_threshold_value_mean", "Compute a threshold-conditioned mean."))
        return _summarize(plan)

    if "trend" in q or "mann" in q or "sen" in q:
        plan.append(PlannedSkillStep(transform, "Retrieve the target thermal product."))
        plan.extend(_repeat("calc_batch_image_mean", annual_blocks, "Summarize each year or period."))
        plan.extend(_repeat("mean", annual_blocks, "Convert each summary to a scalar mean."))
        if "mann" in q or "sen" in q:
            plan.append(PlannedSkillStep("mann_kendall_test", "Estimate the monotonic trend and significance."))
        else:
            plan.append(PlannedSkillStep("compute_linear_trend", "Estimate the linear trend."))
        return _summarize(plan)

    if "band mean" in q:
        plan.append(PlannedSkillStep("calculate_band_mean_by_condition", "Compute a band mean under a condition."))
        return _summarize(plan)

    if "maximum" in q or "max " in q:
        plan.append(PlannedSkillStep("calc_batch_image_max", "Return the maximum thermal summary."))
        return _summarize(plan)

    if "average" in q or "mean" in q:
        plan.append(PlannedSkillStep("calc_batch_image_mean_mean", "Summarize thermal outputs."))
        return _summarize(plan)

    return _summarize(plan)


def _plan_products(item: BenchmarkItem) -> list[PlannedSkillStep]:
    q = item.question_text.lower()
    files_text = " ".join(item.file_list).lower()
    plan: list[PlannedSkillStep] = [PlannedSkillStep("get_filelist", "Discover available rasters first.")]

    existing_product = any(k in files_text for k in ("ndvi", "ndsi", "ndwi", "ndti", "night", "precipitation", "rainfall"))

    if "snow" in q or "ndsi" in q:
        if not existing_product:
            plan.append(PlannedSkillStep("calculate_batch_ndsi", "Derive snow product rasters first."))
        if "stability" in q or "coefficient of variation" in q:
            plan.extend(_repeat("calc_batch_image_mean", 2, "Summarize the compared periods."))
            plan.extend(_repeat("coefficient_of_variation", 2, "Compute per-period variability."))
            plan.append(PlannedSkillStep("difference", "Compare the snow variability."))
        elif "change" in q or "compare" in q or "difference" in q:
            plan.extend(_repeat("calc_batch_image_mean", 2, "Summarize each period."))
            plan.extend(_repeat("mean", 2, "Average each period summary."))
            plan.append(PlannedSkillStep("difference", "Compare the periods."))
            if "percent" in q:
                plan.append(PlannedSkillStep("percentage_change", "Convert the difference into percentage change."))
        else:
            plan.append(PlannedSkillStep("calc_batch_image_mean", "Summarize snow cover rasters."))
        return _summarize(plan)

    if "turbidity" in q or "ntu" in q:
        if not existing_product:
            plan.append(PlannedSkillStep("calculate_water_turbidity_ntu", "Derive turbidity rasters from input bands."))
        plan.append(PlannedSkillStep("calc_batch_image_mean", "Summarize turbidity across the series."))
        if "trend" in q:
            plan.append(PlannedSkillStep("mann_kendall_test", "Estimate the trend direction."))
        return _summarize(plan)

    if "fire" in q or "burn" in q or "hotspot" in q:
        if "prone" in q:
            plan.append(PlannedSkillStep("identify_fire_prone_areas", "Create a fire-prone area map."))
        else:
            plan.append(PlannedSkillStep("calc_batch_image_hotspot_percentage", "Measure hotspot percentage over time."))
        if "max" in q:
            plan.append(PlannedSkillStep("max_value_and_index", "Return the most extreme fire statistic."))
        return _summarize(plan)

    if "trend" in q or "linear regression" in q:
        plan.append(PlannedSkillStep("calc_batch_image_mean", "Summarize each period or year."))
        plan.append(PlannedSkillStep("compute_linear_trend", "Estimate the trend."))
        return _summarize(plan)

    if "kurtosis" in q:
        plan.append(PlannedSkillStep("calc_batch_image_mean", "Summarize the raster series."))
        plan.append(PlannedSkillStep("kurtosis", "Measure distribution shape."))
        return _summarize(plan)

    if "coldest" in q or "minimum" in q:
        plan.append(PlannedSkillStep("calc_batch_image_mean", "Summarize each period."))
        plan.append(PlannedSkillStep("min_value_and_index", "Find the minimum value and its index."))
        return _summarize(plan)

    if "maximum percentage increase" in q or ("percentage increase" in q and "max" in q):
        plan.append(PlannedSkillStep("calc_batch_image_mean", "Summarize each period."))
        plan.append(PlannedSkillStep("percentage_change", "Compute percentage changes."))
        plan.append(PlannedSkillStep("max_value_and_index", "Find the peak increase."))
        return _summarize(plan)

    if "difference" in q or "compare" in q:
        plan.extend(_repeat("calc_batch_image_mean", 2, "Summarize each compared series."))
        plan.extend(_repeat("mean", 2, "Average each compared series."))
        plan.append(PlannedSkillStep("difference", "Compare the two summaries."))
        return _summarize(plan)

    if "celsius" in q:
        plan.append(PlannedSkillStep("calc_batch_image_mean", "Summarize the thermal inputs."))
        plan.append(PlannedSkillStep("kelvin_to_celsius", "Convert the summary to Celsius."))
        return _summarize(plan)

    plan.append(PlannedSkillStep("calc_batch_image_mean", "Default summary of the product series."))
    return _summarize(plan)


def _plan_products_derived_index(item: BenchmarkItem) -> list[PlannedSkillStep]:
    q = item.question_text.lower()
    plan: list[PlannedSkillStep] = [PlannedSkillStep("get_filelist", "Discover available rasters first.")]

    if "ndwi" in q and "cloud" not in q:
        if "difference" in q or "compare" in q or "water body" in q or "water loss" in q:
            plan.extend(_repeat("calculate_tif_average", 2, "Average each compared period before deriving NDWI."))
            plan.extend(_repeat("calculate_ndwi", 2, "Derive NDWI for each compared period."))
            if "water body" in q or "water loss" in q or "percentages" in q or "proportion" in q:
                plan.extend(_repeat("calc_batch_image_hotspot_percentage", 2, "Measure the NDWI-defined water-body proportion per period."))
            else:
                plan.extend(_repeat("calc_batch_image_mean", 2, "Summarize each NDWI period."))
                plan.extend(_repeat("mean", 2, "Average each NDWI summary."))
            if "peak" in q or "most severe" in q or "max" in q:
                plan.append(PlannedSkillStep("max_value_and_index", "Find the most extreme period."))
            else:
                plan.append(PlannedSkillStep("difference", "Compare the NDWI-derived period statistics."))
            return _summarize(plan)
        plan.append(PlannedSkillStep("calculate_ndwi", "Derive NDWI from the relevant inputs."))
        plan.append(PlannedSkillStep("calc_batch_image_mean", "Summarize the NDWI outputs."))
        return _summarize(plan)

    if "cloud" in q and "ndwi" in q:
        repeats = 4 if "between" in q or "compare" in q or "difference" in q else 2
        plan.extend(_repeat("apply_cloud_mask", repeats, "Cloud-mask each source band before index derivation."))
        plan.extend(_repeat("calculate_ndwi", 2 if repeats >= 4 else 1, "Derive NDWI after masking."))
        if "water loss" in q or "most severe" in q:
            plan.extend(_repeat("calc_batch_image_hotspot_percentage", 2, "Measure severe-loss percentages."))
            plan.append(PlannedSkillStep("max_value_and_index", "Find the most severe case."))
        elif "difference" in q or "compare" in q:
            plan.extend(_repeat("calc_batch_image_mean", 2, "Summarize each compared period."))
            plan.extend(_repeat("mean", 2, "Average each compared period."))
            plan.append(PlannedSkillStep("difference", "Compare the water-body summaries."))
        else:
            plan.append(PlannedSkillStep("calc_batch_image_mean", "Summarize the water index."))
        return _summarize(plan)

    if "ndti" in q or "turbidity" in q:
        if "threshold analysis" in q and "calculate ndti" not in q and "determine which" not in q:
            plan.extend(_repeat("calc_batch_image_mean", 4, "Use the provided threshold-analysis summaries for each period."))
            plan.extend(_repeat("mean", 2, "Average each compared period."))
            plan.append(PlannedSkillStep("difference", "Compare the threshold-based turbidity summaries."))
            return _summarize(plan)
        if "difference" in q or "compare" in q:
            plan.extend(_repeat("calculate_tif_average", 2, "Average each period before deriving NDTI."))
            plan.extend(_repeat("calculate_ndti", 2, "Derive NDTI for each period."))
            plan.extend(_repeat("calc_batch_image_mean", 2, "Summarize each NDTI period."))
            if "larger proportion" in q or "proportion" in q:
                plan.append(PlannedSkillStep("difference", "Compare the turbidity proportions."))
                if "multiply" in q:
                    plan.append(PlannedSkillStep("multiply", "Scale the difference if required by the benchmark."))
            else:
                plan.extend(_repeat("mean", 2, "Average each NDTI period summary."))
                plan.append(PlannedSkillStep("difference", "Compare the NDTI periods."))
                if "multiply" in q:
                    plan.append(PlannedSkillStep("multiply", "Scale the difference if required by the benchmark."))
        else:
            plan.append(PlannedSkillStep("calculate_water_turbidity_ntu", "Derive turbidity rasters."))
            plan.append(PlannedSkillStep("calc_batch_image_mean", "Summarize the turbidity series."))
        return _summarize(plan)

    if "nbr" in q or "fire risk" in q or "burn" in q:
        repeats = 6 if "distribution" in q or "dry season" in q else 2
        plan.extend(_repeat("calculate_nbr", repeats, "Derive NBR for each relevant scene."))
        if "direction" in q or "distribution" in q:
            plan.append(PlannedSkillStep("calc_batch_image_hotspot_tif", "Create binary hotspot maps from the NBR outputs."))
            plan.append(PlannedSkillStep("analyze_hotspot_direction", "Analyze hotspot distribution direction."))
        elif "trend" in q:
            plan.append(PlannedSkillStep("calc_batch_image_mean", "Summarize NBR over time."))
            plan.append(PlannedSkillStep("mann_kendall_test", "Estimate the NBR trend."))
        else:
            plan.append(PlannedSkillStep("calc_batch_image_mean", "Summarize NBR outputs."))
        return _summarize(plan)

    if "ndvi" in q:
        repeats = 3 if "peak" in q or "extremes" in q else 2
        plan.extend(_repeat("calculate_ndvi", repeats, "Derive NDVI for each relevant image or period."))
        plan.extend(_repeat("calc_batch_image_mean", repeats, "Summarize each NDVI output."))
        if "peak" in q or "max" in q:
            plan.append(PlannedSkillStep("calc_batch_image_mean", "Combine NDVI summaries for extremum search."))
            plan.append(PlannedSkillStep("max_value_and_index", "Find the peak vegetation coverage."))
        elif "trend" in q:
            plan.append(PlannedSkillStep("compute_linear_trend", "Estimate the NDVI trend."))
        return _summarize(plan)

    return _plan_products(item)


def _plan_products_arithmetic(item: BenchmarkItem) -> list[PlannedSkillStep]:
    q = item.question_text.lower()
    plan: list[PlannedSkillStep] = [PlannedSkillStep("get_filelist", "Discover available rasters first.")]

    if "commercial energy saving" in q:
        plan.extend(_repeat("calculate_tif_average", 2, "Average the nighttime-light rasters for each year."))
        plan.extend(_repeat("calc_batch_image_sum", 4, "Sum the relevant annual products before ratio comparison."))
        plan.extend(_repeat("division", 2, "Form the normalized energy indicators."))
        plan.append(PlannedSkillStep("percentage_change", "Measure the percentage change across years."))
        return _summarize(plan)

    if "residential volume" in q or "built_volume" in q:
        repeats = 8 if "1985 to 2020" in q else 2
        plan.extend(_repeat("subtract", repeats, "Subtract non-residential from total volume for each year."))
        plan.append(PlannedSkillStep("calc_batch_image_mean", "Summarize the residential-volume trajectory."))
        plan.append(PlannedSkillStep("compute_linear_trend", "Estimate the long-term trend."))
        return _summarize(plan)

    return _plan_products(item)


def _plan_rgb(item: BenchmarkItem) -> list[PlannedSkillStep]:
    q = item.question_text.lower()
    count = _file_count(item)
    plan: list[PlannedSkillStep] = [PlannedSkillStep("get_filelist", "Discover available images first.")]

    if "belongs to" in q or "category" in q or "industrial areas" in q:
        plan.extend(_repeat("MSCN", count, "Classify each image into a scene category."))
        return _summarize(plan)

    if "how many" in q or "number of" in q or "count" in q:
        if "destroy" in q or "restor" in q or "building" in q and ("before" in q or "after" in q or "pre" in q or "post" in q):
            plan.append(PlannedSkillStep("ChangeOS", "Detect building change between paired images."))
            plan.append(PlannedSkillStep("count_skeleton_contours", "Count the changed building components."))
        else:
            plan.extend(_repeat("InstructSAM", count, "Count the requested object in each image."))
        return _summarize(plan)

    if "centroid" in q or "westernmost" in q or "easternmost" in q:
        plan.append(PlannedSkillStep("RemoteSAM", "Ground the described region or object."))
        plan.append(PlannedSkillStep("bboxes2centroids", "Convert the box to centroid coordinates."))
        return _summarize(plan)

    if "distance" in q or "closest" in q or "farthest" in q:
        plan.append(PlannedSkillStep("SM3Det", "Detect the target objects."))
        plan.append(PlannedSkillStep("bboxes2centroids", "Convert detections to centroids."))
        plan.append(PlannedSkillStep("centroid_distance_extremes", "Measure nearest or farthest centroid distance."))
        if "gsd" in q or "m/px" in q:
            plan.append(PlannedSkillStep("multiply", "Convert pixels to physical distance with GSD."))
        return _summarize(plan)

    if "building area" in q or "square meters" in q or "pixels" in q:
        if "before" in q or "after" in q or "disaster" in q or "destroy" in q or "restor" in q:
            pairs = 2 if count >= 4 and ("which region" in q or "greater" in q) else 1
            plan.extend(_repeat("ChangeOS", pairs, "Extract or compare building regions."))
            plan.extend(_repeat("calculate_area", pairs, "Measure the building-region area."))
        else:
            plan.append(PlannedSkillStep("SM3Det", "Detect candidate objects first."))
            plan.append(PlannedSkillStep("SAM2", "Segment the detected region."))
            plan.append(PlannedSkillStep("calculate_area", "Measure the segmented region area."))
        if "difference" in q or "greater" in q:
            plan.append(PlannedSkillStep("difference", "Compare the measured areas."))
        return _summarize(plan)

    if "built-up area" in q or "built up area" in q:
        plan.extend(_repeat("SM3Det", count, "Detect built-up structures in each image."))
        plan.extend(_repeat("SAM2", count, "Segment the detected built-up regions."))
        plan.extend(_repeat("calculate_area", count, "Measure each built-up region area."))
        return _summarize(plan)

    if "harbor area" in q or "harbor areas" in q:
        compare_count = 2 if count >= 2 else 1
        plan.extend(_repeat("SM3Det", compare_count, "Detect harbor regions in each image."))
        plan.extend(_repeat("SAM2", compare_count, "Segment each harbor region."))
        plan.extend(_repeat("calculate_area", compare_count, "Measure harbor area for each image."))
        if "difference" in q or "compare" in q:
            plan.append(PlannedSkillStep("difference", "Compare the harbor areas."))
        return _summarize(plan)

    plan.extend(_repeat("MSCN", count, "Default to scene classification across the images."))
    return _summarize(plan)


def derive_skill_plan(item: BenchmarkItem, skill: SkillSpec) -> list[PlannedSkillStep]:
    if skill.skill_id == "earth-spectrum-drought-stress":
        return _plan_spectrum_drought(item)
    if skill.skill_id == "earth-spectrum-thermal-retrieval":
        return _plan_spectrum_thermal(item)
    if skill.skill_id == "earth-product-timeseries":
        return _plan_products(item)
    if skill.skill_id == "earth-product-derived-index-change":
        return _plan_products_derived_index(item)
    if skill.skill_id == "earth-product-raster-arithmetic":
        return _plan_products_arithmetic(item)
    if skill.skill_id == "earth-rgb-perception-change":
        return _plan_rgb(item)
    return [PlannedSkillStep("get_filelist", "Default fallback plan.")]
