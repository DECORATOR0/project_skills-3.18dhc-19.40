# earth-product-derived-index-change

下面这条是从 `19.40` 运行时代码反推出来的统一字段版本，不是外部参考技能库原文。

```yaml
skill_id: earth-product-derived-index-change
display_name: Product Derived Index Change
description: >
  Handles product-domain questions that derive NDVI, NDWI, NDTI, NBR, turbidity,
  or cloud-masked water products before doing hotspot, direction, percentage,
  or period-comparison analysis.
domain: products
benchmark_question_range_prior: [101, 188]

semantic_route_signals:
  question_keywords:
    - ndvi
    - ndwi
    - ndti
    - nbr
    - cloud-masked
    - cloud masked
    - turbidity
    - water body
    - fire risk
    - hotspot

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
  - calculate_ndvi
  - calculate_ndwi
  - calculate_ndti
  - calculate_nbr
  - calculate_water_turbidity_ntu
  - apply_cloud_mask
  - calculate_tif_average
  - calc_batch_image_hotspot_percentage
  - calc_batch_image_hotspot_tif
  - analyze_hotspot_direction
  - multiply
  - sens_slope
  - mann_kendall_test

notable_runtime_extra_candidates:
  - division
  - subtract
  - count_above_threshold
  - calculate_tif_difference
  - coefficient_of_variation
  - calc_batch_image_sum

focus_tools_rule:
  - focus_tools = shortlist intersect seed_allowlist
  - focus_tools highlights the derive-first family inside the current shortlist

subfamilies_or_intents:
  - cloud_masked_water -> apply_cloud_mask -> calculate_ndwi
  - ndvi_change -> repeated calculate_ndvi blocks then compare
  - ndwi_change -> repeated calculate_ndwi blocks then compare
  - ndti_change -> repeated calculate_ndti blocks then compare
  - nbr_change -> repeated calculate_nbr blocks then compare
  - turbidity_retrieval -> calculate_water_turbidity_ntu
  - hotspot_extraction -> calc_batch_image_hotspot_percentage / calc_batch_image_hotspot_tif
  - hotspot_direction -> analyze_hotspot_direction
  - trend_significance -> sens_slope / mann_kendall_test

special_file_priors:
  - raw_sr_with_qa -> require or strongly prefer apply_cloud_mask
  - existing_product_series -> may still route here by question wording, but direct aggregation is often preferred if products already exist
  - existing_ndvi_product / existing_ndwi_product / existing_ndti_product / existing_nbr_product / existing_turbidity_product -> discourage re-derivation when filenames already expose the product

planner_guidance:
  - preserve repeated per-period index derivation blocks when the question compares dates or periods
  - if cloud masking is required, keep apply_cloud_mask before the downstream index tool
  - differentiate NDTI-style derivation from NTU turbidity retrieval
  - for compare questions, keep the explicit final comparison tail instead of stopping after product generation

direct_executor_exposure:
  sees:
    - skill_id
    - display_name
    - description
    - focus_tools
    - planner_guidance rendered as skill guidance
  execution_rule:
    - shortlist is still the real tool boundary

notes_or_limits:
  - division can enter runtime through product profile even though it is not in seed_allowlist
  - this skill is stronger than an external markdown skill because repeated-derivation and cloud-mask order are hard-coded in planner guidance
  - original SkillSpec.notes is not directly injected into prompts

source_files:
  - agent/skill_eval/skill_router.py
  - agent/skill_eval/tool_router.py
  - agent/skill_eval/skill_llm_planner.py
  - agent/skill_eval/direct_executor.py
```
