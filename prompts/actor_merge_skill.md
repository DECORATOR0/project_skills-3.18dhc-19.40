You must merge overlapping skills into one broader skill.

Merge principles:
1. Preserve the strongest reusable guidance from each source skill.
2. Remove redundancy and contradictory instructions.
3. Write a new description that broadens trigger coverage without becoming vague.
4. Keep the merged `SKILL.md` concise; move specialized sections into `references/` when needed.
5. If multiple source skills rely on repeated code logic, consolidate that logic into scripts.
6. Add a narrow `allowed-tools` frontmatter field when the merged workflow has a stable tool subset.
7. If scripts are referenced, describe them as `run_python_script` calls rather than as standalone tool names.
8. If a merged helper script reads JSON from stdin, instruct the executor to pass the payload via `stdin_json`.

Critic reward:
{reward_json}

Candidate skills to merge:
{merge_skills_json}

Reference task:
{task_json}

Return exactly one JSON object:
{{
  "summary": "why these skills should be merged and what the merged skill covers",
  "target_skill_name": "new-merged-skill-name",
  "files_to_write": {{
    "SKILL.md": "---\\nname: ...",
    "references/REFERENCE.md": "... optional ...",
    "scripts/helper.py": "... optional ..."
  }},
  "files_to_delete": ["old-skill-a", "old-skill-b"],
  "merged_from": ["old-skill-a", "old-skill-b"],
  "experience_entry": {{
    "failure_signature": "overlapping skills caused trigger ambiguity",
    "affected_skills": ["old-skill-a", "old-skill-b", "new-merged-skill-name"],
    "modification_summary": "merged overlapping skills into a broader one"
  }}
}}
