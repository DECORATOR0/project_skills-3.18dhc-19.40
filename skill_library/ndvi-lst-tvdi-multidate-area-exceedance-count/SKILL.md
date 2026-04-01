---
name: ndvi-lst-tvdi-multidate-area-exceedance-count
description: Compute TVDI from dated NDVI and LST raster pairs across multiple dates, then count how many dates/images exceed an area-based threshold such as "more than 40% of pixels have TVDI > 0.7". Use for temporal exceedance-count questions, not yearly trend fitting, single-date percentage reporting, or spike counting on mean TVDI.
allowed-tools:
  - get_filelist
  - compute_tvdi
  - count_images_exceeding_threshold_ratio
---

Use this when the task asks for a **count of dates/images** where a drought condition covers more than a given share of the area.

Defaults:
- Call `get_filelist` **once** on the task data directory and treat that returned inventory as the source of truth.
- Pair rasters by identical `YYYY-MM-DD` date tokens in filenames from that inventory.
- Build full input paths by prefixing the task data directory to returned filenames.
- Unless the prompt gives other thresholds, use `value_threshold=0.7` and `ratio_threshold=40.0`.
- Derive TVDI output paths from the current task folder only (for example `question5/...` for data in `benchmark/data/question5`).

Required sequence:
1. Call `get_filelist` on the task data directory exactly once.
2. Split files into NDVI and LST sets, pair **all** same-date filenames from the returned list, and sort pairs by date.
3. If any dates are missing one modality, state the unmatched dates explicitly and continue on matched dates only.
4. Construct complete ordered `ndvi_path[]`, `lst_path[]`, and `output_path[]` lists over **every matched date**.
5. Prefer one **batch** `compute_tvdi` call over the full matched set.
6. Only if batch list input is unsupported, iterate exhaustively over all matched pairs while accumulating every returned TVDI output path until outputs exist for **every** matched date.
7. Pass the **full** returned TVDI output list into `count_images_exceeding_threshold_ratio` using prompt thresholds or defaults `value_threshold=0.7` and `ratio_threshold=40.0`.
8. Return the final numeric count; if answer choices are provided, map the count to the matching option and answer immediately.

Hard guardrails:
- Use the direct three-tool workflow: `get_filelist` -> `compute_tvdi` -> `count_images_exceeding_threshold_ratio`.
- Do **not** call `get_filelist` redundantly after already receiving the file inventory.
- Trust the retrieved file list; do not invent coverage ambiguity when matched pairs are present in the returned inventory.
- Intermediate TVDI rasters are **not** the answer.
- Do **not** stop after listing files, pairing dates, or generating only a subset of rasters.
- Do **not** call the count tool on a partial TVDI list.
- If singleton `compute_tvdi` calls are required, store every returned output path and still execute the final count step before responding.
- Do **not** call `run_python_script`, `read_file`, or any other non-allowed helper/file-inspection tool for this task family.
- If one tool-call format fails, recover once using the known schema, then continue.
- Never guess the final count or answer choice.

Deterministic completion checklist:
- `get_filelist` completed once.
- All same-date NDVI/LST pairs extracted from returned filenames.
- Unmatched dates, if any, reported and skipped.
- Full ordered NDVI/LST/output path lists prepared for all matched dates.
- `compute_tvdi` run for all matched pairs, preferably in one batch call.
- Number of returned TVDI outputs equals number of matched pairs.
- `count_images_exceeding_threshold_ratio` called once on the full TVDI list with prompt thresholds or defaults `0.7` and `40.0`.
- Final numeric count emitted.
- If MCQ options exist, final option emitted.

Completion rules:
- The task is **not complete** after listing files.
- The task is **not complete** after pairing dates.
- The task is **not complete** after computing TVDI, even for all dates.
- The task is complete only after `count_images_exceeding_threshold_ratio` has been called on the full TVDI output set and the final answer has been emitted.

Execution notes:
- Forward the exact TVDI output paths returned by `compute_tvdi` into the count step.
- Keep narration compact.
- After the count tool returns, stop tool use and answer immediately.

Example pattern:
- `get_filelist` returns 11 NDVI files and 11 LST files.
- Pair by matching dates from filenames and verify there are 11 matched dates.
- Build complete ordered lists of all 11 NDVI paths, 11 LST paths, and 11 TVDI output paths.
- Call one batch `compute_tvdi` over all 11 NDVI paths, 11 LST paths, and 11 output paths.
- Call `count_images_exceeding_threshold_ratio(image_paths=all_outputs, value_threshold=0.7, ratio_threshold=40.0)`.
- If the count is `4` and the options include `C = 4`, answer `C`.

Do not use this skill for:
- one-date area-percentage questions,
- annual averaging or trend estimation,
- counting spikes on a mean-TVDI time series.
