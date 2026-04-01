Exact tool pattern:

1. `get_filelist(dir_path="benchmark/data/question3")`
2. Build sorted parallel lists:
   - `lst_path=["benchmark/data/question3/..._LST.tif", ...]`
   - `ndvi_path=["benchmark/data/question3/..._NDVI.tif", ...]`
   - `output_path=["question3/tvdi_YYYY-MM-DD.tif", ...]`
3. `compute_tvdi(lst_path=[...], ndvi_path=[...], output_path=[...])`
4. `calc_batch_image_mean(file_list=tvdi_outputs)`
5. `count_spikes_from_values(values=means)`

Pairing rule:
- Match on the shared `YYYY-MM-DD` token.
- Keep only dates that have both LST and NDVI.
- Sort ascending before calling `compute_tvdi`.

Do / do not:
- Do build full input paths by prefixing the data directory.
- Do continue immediately to mean extraction and spike counting after TVDI succeeds.
- Do preserve `null` values in the means list.
- Do not call `read_file` for this raster workflow.
- Do not rerun `get_filelist` or `compute_tvdi` unchanged after the same failure.
- Do not invoke helper scripts; no supported helper-script path is part of this skill.

Recommended blocker message if `compute_tvdi` rejects otherwise valid list inputs:
- "Blocked by `compute_tvdi` tool interface/environment mismatch: same-date NDVI/LST files were listed successfully and converted to fully qualified paths, but `compute_tvdi` rejected the list input with an invalid-path/interface error. Per skill policy, stopping instead of retrying unsupported detours."