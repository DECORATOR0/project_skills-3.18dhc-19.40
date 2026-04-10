Aggregate this cluster into one `flat` skill.

Cluster contract:
{cluster_json}

Distilled hints:
{distilled_guidance_json}

Retained source skills:
{source_skills_json}

Return exactly one JSON object:
{{
  "name": "public-facing skill name",
  "description": "concise routing description",
  "skill_md": "full SKILL.md markdown content",
  "summary": "optional short note"
}}

Routing-surface requirements:
1. `name` and `description` are router-critical. Keep 1-3 strong public anchors from the retained source prompts/skill descriptions when they are the main disambiguators, such as NDTI/turbidity, precipitation/rainfall, paired bands, before/after periods, or linear trend.
2. If this cluster is about discrete period comparison, say that explicitly in `description`; do not make it sound like a generic trend/regression skill.
3. If this cluster truly fits a time-series trend workflow, mention linear trend/slope/time series explicitly.
4. Never expose benchmark/internal identifiers anywhere in the returned object. Forbidden leakage includes question ids like `Q<ID>`, phrases like `question <id>`, source ids, `original_question_id`, source skill names, and question-specific helper filenames such as `*_<id>.py`.
5. Only reference helper scripts or references that appear in `cluster_json.shared_resource_candidates`. Do not mention source-only helper paths that are not in that list.

`skill_md` requirements:
1. Include YAML frontmatter with `name`, `description`, `allowed-tools`, `compatibility`, and `metadata`.
2. The body must use exactly these sections:
   - `## High-Level Execution Guidance`
   - `## Common Defaults`
   - `## Global Guardrails`
3. `## High-Level Execution Guidance` should state the reusable execution skeleton for this cluster. If the cluster compares multiple windows, years, groups, or before/after periods, make the block structure explicit and require all blocks to finish before comparison or answer mapping.
4. `## Common Defaults` should define stable defaults such as discovery-first, one successful discovery call is enough and its returned filenames/paths should be reused directly for later tool arguments, exact returned-path reuse, task-local derived output paths instead of writing artifacts into source data directories, explicit block completion before final comparison, and late multiple-choice mapping when applicable.
5. When the allowed tool envelope includes trend tools such as `compute_linear_trend`, `## Common Defaults` should say to prefer the observed sequence order by default and omit optional `x` unless it is derived from discovered data and exactly matches `y` length.
6. `## Global Guardrails` should capture the major failure-prevention rules and boundary conditions, including no invented dates/files and no stopping after only part of a required comparison has been completed.
7. If `cluster_json.shared_resource_candidates` lists reusable scripts or references that materially reduce executor guesswork, you may reference those exact paths in `SKILL.md`. Do not invent new resource paths beyond that list.
8. Do not add `Execution Profile Index`, mode routing, or extra reference files.
9. If you reference a shared helper script, include the exact call contract in `SKILL.md`:
   - exact `script_path`
   - exact `stdin_json` keys expected by the script
   - the important returned fields the executor should use next
   - keep the real schema keys from the source contract; do not paraphrase them into new names
10. If the retained source skill or helper contract already fixes fragile literal defaults or role bindings that are easy to swap, preserve them verbatim in `SKILL.md`. Examples include exact block keys like `year` / `month`, exact band-token bindings such as `red_band=\"b01\"` and `green_band=\"b04\"`, exact chronology-sensitive helper key roles, and exact task answer key names like `choices`.
11. If a shared helper script accepts multiple windows/blocks/groups but the workflow can be completed block-by-block, write the guidance so the executor calls the helper once per required block/group/window instead of bundling them into one oversized structured result.
12. If such a helper script takes a discovery-derived `filenames` list plus selectors like `date_prefix`, the guidance must explicitly say that `filenames` stays equal to the full original `get_filelist` result on every call. The executor should narrow with selector fields, not by manually dropping other bands/years/files from `filenames`.
13. If such a helper script returns planned `output_path` / `output_paths` for derived artifacts, the guidance must explicitly say those are only seeds for the next producing-tool call. Any later `calc_*`, `mean`, difference, or comparison step must use the actual artifact paths returned by that tool, not the helper-planned seeds.
14. If a task-local `out_subdir` convention is needed for derived outputs, prefer a deterministic rule based on the current `data_dir` basename and keep that same value unchanged across helper planning and producing-tool execution. Do not invent `_output`-style suffixes unless the source contract explicitly requires them.
15. If the skill maps a computed result to task answer options, preserve the task payload key name `choices` unless the helper contract explicitly defines a different key. Do not silently rename task answer options to `options`, `answers`, or another invented field.
16. `name` must be public-facing natural language. Do not copy a source skill slug or internal hyphenated label verbatim; if you keep the same anchors, rewrite them as a normal readable name.
17. If the workflow requires planned derived outputs for multiple blocks/windows, `## High-Level Execution Guidance` must define a strict phase order: discovery -> helper planning -> producing-tool call for every required block/window -> block-local statistics from tool-returned paths -> comparison -> answer mapping. Do not describe or imply a path where one block is summarized while another still only has helper-planned outputs.
18. If discovery on `data_dir` may include stale derived artifacts mixed with raw inputs, `## Global Guardrails` must say those files are ambient unless the task explicitly names them. Downstream steps for newly produced artifacts must use tool-returned paths from the current run.
19. If source material mentions benchmark-specific helper names or internal labels, rewrite them into public reusable guidance or omit them. Do not echo those literals into `skill_md` or `summary`.
