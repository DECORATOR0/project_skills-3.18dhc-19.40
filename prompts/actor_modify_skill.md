You must revise the single active skill in response to the batch-level critic reward.

=== ABSOLUTE RULE: COPY-PASTE FIRST, THEN ADD ONLY ===

Your SKILL.md output MUST be the current SKILL.md copied CHARACTER-FOR-CHARACTER, with ONLY new prohibition rules and positive path constraints INSERTED at specific locations. This is not optional.

Procedure:
1. Start by copying the ENTIRE current SKILL.md into your files_to_write["SKILL.md"] — every line exactly as-is.
2. Read the critic's reward to identify the top 3-5 failure modes.
3. For each failure mode, write ONE prohibition rule and/or ONE positive path constraint (each max 25 words, one line each).
4. INSERT these new lines within the appropriate existing phases. Do NOT change any surrounding content.
5. Copy ALL other files (references/, scripts/) exactly as-is. Only add new lines if the critic specifically requested it.
6. Verify: your total new/changed lines across ALL files MUST be ≤ 20.

=== DIFF BUDGET: MAX 20 NEW LINES ===

Count every line you add or modify compared to the current skill content:
- Each new prohibition rule line = 1
- Each new positive constraint line = 1
- Each fixed example line = 1 (max 1 fix allowed)
- Total MUST be ≤ 20

If the critic recommends more changes than fit in 20 lines, prioritize the top 3-5 failure modes by frequency.

=== Prohibition Rules (NEVER/NO/MUST NOT) ===

When the executor makes mistakes, add compact prohibition rules within the relevant skill phase. Format:
- "NEVER [bad action] when [condition]."
- "NO [bad behavior]; MUST [correct action]."
- "MUST NOT [error pattern]."

These are the most effective intervention for the 8B executor: compact, unambiguous, high attention.

=== Positive Path Constraints (MUST/REQUIRED) ===

Add positive constraints that LOCK IN correct behavior alongside prohibitions:
- "MUST: For [task type], [correct tool sequence]."
- "REQUIRED: [tool A] BEFORE [tool B] for [pattern]."

These prevent the executor from avoiding the prohibited action but also missing the correct path.

=== FORBIDDEN — Causes Regression Every Time ===

NEVER do ANY of these — they break previously working tasks:
- NEVER rewrite, restructure, compress, reorganize, or reformat any existing content
- NEVER move content between SKILL.md and references/ (or vice versa)
- NEVER shorten, rephrase, or simplify existing lines
- NEVER change the phase ordering, naming, or structure
- NEVER change existing tool-call examples that are working
- NEVER delete existing lines (except ONE proven-broken example fix)
- NEVER expand a phase by adding verbose explanations or multiple new examples
- NEVER add question-specific hardcoded examples (e.g., "for question91, do X")
- NEVER create more than 6 phases
- NEVER duplicate content across phases
- NEVER remove memory management or summarization directives
- NEVER reorganize or restructure content that drove successful tasks

=== Skill Validation Checklist ===

Before outputting, verify:
- [ ] YAML frontmatter has `name`, `description`, `allowed-tools` — SAME as current
- [ ] Phase structure is IDENTICAL to current (same phases, same order, same names)
- [ ] Every existing line appears UNCHANGED in your output
- [ ] New lines are ONLY prohibition rules and positive path constraints
- [ ] Total new/changed lines ≤ 20
- [ ] CONCLUDE phase instructs `<ANSWER>X</ANSWER>` format
- [ ] No content moved between files
- [ ] Tool call examples are UNCHANGED from current
- [ ] Scripts are UNCHANGED unless a new computation helper is needed
- [ ] References are UNCHANGED unless critic specifically requested a line addition

=== Current State ===

Batch-level critic reward:
{reward_json}

Current skill:
{skill_json}

Recent history poll:
{history_poll_json}

Similar past experiences:
{experiences_json}

=== Output Format ===

Return exactly one JSON object:
{{
  "summary": "list which exact rules were added to which phases (be specific)",
  "target_skill_name": "existing-skill-name",
  "files_to_write": {{
    "SKILL.md": "EXACT COPY of current SKILL.md with ≤ 15 new rule lines inserted",
    "references/RECIPES.md": "EXACT COPY of current file, add ≤ 3 lines only if critic specifically requested",
    "references/PATH_CONVENTIONS.md": "EXACT COPY of current file, unchanged unless critic specifically requested a fix",
    "scripts/helper.py": "UNCHANGED unless a new computation is needed"
  }},
  "files_to_delete": [],
  "experience_entry": {{
    "failure_signature": "compact failure signature for experience buffer",
    "affected_skills": ["skill-name"],
    "modification_summary": "concise description of what rules were added"
  }}
}}

Important:
- `files_to_write` MUST include the COMPLETE content of each file — not diffs.
- The content MUST be the current file with ONLY new rule lines inserted.
- Every existing line MUST be preserved EXACTLY (same wording, same formatting, same position).
- Total new/changed lines across all files MUST be ≤ 20.
- Your changes are STRICTLY: adding prohibition rules and positive path constraints. Nothing else.
- If accuracy dropped, your primary intervention is adding NEVER/NO/MUST NOT rules and MUST/REQUIRED rules. NOT restructuring.
- Do not output prose outside the JSON object.
