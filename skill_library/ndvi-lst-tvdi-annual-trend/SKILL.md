---
name: ndvi-lst-tvdi-annual-trend
description: Compute annual dryness trend from NDVI and LST raster time series by pairing filenames by date, generating TVDI rasters, averaging TVDI within each available year, extracting yearly means, and fitting a linear trend.
allowed-tools:
  - get_filelist
  - compute_tvdi
  - calculate_tif_average
  - calc_batch_image_mean
  - compute_linear_trend
---

Use this skill for annual dryness/wetness trend tasks from NDVI+LST GeoTIFFs stored in one directory.

Default mode: native tools only. Do not use helper scripts for pairing unless a task explicitly requires them. Do not use unrelated tools such as `read_file`.

Procedure:
1. Call `get_filelist(dir_path=DATA_DIR)` once.
2. From returned bare filenames, pair `*_LST.tif` and `*_NDVI.tif` by the same date token `YYYY-MM-DD` in the filename.
3. Build fully qualified aligned lists:
   - `lst_path=[DATA_DIR/<date>_LST.tif, ...]`
   - `ndvi_path=[DATA_DIR/<date>_NDVI.tif, ...]`
   - `output_path=[TASK_OUT/tvdi_YYYY-MM-DD.tif, ...]`
4. Call `compute_tvdi(lst_path=..., ndvi_path=..., output_path=...)` once over all matched dates.
5. Group the returned TVDI rasters by year; for each available year, call `calculate_tif_average(file_list=year_tvdis, output_path=annual_avg_path)` once.
6. Call `calc_batch_image_mean(file_list=annual_avg_paths)` once to get yearly means.
7. Call `compute_linear_trend(x=sorted_years, y=annual_means)`.
8. Report the annual trend: negative slope = decreasing dryness, positive slope = increasing dryness, near zero = little/no clear annual change.

Required execution rules:
- Use exact signatures only:
  - `get_filelist(dir_path=...)`
  - `compute_tvdi(lst_path=[...], ndvi_path=[...], output_path=[...])`
  - `calculate_tif_average(file_list=[...], output_path=...)`
  - `calc_batch_image_mean(file_list=[...])`
  - `compute_linear_trend(x=[years], y=[means])`
- Prefer one batch `compute_tvdi` call over many single-date calls.
- In a batch `compute_tvdi` call, `lst_path`, `ndvi_path`, and `output_path` must all be lists of the same length.
- `output_path` must be a JSON list like `["question1/tvdi_2019-01-01.tif", "question1/tvdi_2019-01-17.tif"]`, never one comma-joined string.
- After any helper/tool failure, immediately fall back to direct filename-based pairing and continue the pipeline.
- Do not retry with guessed parameter names. Do not probe unsupported tools.
- Exclude unmatched dates rather than inventing pairs.
- If a requested year is absent, complete the analysis on available years and state the coverage limitation explicitly.
- If some dates are missing within a year, use the matched dates that exist, note the omission briefly, and continue.
- If fewer than 2 yearly means are available, report that a linear trend is not reliable.

Path convention:
- Inputs from `DATA_DIR/...`
- Intermediate TVDI outputs under task output directory, e.g. `question1/tvdi_YYYY-MM-DD.tif`
- Annual averages under output directory, e.g. `benchmark/out/question1/tvdi_annual_avg_2021.tif`

Mini example:
- Files include `Xinjiang_2019-01-01_LST.tif` and `Xinjiang_2019-01-01_NDVI.tif`
- Pair on `2019-01-01`
- Output list example:
  - `lst_path=["benchmark/data/question1/Xinjiang_2019-01-01_LST.tif", "benchmark/data/question1/Xinjiang_2019-01-17_LST.tif"]`
  - `ndvi_path=["benchmark/data/question1/Xinjiang_2019-01-01_NDVI.tif", "benchmark/data/question1/Xinjiang_2019-01-17_NDVI.tif"]`
  - `output_path=["question1/tvdi_2019-01-01.tif", "question1/tvdi_2019-01-17.tif"]`
- After yearly averaging and mean extraction, fit trend on `x=[2019,2020,2021,2022]`
- Example slope `-0.037` => dryness decreases annually => choose the option describing decreasing dryness (often `B`).
