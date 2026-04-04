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
- `file_list_preview` entries are file names inside `data_dir`; when an EO tool needs an input file path, expand them to full paths under `data_dir` unless you already have a tool-returned absolute path

Execute the task now. Follow the skill. If additional resource files are needed, read or execute them explicitly with the correct tools.
