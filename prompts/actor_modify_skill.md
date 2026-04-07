You must revise one existing skill in response to the critic reward.
This is a machine-only JSON contract, not a human conversation.

Revision objectives:
1. Fix the specific failure mode with the smallest reliable change.
2. Incorporate prior similar failures from the NL-Experience Buffer when useful.
3. Prefer explicit defaults, few-shot examples, path conventions, and scripts over vague prose.
4. If the skill is too long or the reward mentions context overload, move details into `references/`.
5. If the task failed because the executor guessed numbers, paths, or logic, add a script or a stricter tool-use rule.
6. Preserve the skill's useful prior coverage.
7. Tighten `allowed-tools` when reducing executor drift would help.
8. If the skill uses bundled scripts, explicitly instruct the executor to invoke them through `run_python_script` rather than treating file paths as tool names.
9. If a bundled script reads JSON from stdin, explicitly say to pass the payload via `stdin_json`.
10. If the failure involves repeated same-tool calls or executor step pressure, prefer an available batch-equivalent tool from the relevant tool list and update `allowed-tools` accordingly.
11. For multi-period or multi-block derived-raster workflows, rewrite the skill so it spells out the full phase order: discovery -> helper planning -> EO generation for every required block/window -> block-local statistics from EO-tool returned artifact paths -> comparison -> answer mapping.
12. Never leave ambiguous wording that suggests helper-planned `output_path` / `output_paths` can be sent directly into statistics or comparison tools.
13. If source discovery may surface stale derived artifacts beside raw inputs, state that those files are ambient unless the task explicitly names them as required inputs; downstream derived-product statistics must use current-run EO returned paths.

Critic reward:
{reward_json}

Reference task:
{task_json}

Target skill:
{skill_json}

Similar experiences:
{experiences_json}

Relevant available tools:
{relevant_tools_json}

Return exactly one JSON object:
{{
  "summary": "what was revised and why",
  "target_skill_name": "existing-skill-name",
  "files_to_write": {{
    "SKILL.md": "---\\nname: ...",
    "references/REFERENCE.md": "... optional ...",
    "scripts/helper.py": "... optional ..."
  }},
  "files_to_delete": [],
  "merged_from": [],
  "experience_entry": {{
    "failure_signature": "compact failure signature",
    "affected_skills": ["existing-skill-name"],
    "modification_summary": "what changed in the skill"
  }}
}}
The first character of your reply must be `{{` and the last character must be `}}`.
Do not output any preamble, explanation, or planning sentence before the JSON object.
