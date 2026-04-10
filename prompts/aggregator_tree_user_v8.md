Aggregate this cluster into one `tree` skill.

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
  "execution_guidance_md": "full references/EXECUTION_GUIDANCE.md markdown content",
  "summary": "optional short note"
}}

Routing-surface requirements:
1. `name` and `description` are router-critical. Keep 1-3 strong public anchors from the retained source prompts/skill descriptions when they are the main disambiguators, such as NDTI/turbidity, precipitation/rainfall, paired bands, before/after periods, or linear trend.
2. If this cluster is about discrete period comparison, say that explicitly in `description`; do not make it sound like a generic trend/regression skill.
3. If this cluster truly fits a time-series trend workflow, mention linear trend/slope/time series explicitly.
4. For paired-band or derived-index comparison clusters, surface the public task shape in `name` or `description` using phrasing router can match, such as before/after, same month across years, or year-vs-year comparison. Do not hide that shape behind abstract wording like `explicit windows` alone.
5. For generic raster trend clusters, keep the routing surface narrow to a single ordered raster series / linear trend task. Do not make it sound like it handles paired-band index change questions.
6. Never expose benchmark/internal identifiers anywhere in the returned object. Forbidden leakage includes question ids like `Q<ID>`, phrases like `question <id>`, source ids, `original_question_id`, source skill names, and question-specific helper filenames such as `*_<id>.py`.
7. Only reference helper scripts or references that appear in `cluster_json.shared_resource_candidates`. Do not mention source-only helper paths that are not in that list.

`skill_md` requirements:
1. Include YAML frontmatter with `name`, `description`, `allowed-tools`, `compatibility`, and `metadata`.
2. The body must use exactly these sections:
   - `## Execution Profile Index`
   - `## Global Execution Rules`
   - `## Global Guardrails`
   - `## Reference Usage`
3. In `## Execution Profile Index`, define 2-4 coarse modes. Each mode must get 1-2 sentences that explain:
   - when it applies
   - the typical terminal objective
   - how it differs from the other modes
4. Keep `SKILL.md` compact. Do not expand full preferred-tool lists or long recipes inside the mode index.
5. `## Reference Usage` should tell the executor to read `references/EXECUTION_GUIDANCE.md` only after selecting the dominant mode.
6. If the cluster compares multiple windows, years, groups, or before/after periods, the mode descriptions and global rules must make block completion explicit: finish every required block before comparison or final answer mapping.
7. If a helper script is the first critical step after discovery, include its exact `run_python_script` contract directly in `## Global Execution Rules` as a compact executable rule, not only in `execution_guidance_md`.
8. If the workflow includes a batch statistics step whose tool returns one value per artifact (for example `calc_batch_image_mean`), `## Global Execution Rules` must explicitly say that the returned list is not yet the block/window scalar and must be reduced with the next tool (for example `mean`) before any comparison or answer mapping. Do not leave this only in `execution_guidance_md`.
9. If the cluster needs a signed change/trend result or late multiple-choice option mapping and `cluster_json.shared_resource_candidates` contains a stable helper for that stage, include that helper's exact top-level contract in `## Global Execution Rules` as a compact executable rule, including the exact input keys the executor must preserve (for example `a`/`b` or `choices`/`change`/`trend`).
10. If such a signed helper is direction-sensitive, the top-level rules must explicitly bind each key to chronology/role (for example `a = later window mean`, `b = earlier window mean`) and warn not to swap them based on which window happened to be computed first.
11. For exactly two ordered windows, the guidance should prefer semantic labels like `earlier` and `later` (or another equally explicit chronology label) instead of relying only on raw year names.
12. If the retained source skill or helper contract already fixes fragile literal defaults or bindings that are easy to swap, preserve them verbatim in top-level rules. Examples include exact block keys like `year` / `month`, exact band-token bindings such as `red_band=\"b01\"` and `green_band=\"b04\"`, and deterministic `out_subdir` conventions. Do not rename these keys, invert them, or replace them with looser semantic placeholders.
13. If a task-local `out_subdir` convention is needed for derived outputs, prefer a deterministic rule based on the current `data_dir` basename and keep that exact value unchanged across helper planning and producing-tool execution. Do not invent `_output`-style suffixes unless the source contract explicitly requires them.
14. If the workflow requires planned derived outputs for multiple blocks/windows, `## Global Execution Rules` must define a strict phase order: discovery -> helper planning -> producing-tool call for every required block/window -> block-local statistics from tool-returned paths -> comparison -> answer mapping. Do not describe or imply a path where one block is summarized while another still only has helper-planned outputs.
15. If discovery on `data_dir` may include stale derived artifacts mixed with raw inputs, `## Global Guardrails` must say those files are ambient unless the task explicitly names them. Downstream steps for newly produced artifacts must use tool-returned paths from the current run.

`execution_guidance_md` requirements:
1. It must be a markdown file dedicated to the same mode labels used in `SKILL.md`.
2. For each mode, include exactly these fields:
   - `applies_when`
   - `preferred_tools`
   - `flow_hint`
   - `canonical_flows`
   - `edge_cases`
3. Keep it cluster-level and reusable. Do not include question ids or per-instance walkthroughs.
4. Preserve executor-safe defaults when relevant to the cluster:
   - derived artifacts should go to task-local output paths, not source data directories
   - later/earlier or multi-group comparisons must finish all blocks before comparison
   - `compute_linear_trend` should prefer implicit observed order and omit optional `x` unless discovered data guarantees `len(x) == len(y)`
   - one successful discovery/listing result should be reused to build later tool arguments instead of repeating the same discovery call
5. If `cluster_json.shared_resource_candidates` lists reusable scripts or references that materially reduce executor guesswork, you may reference those exact paths in `SKILL.md` or `execution_guidance_md`. Do not invent new resource paths beyond that list.
6. If you reference a shared helper script, include the exact call contract in `SKILL.md` or `execution_guidance_md`:
   - exact `script_path`
   - exact `stdin_json` keys expected by the script
   - the important returned fields the executor should use next
   - keep the real schema keys from the source contract; do not paraphrase them into new names
7. If a shared helper script accepts multiple windows/blocks/groups but the workflow can be completed block-by-block, write the guidance so the executor calls the helper once per required block/group/window instead of bundling them into one oversized structured result.
8. If such a helper script takes a discovery-derived `filenames` list plus selectors like `date_prefix`, the guidance must explicitly say that `filenames` stays equal to the full original discovery result on every call. The executor should narrow with selector fields, not by manually dropping other bands/years/files from `filenames`.
9. If such a helper script returns planned `output_path` / `output_paths` for derived artifacts, the guidance must explicitly say those are only seeds for the next producing-tool call. Any later `calc_*`, `mean`, difference, or comparison step must use the actual artifact paths returned by that tool, not the helper-planned seeds.
10. If the skill maps a computed result to task answer options, preserve the task payload key name `choices` unless the helper contract explicitly defines a different key. Do not silently rename task answer options to `options`, `answers`, or another invented field.
11. If the retained source skill or helper contract already fixes fragile literal defaults or bindings that are easy to swap, preserve them verbatim in `execution_guidance_md`. Examples include exact block keys like `year` / `month`, exact band-token bindings such as `red_band=\"b01\"` and `green_band=\"b04\"`, deterministic `out_subdir` conventions, and exact chronology-sensitive helper key roles.
12. `name` must be public-facing natural language. Do not copy a source skill slug or internal hyphenated label verbatim; if you keep the same anchors, rewrite them as a normal readable name.
13. If a mode ends with a direction-sensitive helper such as signed change/trend, its `flow_hint`, `canonical_flows`, or `edge_cases` must explicitly remind the executor that chronology controls the helper keys (`later` vs `earlier`), not execution order.
14. If a mode requires planned derived outputs for multiple blocks/windows, its `flow_hint` or `canonical_flows` must keep a strict phase order: discovery -> helper planning -> producing-tool call for every required block/window -> block-local statistics from tool-returned paths -> comparison -> answer mapping. Do not let one block be summarized while another still has only helper-planned outputs.
15. If discovery on `data_dir` may include stale derived artifacts mixed with raw inputs, mode guidance must treat those files as ambient unless the task explicitly names them as inputs. Downstream steps for newly produced artifacts must use tool-returned paths from the current run.
16. If source material mentions benchmark-specific helper names or internal labels, rewrite them into public reusable guidance or omit them. Do not echo those literals into `skill_md`, `execution_guidance_md`, or `summary`.
