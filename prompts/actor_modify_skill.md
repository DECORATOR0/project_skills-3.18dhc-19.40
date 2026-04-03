You must revise one existing skill in response to the critic reward.

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

Critic reward:
{reward_json}

Reference task:
{task_json}

Target skill:
{skill_json}

Similar experiences:
{experiences_json}

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
