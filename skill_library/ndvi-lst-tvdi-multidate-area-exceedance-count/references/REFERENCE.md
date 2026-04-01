Multidate TVDI exceedance-count quick reference

Use native tools only:
1. `get_filelist(dir_path=...)`
2. Pair all same-date `*_NDVI.tif` and `*_LST.tif` files from the returned list.
3. `compute_tvdi(ndvi_path=[...], lst_path=[...], output_path=[...])` in batch when supported.
4. `count_images_exceeding_threshold_ratio(image_paths=[returned tvdi paths], value_threshold=0.7, ratio_threshold=40.0)` unless the prompt specifies other thresholds.
5. Emit the numeric count, then the MCQ option if applicable.

Path convention:
- Inputs: prefix filenames from `get_filelist` with the task data directory.
- Outputs: use task-local relative outputs such as `question5/tvdi_2021-05-09.tif`; pass the exact returned output paths into the final count tool.

Failure prevention:
- Do not stop after partial singleton TVDI calls.
- Do not omit the final count tool.
- If some dates are unmatched, report them and continue with matched pairs.
- Trust the observed file inventory instead of speculative mismatch notes.
