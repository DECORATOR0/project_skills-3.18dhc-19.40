---
name: ndvi-lst-tvdi-single-date-threshold-exceedance
description: Compute single-date TVDI from one vegetation raster (NDVI or EVI) and one LST raster, then calculate the percentage of area/pixels above a threshold (default 0.75). Use for one-date drought or water-stress questions, including MCQ selection from the computed percentage.
allowed-tools:
  - get_filelist
  - compute_tvdi
  - calculate_threshold_ratio
---

Use for **single-date** TVDI threshold-exceedance tasks, not time series or annual summaries.

## Workflow
1. Call `get_filelist(dir_path="benchmark/data/<question_folder>")`.
2. Identify exactly one matched vegetation raster (NDVI or EVI) and one LST raster for the operative date.
3. If prompt metadata conflicts with discovered filenames, trust the discovered filenames over the prompt text when there is only one clear benchmark pair.
4. Extract the operative `<YYYY-MM-DD>` from the discovered matched filenames and reuse that exact date consistently.
5. Build full input paths from `benchmark/data/<question_folder>/...`; never pass bare filenames.
6. Call `compute_tvdi(ndvi_path="<full veg path>", lst_path="<full lst path>", output_path="<question_folder>/tvdi_<discovered-date>.tif")`.
7. After `compute_tvdi`, preserve the tool-returned artifact path and determine the downstream path:
   - Canonical expected path: `benchmark/out/<question_folder>/tvdi_<discovered-date>.tif`
   - If the returned path is clearly the same requested artifact expressed differently (for example absolute temp/runtime path vs canonical benchmark output path, but same intended output filename/product), use the **canonical benchmark path** for `calculate_threshold_ratio` and still preserve the returned path in notes.
   - Otherwise, if canonical equivalence is not clear, use the returned path unchanged.
   - Do not invent any third path.
8. Call `calculate_threshold_ratio(image_paths="<downstream tvdi path>", threshold=<numeric prompt threshold or 0.75>)`.
9. If the ratio succeeds, return the percentage; if choices are provided, map the percentage directly to the matching/closest option and answer with the choice label.
10. If `calculate_threshold_ratio` fails with a GDAL/runtime/dependency error, stop immediately and return a blocked status instead of a percentage or MCQ label. State that the workflow logic was correct but the environment blocked completion.

## Rules
- Use exact parameter names only: `dir_path`, `ndvi_path`, `lst_path`, `output_path`, `image_paths`, `threshold`.
- Treat EVI like NDVI for this skill; if the tool schema uses `ndvi_path`, pass the single discovered vegetation raster there.
- When prompt wording conflicts with actual benchmark files, prefer the discovered files over the prompt text unless multiple plausible pairs exist.
- The discovered filename date is authoritative for `output_path`, downstream references, and final narration.
- Always request a benchmark-relative `output_path` in `compute_tvdi`: `<question_folder>/tvdi_<discovered-date>.tif`.
- Prefer benchmark-compatible downstream paths: when `compute_tvdi` returns an environment-specific absolute path for the requested output artifact, normalize it back to `benchmark/out/<question_folder>/tvdi_<discovered-date>.tif` before thresholding when equivalence is clear.
- Use the exact returned path only when canonical benchmark normalization is not clearly justified.
- Preserve both representations when they differ: the returned artifact path and the normalized benchmark path.
- Pass `threshold` as a numeric value, not a quoted string.
- Do **not** add extra arguments such as `mode` unless the tool schema explicitly requires them.
- If pairing is missing or ambiguous, stop and report the blocker.
- Do not add extra tools in the normal case.
- Do not invent a percentage, drought-hotspot claim, or MCQ label when threshold computation is blocked.
- If a multiple-choice answer is required but threshold computation is blocked, explicitly state that no valid choice can be selected in the current runtime.

## Failure handling
- If `compute_tvdi` fails from bad paths, rebuild full input paths from `benchmark/data/<question_folder>` and ensure `output_path` is benchmark-relative (`<question_folder>/tvdi_<discovered-date>.tif`), then retry once.
- If `compute_tvdi` returns no usable output path, fall back once to `benchmark/out/<question_folder>/tvdi_<discovered-date>.tif` only if that path is directly implied by the requested output artifact; otherwise stop and report an environment/path blocker.
- If `calculate_threshold_ratio` fails with an error containing `GDAL`, missing runtime, missing dependency, or equivalent environment wording, treat it as an **environment blocker**, not an analytical failure.
- After such a dependency error, do not claim completion, do not present partial success as task success, and do not guess the percentage or MCQ choice.
- Final blocked response must clearly say all of the following:
  - TVDI computation succeeded.
  - The threshold-exceedance step was blocked by the runtime/tool dependency.
  - The workflow logic and selected tools were appropriate.
  - No valid final percentage can be derived in the current runtime.
  - If MCQ formatted, no valid answer choice can be selected under the blocker.
- Final blocked response must include: failing tool name, exact error text, the path used for the failed threshold call, and the computed TVDI artifact location for retry (prefer the normalized benchmark path when available, but also surface the returned path when different).

See `references/REFERENCE.md` for canonical call patterns, path propagation examples, MCQ mapping, and blocked-execution response templates.
