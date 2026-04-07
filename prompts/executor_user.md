Task payload:
{task_json}

Activated skill name:
{skill_name}

Activated SKILL.md:
{skill_md}

Bundled resources visible to you:
{resources_json}

Execution reminder:
- resources listed above are files relative to the skill root
- execute `scripts/*.py` via `run_python_script`
- if a script reads JSON from stdin, pass the payload via `stdin_json`
- inspect `references/*.md` via `read_file`
- `file_list_preview` entries are only a preview of files inside `data_dir`; when `file_list_preview_is_partial` is true, you must not infer file/band/date absence from that preview alone
- when a missing-input judgment matters and `get_filelist` is available, call `get_filelist` on `data_dir` before concluding that a required file/band/date is absent
- when an EO tool needs an input file path, expand discovered file names to full paths under `data_dir` unless you already have a tool-returned absolute path
- if the skill body contains `Execution Profile Index`, pick one dominant mode first and keep later tool choices aligned to that mode
- for tree skills that instruct `read references/EXECUTION_GUIDANCE.md`, choose the mode, finish one initial discovery call if needed, and then make the very next non-discovery step `read_file("references/EXECUTION_GUIDANCE.md")` before any `run_python_script` or other helper-contract-sensitive call; do not learn helper schemas by trial and error
- `allowed-tools` in the skill frontmatter is the hard boundary; do not assume hidden tools or an external shortlist
- once a discovery tool has returned the needed filenames/paths, reuse that observation; do not repeat the same discovery call unless the prior result was incomplete or the underlying state changed
- if a helper script expects discovery-derived `filenames`, keep that argument equal to the full original discovery result unless the script contract explicitly says otherwise; narrow with selector fields, not by deleting filenames from the list
- if the skill shows exact helper literals or fragile defaults such as `year`, `month`, `red_band="b01"`, `green_band="b04"`, or a stable `out_subdir` rule, preserve them verbatim; do not rename keys, swap the band roles, or invent suffixes like `_output`
- if a helper returns planned `output_path` / `output_paths` for a derived raster, those are only seeds for the next EO derivation call; statistics tools must wait for and then use the actual artifact paths returned by the EO tool
- if a helper response already includes multiple required windows/blocks/groups, reuse those returned block results directly later instead of rerunning the same helper for another block
- for direction-sensitive helper contracts, preserve the exact semantic role of each input key; for example, if a skill says `a` is the later-window scalar and `b` is the earlier-window scalar, bind them by chronology rather than by computation order
- for two-window change tasks, prefer semantic labels like `earlier` and `later` when organizing block-local results so the final signed comparison step cannot accidentally swap them
- it is valid and expected to build concrete JSON tool arguments directly from previously observed filenames, dates, and numeric outputs when no dedicated parsing tool exists inside the allowed envelope

Execute the task now. Follow the skill. If additional resource files are needed, read or execute them explicitly with the correct tools.
