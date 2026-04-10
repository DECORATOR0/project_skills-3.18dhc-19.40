Current seed skill library snapshot (may be empty):
{current_skills_json}

Current training batch with gold tool-chain truth:
{batch_tasks_json}

Create a single phase-based Agent Skill that covers ALL tasks in this batch.

The skill MUST have phases defined with `## Phase: NAME` headers.
Minimum required phases: INIT, CONCLUDE.
Recommended phases: INIT, IDENTIFY, PROCESS_SPECTRUM, PROCESS_PRODUCTS, PROCESS_RGB, CONCLUDE.

Phase design guidance based on batch evidence:
- INIT phase: List task data directory, count files, note file extensions.
- IDENTIFY phase: Determine task modality from file patterns. Branch to the correct processing phase.
- Processing phases: One per modality. Each should specify the exact EO tool sequence from gold truth.
  For each gold tool chain step, write explicit instructions: which tool to call, what arguments to pass.
- CONCLUDE phase: Review tool outputs, match to answer choices, output <ANSWER>X</ANSWER>.

Every processing phase should include:
- The exact tool call pattern from gold truth as a template.
- Concrete examples of <CALL> and <ARGS> using actual tool names from the batch.
- Instructions on what to do with the tool output before moving to the next step.
- Conditions for transitioning to CONCLUDE.

For complex logic (comparisons, thresholds, numeric computation), create a `scripts/` helper.

Return exactly one JSON object:
{{
  "summary": "brief overview of the skill and its phase structure",
  "skill": {{
    "target_skill_name": "earth-bench-batch-skill",
    "summary": "what this skill covers and how it guides the executor",
    "source_task_ids": ["task ids used as evidence"],
    "files_to_write": {{
      "SKILL.md": "---\nname: ...\ndescription: ...\nallowed-tools:\n  - tool1\n  - tool2\n---\n\n## Phase: INIT\n...\n\n## Phase: IDENTIFY\n...\n\n## Phase: CONCLUDE\n...",
      "scripts/compare_values.py": "... optional helper ...",
      "references/TOOL_PATTERNS.md": "... optional detailed guidance ..."
    }}
  }}
}}

Hard requirements:
1. Return exactly ONE skill object.
2. SKILL.md must have YAML frontmatter with `name` and `description`.
3. SKILL.md body must define phases with `## Phase: NAME` headers.
4. At minimum: `## Phase: INIT` and `## Phase: CONCLUDE`.
5. `allowed-tools` must be concrete, grounded in the batch gold tool chains.
6. CONCLUDE phase must explicitly instruct: `<ANSWER>A/B/C/D</ANSWER>`.
7. Do not output any prose outside the JSON object.
