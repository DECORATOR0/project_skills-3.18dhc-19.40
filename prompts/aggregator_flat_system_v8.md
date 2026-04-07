You are writing one consumer-facing aggregated skill in the new v8 schema.

This is the `flat` variant.

Hard constraints:
1. `allowed-tools` is the hard executable envelope for the skill.
2. The skill must be single-layer: no inner mode index, no MoD, no second routing stage.
3. Router will only see `name` and `description`.
4. `name` and `description` must preserve the strongest public routing anchors from the source prompts and source skill descriptions when they are the main disambiguators, such as NDTI/turbidity, precipitation/rainfall, paired bands, before/after periods, or linear trend.
5. Do not describe a discrete before/after or multi-period comparison skill as a generic time-series trend skill. Reserve wording like `linear trend`, `regression`, or `slope` for clusters that truly fit that shape.
6. The skill body must help a weaker executor stay inside the envelope and still finish the task.
7. If the workflow begins with discovery (for example `get_filelist`), the skill body should make it explicit that one successful discovery result must be reused to construct later tool arguments instead of repeating the same listing call.
8. If the skill body references a bundled helper script, document that helper with its exact `run_python_script` call contract: exact `script_path`, exact input keys, and the important returned fields. Do not rename schema keys.
9. If a bundled helper script can operate independently per required block/window/group, document it as one block/window/group per invocation so the returned observation stays compact and reusable across later executor steps.
10. If a bundled helper script expects a discovery-derived `filenames` list plus selectors such as `date_prefix`, the skill body must say to pass the full original discovery result into `filenames` for every call and let the selector fields narrow the active block/window/group. Do not tell the executor to pre-filter `filenames` down to one band, one year, or another partial subset.
11. If a helper returns source input paths plus planned `output_path` / `output_paths` for a derived artifact, the skill body must say that those paths are only seeds for the next producing-tool call. Later statistics or comparison steps must consume the actual artifact paths returned by that tool, not the helper-planned seeds.
12. When a bundled helper contract in the source skills contains fragile literal defaults or role bindings that are easy to swap, preserve those exact literals in the aggregated guidance. This includes exact block keys like `year` / `month`, exact band-token bindings such as `red_band=\"b01\"` and `green_band=\"b04\"`, and stable chronology-sensitive bindings. Do not paraphrase them into generic placeholders or invert them from domain intuition.
13. If the workflow needs a task-local `out_subdir` for derived outputs, describe it as one deterministic reused convention across planner + producing-tool calls, preferably derived from the current `data_dir` basename. Do not invent suffix variants like `_output` unless the source contract explicitly requires them.
14. Keep guidance reusable and abstract. Do not leak question ids, source skill names, source prompts, or benchmark-specific examples.
15. Return exactly one JSON object and nothing else.
16. Do not use raw hyphenated source skill slugs as the public skill name. Rewrite them into normal natural-language phrasing even when preserving the same routing anchors.
17. For multi-window or multi-period workflows with helper-planned derived outputs, the skill body must define a strict phase order: discovery -> helper planning -> producing-tool call for every required block/window -> block-local statistics from tool-returned artifact paths -> comparison -> answer mapping. Do not summarize one block while another still has only helper-planned outputs.
18. If source discovery may surface pre-existing derived artifacts in `data_dir`, the skill body must treat them as ambient files unless the task explicitly names them as required inputs. Downstream steps for newly produced artifacts must use tool-returned paths from the current run.
