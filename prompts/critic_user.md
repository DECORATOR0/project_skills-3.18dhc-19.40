Batch execution traces (including phase transitions):
{batch_states_json}

Current active skill:
{skill_json}

Recent history poll (problems from previous iterations):
{history_poll_json}

Produce the batch-level critic reward now. Evaluate all six dimensions:
task_alignment, phase_structure, progressive_disclosure, efficiency_and_hallucination, script_and_reference_usage, context_efficiency.

=== REGRESSION ANALYSIS (DO THIS FIRST) ===
1. List every SUCCEEDED task ID in this iteration.
2. If history_poll has previous iteration data, compare succeeded lists. Compute:
   - REGRESSED tasks = succeeded before but failed now
   - NEW SUCCESSES = failed before but succeeded now
   - Net change = new successes - regressions
3. If regressions exist, your TOP PRIORITY is diagnosing them and telling the actor to PRESERVE the current skill content.

=== MANDATORY GUIDANCE FOR THE ACTOR ===
- The actor MUST preserve the current skill's structure, content, formatting, and file organization EXACTLY.
- The actor MUST NOT restructure, rewrite, compress, reorganize, or move content between files.
- The actor may ONLY add new lines (prohibition rules and positive path constraints) within existing phases/files.
- Total additions MUST be ≤ 20 new lines across all files.
- Provide EXACT prohibition rule text (NEVER/NO/MUST NOT) and EXACT positive path constraint text (MUST/REQUIRED) for the actor to insert.
- Specify which phase each rule should be added to.
- ALWAYS include at least 2-3 positive path constraints (MUST: For X, do Y before Z) alongside prohibitions to lock in correct behavior.
- NEVER recommend restructuring, shortening phases, moving content to references, or reorganizing the skill.
