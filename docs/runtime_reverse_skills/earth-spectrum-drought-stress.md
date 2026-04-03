# earth-spectrum-drought-stress

下面这条是从 `19.40` 运行时代码反推出来的统一字段版本，不是外部参考技能库原文。

```yaml
skill_id: earth-spectrum-drought-stress
display_name: Spectrum Drought Stress
description: >
  Handles drought or dryness indicator questions built from thermal and
  vegetation signals. Use for TVDI, ATI, drought severity bins, dryness trend,
  or stress-threshold analysis.
domain: spectrum
benchmark_question_range_prior: [1, 100]

semantic_route_signals:
  question_keywords:
    - tvdi
    - dryness
    - drought
    - thermal inertia
    - ati
  composite_rule:
    - if question contains ndvi + lst + one of severity/stress/dry, route here

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
  - compute_tvdi
  - ATI
  - calculate_tif_average
  - calculate_threshold_ratio
  - calc_threshold_value_mean
  - calc_batch_image_mean_threshold
  - calc_batch_image_mean_max_min
  - mann_kendall_test

notable_runtime_extra_candidates:
  - average_ratio_exceeding_threshold
  - calculate_band_mean_by_condition
  - image_division_mean
  - count_images_exceeding_threshold_ratio
  - count_images_exceeding_mean_multiplier
  - count_pixels_satisfying_conditions
  - calc_batch_image_max
  - calculate_multi_band_threshold_ratio

focus_tools_rule:
  - focus_tools = shortlist intersect seed_allowlist
  - drought skill 对模型的作用是 indicator-first prior，不是硬模板

subfamilies_or_intents:
  - tvdi -> compute_tvdi
  - ati -> ATI
  - period_average -> calc_batch_image_mean_mean / calculate_tif_average
  - trend -> compute_linear_trend / mann_kendall_test
  - threshold_ratio -> calculate_threshold_ratio
  - threshold_count -> calc_batch_image_mean_threshold
  - threshold_value_mean -> calc_threshold_value_mean
  - average_ratio_threshold_change -> average_ratio_exceeding_threshold
  - dryness_condition_mean -> calculate_band_mean_by_condition

special_file_priors:
  - no dedicated drought-only filename prior is hard-coded
  - still inherits general spectrum file priors when filenames imply available products

planner_guidance:
  - treat drought questions as indicator-first workflows: derive dryness product, then aggregate/threshold/compare
  - for annual or trend questions, preserve year-wise structure instead of collapsing into one summary
  - common benchmark pattern: repeated compute_tvdi -> repeated annual aggregation -> compute_linear_trend
  - if the question compares periods, keep repeated per-period blocks explicit before the final comparison

direct_executor_exposure:
  sees:
    - skill_id
    - display_name
    - description
    - focus_tools
    - planner_guidance rendered as skill guidance
  execution_rule:
    - shortlist is the real executable boundary

notes_or_limits:
  - original SkillSpec.notes is not directly injected into prompts
  - this skill shares the spectrum domain base, so neighboring spectrum threshold tools may still enter shortlist
  - runtime semantics are stronger than the seed_allowlist alone

source_files:
  - agent/skill_eval/skill_router.py
  - agent/skill_eval/tool_router.py
  - agent/skill_eval/skill_llm_planner.py
  - agent/skill_eval/direct_executor.py
```
