# Reference

## Canonical call pattern
1. `get_filelist(dir_path="benchmark/data/<question_folder>")`
2. `compute_tvdi(ndvi_path="benchmark/data/<question_folder>/<veg>.tif", lst_path="benchmark/data/<question_folder>/<lst>.tif", output_path="<question_folder>/tvdi_<date>.tif")`
3. `calculate_threshold_ratio(image_paths="benchmark/out/<question_folder>/tvdi_<date>.tif", threshold=0.75)`

## Path propagation examples

### Example A: canonical return
- Requested output: `question4/tvdi_2022-08-13.tif`
- `compute_tvdi` returns: `benchmark/out/question4/tvdi_2022-08-13.tif`
- Use in next step: `benchmark/out/question4/tvdi_2022-08-13.tif`

### Example B: environment-specific returned path for same artifact
- Requested output: `question4/tvdi_2022-08-13.tif`
- `compute_tvdi` returns: `/tmp/runtime/benchmark/out/question4/tvdi_2022-08-13.tif`
- If clearly the same saved artifact, use canonical downstream path: `benchmark/out/question4/tvdi_2022-08-13.tif`
- Preserve the returned path in notes for retry/debugging.

### Example C: unclear equivalence
- Requested output: `question4/tvdi_2022-08-13.tif`
- `compute_tvdi` returns some other concrete saved path not clearly mappable to the canonical benchmark path
- Use the returned path unchanged.
- Do not invent another path.

## Success response pattern
- Report the computed percentage.
- If MCQ choices exist, map directly to the matching/closest option.

## Blocked response template
Use when `calculate_threshold_ratio` fails from GDAL/runtime/dependency issues:

- TVDI computation succeeded for `<date>`.
- The final threshold-exceedance calculation was blocked by an environment/runtime dependency, not by reasoning uncertainty.
- Failing tool: `calculate_threshold_ratio`
- Error: `<exact tool error>`
- Threshold-call path: `<path passed to calculate_threshold_ratio>`
- TVDI artifact for retry: `<normalized benchmark path if available>`
- Returned artifact path from `compute_tvdi`: `<returned path>`
- Therefore, no valid final percentage can be derived in the current runtime.
- If MCQ formatted: no valid answer choice can be selected without a successful threshold computation.

## MCQ rule
- Only output a choice label when the percentage was actually computed successfully.
- Never infer or guess from answer options when the threshold tool failed.
