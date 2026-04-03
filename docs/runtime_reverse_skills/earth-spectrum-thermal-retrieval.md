# earth-spectrum-thermal-retrieval

下面这条是从 `19.40` 运行时代码反推出来的统一字段版本，不是外部参考技能库原文。

```yaml
skill_id: earth-spectrum-thermal-retrieval
display_name: Spectrum Thermal Retrieval
description: >
  Handles thermal-band spectrum tasks that retrieve or transform LST and then
  apply thresholding, period comparison, or summary statistics. Use for
  split-window, single-channel, multi-channel, TES, MODIS day/night, TTM, PWV,
  or emissivity questions.
domain: spectrum
benchmark_question_range_prior: [1, 100]

semantic_route_signals:
  question_keywords:
    - split-window
    - split window
    - single-channel
    - single channel
    - multi-channel
    - tes
    - emissivity
    - band 31
    - band 32
    - ttm
    - modis day
    - modis night
    - lst
  fallback_rule:
    - if no stronger semantic match appears and question_id <= 100, route here

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
  - split_window
  - lst_single_channel
  - lst_multi_channel
  - temperature_emissivity_separation
  - modis_day_night_lst
  - ttm_lst
  - band_ratio
  - count_images_exceeding_threshold_ratio
  - count_images_exceeding_mean_multiplier
  - count_pixels_satisfying_conditions
  - calculate_band_mean_by_condition
  - calculate_threshold_ratio
  - calc_batch_image_mean_threshold
  - calc_batch_image_max
  - calc_threshold_value_mean
  - average_ratio_exceeding_threshold
  - image_division_mean
  - calc_batch_image_mean_max_min

notable_runtime_extra_candidates:
  - calculate_tif_average
  - mann_kendall_test
  - calculate_multi_band_threshold_ratio
  - get_percentile_value_from_image
  - calculate_mean_lst_by_ndvi
  - calculate_max_lst_by_ndvi
  - count_spikes_from_values
  - calculate_intersection_percentage

focus_tools_rule:
  - focus_tools = shortlist intersect seed_allowlist
  - planner/direct-executor 会把 focus_tools 当作强 prior
  - 但真正硬边界仍然是 shortlist，不是 seed_allowlist

subfamilies_or_intents:
  - split_window -> split_window
  - single_channel_lst -> lst_single_channel
  - multi_channel_period_average -> lst_multi_channel
  - ttm -> ttm_lst
  - modis_lst -> modis_day_night_lst
  - band_ratio_or_pwv -> band_ratio
  - tes -> temperature_emissivity_separation
  - threshold_ratio -> calculate_threshold_ratio
  - count_days_or_threshold_count -> count_images_exceeding_threshold_ratio / calc_batch_image_mean_threshold
  - period_average -> calc_batch_image_mean_mean, sometimes calculate_tif_average
  - trend -> compute_linear_trend, sometimes mann_kendall_test
  - extremum_pick -> max_value_and_index / min_value_and_index
  - mean_multiplier_count -> count_images_exceeding_mean_multiplier
  - condition_pixel_count -> count_pixels_satisfying_conditions
  - condition_band_mean -> calculate_band_mean_by_condition
  - threshold_value_mean -> calc_threshold_value_mean
  - image_division_mean -> image_division_mean
  - max_stat -> calc_batch_image_max

special_file_priors:
  - modis_bt31_only -> prefer modis_day_night_lst

planner_guidance:
  - match the retrieval family to the wording instead of using one generic thermal chain
  - prefer specialized threshold/count tools over generic mean chains
  - if the question compares multiple periods, keep repeated transform/aggregation blocks explicit
  - for annual or yearly trend questions, keep per-year blocks before the final trend step
  - distinguish calculate_threshold_ratio from calculate_multi_band_threshold_ratio when multi-band wording appears

direct_executor_exposure:
  sees:
    - skill_id
    - display_name
    - description
    - focus_tools
    - planner_guidance rendered as skill guidance
  execution_rule:
    - may execute any exact tool name inside shortlist
    - may not execute a tool outside shortlist even if guidance mentions it

notes_or_limits:
  - original SkillSpec.notes is not directly injected into prompts
  - calculate_multi_band_threshold_ratio and mann_kendall_test are runtime-relevant but absent from seed_allowlist
  - this skill is already wider in runtime than a plain external SKILL.md can express

source_files:
  - agent/skill_eval/skill_router.py
  - agent/skill_eval/tool_router.py
  - agent/skill_eval/skill_llm_planner.py
  - agent/skill_eval/direct_executor.py
```
