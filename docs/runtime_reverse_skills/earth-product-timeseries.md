# earth-product-timeseries

下面这条是从 `19.40` 运行时代码反推出来的统一字段版本，不是外部参考技能库原文。

```yaml
skill_id: earth-product-timeseries
display_name: Product Time-Series Analysis
description: >
  Handles non-RGB Earth observation product questions centered on generic
  multi-date raster statistics, trends, averages, minima, maxima, kurtosis,
  coefficient of variation, and period-to-period comparisons.
domain: products
benchmark_question_range_prior: [101, 188]

semantic_route_signals:
  question_keywords:
    - rainfall
    - nighttime light
    - ndsi
    - snow
    - ndwi
    - ndti
    - nbr
    - turbidity
    - hotspot
    - trend
  fallback_rule:
    - if question_id <= 188 and it does not hit the more specific product-derived-index-change or raster-arithmetic routes, route here

seed_allowlist:
  - get_filelist
  - calc_batch_image_mean
  - calc_batch_image_mean_mean
  - mean
  - difference
  - percentage_change
  - max_value_and_index
  - min_value_and_index
  - compute_linear_trend
  - calculate_batch_ndsi
  - calculate_ndwi
  - calculate_ndti
  - calculate_nbr
  - calculate_water_turbidity_ntu
  - apply_cloud_mask
  - calculate_tif_average
  - calculate_tif_difference
  - calc_batch_image_sum
  - calc_batch_image_hotspot_percentage
  - coefficient_of_variation
  - kurtosis
  - kelvin_to_celsius
  - division
  - multiply
  - mann_kendall_test
  - calc_batch_fire_pixels
  - identify_fire_prone_areas
  - index_to_date_range

notable_runtime_extra_candidates:
  - subtract
  - sens_slope
  - count_above_threshold
  - analyze_hotspot_direction
  - argmax
  - create_fire_increase_map
  - calc_batch_image_hotspot_tif
  - skewness
  - calc_batch_image_skewness

focus_tools_rule:
  - focus_tools = shortlist intersect seed_allowlist
  - this skill is broad and often acts as the generic product fallback family

subfamilies_or_intents:
  - existing_product_series -> aggregate existing rasters directly
  - raw_sr_with_qa -> apply_cloud_mask before downstream product computation
  - generic_mean_or_average -> calc_batch_image_mean + mean, or calculate_tif_average
  - extremum_pick -> max_value_and_index / min_value_and_index
  - celsius_conversion -> kelvin_to_celsius
  - percentage_or_difference_tail -> percentage_change / difference
  - hotspot_proportion -> calc_batch_image_hotspot_percentage
  - volatility -> coefficient_of_variation
  - trend -> compute_linear_trend / mann_kendall_test
  - fire_series -> calc_batch_fire_pixels / identify_fire_prone_areas

special_file_priors:
  - raw_sr_with_qa -> require or strongly prefer apply_cloud_mask
  - existing_ndvi_product -> prefer direct aggregation, discourage calculate_ndvi
  - existing_ndwi_product -> prefer direct aggregation, discourage calculate_ndwi
  - existing_ndti_product -> prefer direct aggregation, discourage calculate_ndti
  - existing_ndsi_product -> prefer direct aggregation, discourage calculate_batch_ndsi
  - existing_nbr_product -> prefer direct aggregation, discourage calculate_nbr
  - existing_turbidity_product -> prefer direct aggregation, discourage calculate_water_turbidity_ntu
  - existing_product_series -> prefer calc_batch_image_mean / mean / difference / percentage_change tails

planner_guidance:
  - use this skill for general product time-series reasoning
  - do not turn it into repeated per-period derived-index workflows when the question is really a derived-index family
  - if filenames already indicate an existing derived product series, aggregate those products directly instead of recomputing the index
  - for compare/two-period questions, keep one aggregate block per period before the final comparison tail

direct_executor_exposure:
  sees:
    - skill_id
    - display_name
    - description
    - focus_tools
    - planner_guidance rendered as skill guidance
  execution_rule:
    - may execute any shortlisted tool, including product-domain extras outside seed_allowlist

notes_or_limits:
  - this skill is partly a fallback bucket for product-domain questions
  - route semantics here are weaker than the other 5 skills because question_id fallback matters more
  - runtime product profile is richer than the static seed_allowlist

source_files:
  - agent/skill_eval/skill_router.py
  - agent/skill_eval/tool_router.py
  - agent/skill_eval/skill_llm_planner.py
  - agent/skill_eval/direct_executor.py
```
