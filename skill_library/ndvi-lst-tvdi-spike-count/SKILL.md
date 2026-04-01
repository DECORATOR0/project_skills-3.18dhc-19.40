---
name: ndvi-lst-tvdi-spike-count
description: Compute a drought-index spike count from dated NDVI and LST rasters by pairing same-date files, generating TVDI rasters for each timestamp, averaging each TVDI raster to a time series, and counting spikes in that series.
allowed-tools:
  - get_filelist
  - compute_tvdi
  - calc_batch_image_mean
  - count_spikes_from_values
---

Use when a task asks for drought-index spike/peak/event counts from multi-date NDVI and LST rasters.

Canonical sequence:
1. `get_filelist(dir_path=data_dir)`
2. Pair `_LST.tif` and `_NDVI.tif` by shared `YYYY-MM-DD`; keep only same-date pairs.
3. Sort dates ascending and build full input paths by prefixing `data_dir`.
4. Build one output path per date, usually `questionX/tvdi_YYYY-MM-DD.tif`.
5. Call `compute_tvdi` exactly once with parallel `lst_path`, `ndvi_path`, and `output_path` lists.
6. If TVDI outputs are returned, call `calc_batch_image_mean(file_list=tvdi_outputs)`.
7. Call `count_spikes_from_values(values=means)`.
8. If multiple choice, map the numeric count to the option only after step 7.

Defaults:
- Preserve all valid same-date pairs.
- Preserve `null` means; do not impute or drop unless the prompt instructs otherwise.
- Use fully qualified input paths, not bare filenames.

Strict failure policy:
- Do not call `read_file` to check raster existence in this workflow.
- Do not repeat an identical failing `compute_tvdi` call.
- If `compute_tvdi` fails with list-path parsing, `invalid path or file`, or similar interface ambiguity despite fully qualified same-date path lists built from `get_filelist`, treat it as a tool/environment blocker and stop with a precise message.
- Do not use helper scripts or `run_python_script` unless this skill explicitly documents an exact supported invocation; it does not.

Example path construction:
- `data_dir = benchmark/data/question3`
- file from `get_filelist`: `Yellow River basin_2023-06-10_LST.tif`
- full path: `benchmark/data/question3/Yellow River basin_2023-06-10_LST.tif`
- output path: `question3/tvdi_2023-06-10.tif`

See `references/REFERENCE.md` for the exact call pattern and blocker wording.
