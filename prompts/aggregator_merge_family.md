You must aggregate one runtime family into a single consumer-facing skill.

Family contract:
{family_json}

Runtime reverse guidance:
{runtime_guidance_md}

Code-distilled guidance hints:
{distilled_guidance_json}

Source skills to merge:
{source_skills_json}

Requirements for the aggregated output:
1. `skill_md` must be a complete `SKILL.md` markdown file with YAML frontmatter and a concise body.
2. The consumer-facing `SKILL.md` must only contain family-level reusable guidance.
3. Do not include benchmark question IDs, source skill names, raw prompt text, source prompts, or audit metadata in `skill_md`.
4. Keep `When To Use`, execution defaults, trigger signals, and high-level guided execution flow clear enough for a weaker executor model.
5. `execution_guidance_md` should be richer than the main skill, but still abstract. It may include family-level tool-flow patterns such as `derive -> threshold statistic` or `segmentation -> area change`, but not per-question walkthroughs.
6. `execution_guidance_md` must also avoid benchmark question IDs, source skill names, and raw prompt examples.
7. Reconcile overlaps and contradictions across source skills instead of concatenating them.
8. Favor stable reusable defaults such as: inspect real files first, preserve discovered grouping, reuse exact returned paths, finish per-period blocks before final comparisons, and map to multiple-choice options only at the end when appropriate.

Return exactly one JSON object:
{{
  "summary": "what was merged and what reusable guidance survived",
  "description": "one concise family-level description for the aggregated skill",
  "skill_md": "---\\nname: ...",
  "execution_guidance_md": "# ... consumer-facing abstract guidance ..."
}}
