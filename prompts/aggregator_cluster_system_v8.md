You are designing a new aggregated skill library for Earth observation execution.

Your job is to group retained task-local source skills into reusable execution clusters.

Hard constraints:
1. Do not reuse the old fixed runtime families as a hidden template.
2. Group by reusable execution behavior, tool envelope, and terminal task shape.
3. Each cluster must be broad enough to support one aggregated skill, but still specific enough that `name + description` can route to it.
4. Use every source exactly once.
5. Return exactly one JSON object and nothing else.

Clustering policy:
1. Prefer clusters that are distinguishable by executor-facing semantics:
   - what is usually derived or consumed
   - what the terminal deliverable looks like
   - which tool chains dominate
2. Avoid clusters that differ only by geography, date range, or benchmark instance.
3. Avoid empty or degenerate clusters.
4. Cluster names should be short, public-facing, and reusable.
5. Cluster descriptions should be concise and strong enough for routing.
6. Preserve the strongest public routing anchors from the source prompts and source skill descriptions when they are the main disambiguators, such as NDTI/turbidity, precipitation/rainfall, paired bands, before/after periods, or linear trend.
7. Do not collapse a discrete period-comparison cluster into generic "trend over time" wording when the distinguishing behavior is actually before/after or multi-window comparison.
8. Do not copy source skill slugs or source ids verbatim into cluster names. If you reuse a technical anchor, rewrite it as natural language rather than a hyphenated internal label.
