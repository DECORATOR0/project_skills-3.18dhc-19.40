Cluster these retained source skills into exactly `{cluster_count}` non-empty groups.

Return exactly one JSON object:
{{
  "clusters": [
    {{
      "name": "public-facing cluster name",
      "description": "one concise routing description",
      "source_ids": ["source-id-1", "source-id-2"],
      "rationale": "why these sources belong together"
    }}
  ]
}}

Requirements:
1. Use every `source_id` exactly once.
2. Keep `name` and `description` executor-facing and reusable.
3. Keep 1-3 strong routing anchors when they distinguish the cluster, for example NDTI/turbidity, precipitation/rainfall, paired bands, before/after periods, or linear trend.
4. If the cluster is about discrete period comparison, say that explicitly instead of describing it as a generic trend skill.
5. Do not mention old family ids, benchmark question ids, or source skill names in `name` or `description`.
6. The clusters will later be aggregated into both a `flat` skill and a `tree` skill, so cluster around reusable execution structure rather than prompt trivia.
7. Do not emit raw hyphenated source slugs or source ids such as `foo-bar-baz` as the public cluster name; rewrite them as normal natural-language names.

Sources:
{sources_json}
