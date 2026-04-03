# earth-product-raster-arithmetic

下面这条是从 `19.40` 运行时代码反推出来的统一字段版本，不是外部参考技能库原文。

```yaml
skill_id: earth-product-raster-arithmetic
display_name: Product Raster Arithmetic
description: >
  Handles product-domain questions whose benchmark trajectory relies on
  arithmetic over multiple raster products, such as built-volume subtraction,
  energy-saving ratios, multi-image sums, division chains, and percentage change.
domain: products
benchmark_question_range_prior: [101, 188]

semantic_route_signals:
  question_keywords:
    - built_volume
    - residential volume
    - non-residential
    - commercial energy saving
    - percentage of change

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
  - calculate_tif_average
  - calc_batch_image_sum
  - division
  - subtract

notable_runtime_extra_candidates:
  - multiply
  - mann_kendall_test
  - calculate_tif_difference
  - coefficient_of_variation
  - index_to_date_range

focus_tools_rule:
  - focus_tools = shortlist intersect seed_allowlist
  - arithmetic composition order is carried mostly by guidance, not by a rigid template

subfamilies_or_intents:
  - average_first_arithmetic -> calculate_tif_average or calc_batch_image_mean before arithmetic tails
  - sum_first_arithmetic -> calc_batch_image_sum before division / percentage_change
  - built_volume_difference -> subtract
  - energy_saving_ratio -> calc_batch_image_sum / division / percentage_change
  - temporal_arithmetic_trend -> repeated arithmetic blocks then compute_linear_trend

special_file_priors:
  - inherits the generic products-domain file priors
  - existing_product_series can feed arithmetic directly without re-deriving products

planner_guidance:
  - preserve arithmetic composition order
  - compute the required product summaries first, then apply subtract/division/percentage-change tails
  - do not replace an arithmetic workflow with a generic mean-only chain
  - distinguish average-first arithmetic from sum-first arithmetic

direct_executor_exposure:
  sees:
    - skill_id
    - display_name
    - description
    - focus_tools
    - planner_guidance rendered as skill guidance
  execution_rule:
    - may execute any shortlisted tool, not only the seed_allowlist

notes_or_limits:
  - this skill has the narrowest seed_allowlist among the product-domain skills
  - multiply is a likely runtime extra from the products profile even though it is not in seed_allowlist
  - the real arithmetic ordering logic currently lives in planner guidance, not in a standalone external skill field

source_files:
  - agent/skill_eval/skill_router.py
  - agent/skill_eval/tool_router.py
  - agent/skill_eval/skill_llm_planner.py
  - agent/skill_eval/direct_executor.py
```
