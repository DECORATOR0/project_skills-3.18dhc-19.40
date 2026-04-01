Canonical workflow for annual TVDI trend tasks

1. List files
- Call `get_filelist(dir_path=DATA_DIR)`.
- Returned names may be bare filenames, so prepend `DATA_DIR/` when constructing tool inputs.

2. Pairing rule
- Match only filenames with the same date token `YYYY-MM-DD` and opposite suffixes `_LST.tif` and `_NDVI.tif`.
- Example:
  - `Xinjiang_2020-03-21_LST.tif`
  - `Xinjiang_2020-03-21_NDVI.tif`
- Ignore unmatched files.

3. Batch TVDI
- Build three same-length lists in the same date order:
  - `lst_path`
  - `ndvi_path`
  - `output_path`
- Then call one batch `compute_tvdi`.

4. Annual averaging
- Group returned TVDI outputs by year parsed from `tvdi_YYYY-MM-DD.tif`.
- One `calculate_tif_average` call per year.

5. Yearly means and trend
- Call `calc_batch_image_mean(file_list=annual_avg_paths)`.
- Call `compute_linear_trend(x=sorted_years, y=annual_means)`.

6. Interpretation
- Negative slope: drying indicator decreases over time, implying reduced dryness / wetter trend.
- Positive slope: drying indicator increases over time, implying stronger dryness.
- Near-zero slope: no clear annual trend.

Failure recovery rules
- If any helper script fails, do not stop and do not explore unrelated tools. Resume from direct filename pairing.
- If the prompt mentions years beyond file coverage, analyze only covered years and explicitly mention the missing requested years.
- If one or more dates are missing within a year, continue with available matched dates for that year.

Anti-drift reminders
- Do not call `read_file` for this task.
- Do not guess tool parameters such as `file_path`, `source`, `directory`, `lst_file`, or `ndvi_file`.
- Keep `x` and `y` aligned and years sorted ascending.
