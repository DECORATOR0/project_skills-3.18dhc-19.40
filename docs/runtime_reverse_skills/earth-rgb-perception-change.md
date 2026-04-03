# earth-rgb-perception-change

下面这条是从 `19.40` 运行时代码反推出来的统一字段版本，不是外部参考技能库原文。

```yaml
skill_id: earth-rgb-perception-change
display_name: RGB Perception And Change
description: >
  Handles RGB Earth observation image tasks including scene classification,
  counting, visual grounding, geometry, segmentation-based area measurement,
  and before/after change analysis.
domain: rgb
benchmark_question_range_prior: [189, 248]

semantic_route_signals:
  file_or_question_clues:
    - image extension .png/.jpg/.jpeg
    - scene
    - airport
    - industrial
    - building area
    - built-up area
    - centroid
    - distance
    - closest
    - westernmost
    - destroyed
    - disaster
    - restor
    - harbor

seed_allowlist:
  - get_filelist
  - MSCN
  - InstructSAM
  - RemoteSAM
  - SM3Det
  - SAM2
  - ChangeOS
  - bboxes2centroids
  - centroid_distance_extremes
  - calculate_area
  - calculate_bbox_area
  - count_skeleton_contours
  - get_list_object_via_indexes
  - difference
  - division
  - multiply
  - ceil_number

notable_runtime_extra_candidates:
  - none
  - this is the only skill whose seed_allowlist and rgb domain base are already aligned

focus_tools_rule:
  - focus_tools = shortlist intersect seed_allowlist
  - for this skill, focus_tools is usually very close to the whole rgb shortlist

subfamilies_or_intents:
  - rgb_scene_classification -> MSCN
  - rgb_counting -> InstructSAM
  - rgb_grounding_centroid -> RemoteSAM + bboxes2centroids
  - rgb_pair_geometry -> SM3Det + bboxes2centroids + centroid_distance_extremes + get_list_object_via_indexes
  - rgb_building_change -> ChangeOS + calculate_area / count_skeleton_contours
  - rgb_building_area -> ChangeOS + calculate_area
  - rgb_builtup_rank -> SM3Det + SAM2 + calculate_area + multiply / ceil_number when GSD conversion is needed
  - rgb_detection_area -> SM3Det + calculate_bbox_area

special_file_priors:
  - paired_disaster_building -> prefer change-segmentation style building-change flow
  - gsd_builtup_ranking -> prefer repeated per-image rank workflow with SM3Det + SAM2 + calculate_area

planner_guidance:
  - first identify the rgb subfamily instead of mixing pipelines
  - prefer the canonical tool family for the detected subtask
  - for building change, prefer change-segmentation style workflows over detection-then-difference shortcuts
  - for centroid or described-region questions, prefer grounding -> centroid workflows
  - for plain object counting, prefer counting-oriented tools unless geometry is explicitly required
  - for ranking questions, keep repeated per-image blocks explicit

direct_executor_exposure:
  sees:
    - skill_id
    - display_name
    - description
    - focus_tools
    - planner_guidance rendered as skill guidance
  execution_rule:
    - shortlist remains the real boundary, but here shortlist and seed_allowlist are usually closely aligned

notes_or_limits:
  - this is the most self-contained of the six runtime skills
  - compared with the product/spectrum skills, fewer hidden profile extras leak in from domain base
  - original SkillSpec.notes is still not directly injected into prompts

source_files:
  - agent/skill_eval/skill_router.py
  - agent/skill_eval/tool_router.py
  - agent/skill_eval/skill_llm_planner.py
  - agent/skill_eval/direct_executor.py
```
