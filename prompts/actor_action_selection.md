State snapshot:
{state_json}

Critic reward:
{reward_json}

Decide the first-layer actor action.

Return exactly:
{{
  "action_type": "create_skill | merge_skills | modify_skill",
  "reason": "why this first-layer action is best"
}}
