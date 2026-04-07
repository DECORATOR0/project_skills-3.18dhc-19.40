You must create one new skill because the current library is insufficient for the task.
This is a machine-only JSON contract, not a human conversation.

Design objectives:
1. Use the gold trajectory as the main source of procedural knowledge.
2. Generalize beyond the exact instance. The skill should cover the broader task family when that can be done safely.
3. Make the description highly triggerable: include what the skill does and when to use it.
4. Use progressive disclosure. Keep `SKILL.md` focused; move long checklists, examples, or edge-case references into `references/`.
5. If the task repeatedly requires arithmetic, parsing, batching, or path handling, include a reusable script in `scripts/`.
6. The skill must be usable by a weaker executor model. Make defaults explicit and reduce ambiguity.
7. When you reference a script in the skill body, explicitly instruct the executor to call `run_python_script` with `script_path="scripts/..."`. Do not write the script path as if it were a standalone tool name.
8. If the script reads JSON from stdin, explicitly tell the executor to pass that payload via `stdin_json`.
9. If the workflow repeats the same transform across many files and the relevant tool list contains a batch-equivalent tool, prefer that batch tool in both the body and `allowed-tools`.
10. For multi-period or multi-block derived-raster workflows, spell out the full phase order: discovery -> helper planning -> EO generation for every required block/window -> block-local statistics from EO-tool returned artifact paths -> comparison -> answer mapping.
11. Never leave wording that suggests helper-planned `output_path` / `output_paths` can be sent directly into statistics or comparison tools; make clear they are only seeds for the next EO-tool call.
12. If source discovery may surface stale derived artifacts beside raw inputs, guidance should say those files are ambient unless the task explicitly names them as required inputs, and downstream derived-product statistics must use current-run EO returned paths.

Current task:
{task_json}

Critic reward:
{reward_json}

Existing skills:
{existing_skills_json}

Relevant available tools:
{relevant_tools_json}

Return exactly one JSON object:
{{
  "summary": "what this new skill adds and why",
  "target_skill_name": "lowercase-hyphen-name",
  "files_to_write": {{
    "SKILL.md": "---\\nname: ...",
    "references/REFERENCE.md": "... optional ...",
    "scripts/helper.py": "... optional ..."
  }},
  "files_to_delete": [],
  "merged_from": [],
  "experience_entry": {{
    "failure_signature": "compact failure signature",
    "affected_skills": ["target_skill_name"],
    "modification_summary": "what the new skill teaches"
  }}
}}
The first character of your reply must be `{{` and the last character must be `}}`.
Do not output any preamble, explanation, or planning sentence before the JSON object.

Hard requirements for `SKILL.md`:
1. The frontmatter must include `name` and `description`.
2. The skill directory name and `name` must match.
3. Add `allowed-tools` when the workflow can be narrowed to a stable subset of tools.
4. The body must contain concrete execution guidance, not generic advice.
5. Include clear tool-use defaults.
6. If a script is introduced, the instructions must explicitly tell the executor when to call it.
7. If a script reads JSON from stdin, the instructions must say to pass the payload via `stdin_json`.
8. For compare-two-period or multi-block derived-raster skills, the body must explicitly require EO generation for every required block/window before any statistics, comparison, or answer mapping.
9. If a helper returns planned `output_path` / `output_paths`, the body must say that downstream statistics must use the actual artifact paths returned by the EO tool, not the helper-planned seeds.
10. Do not imply that stale derived rasters already present in `data_dir` are valid downstream inputs unless the task explicitly names them.
