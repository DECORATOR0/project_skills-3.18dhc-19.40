from __future__ import annotations

import json
from pathlib import Path

from .config import SystemConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import render_prompt
from .schemas import CriticReward, EnvState, LLMMessage
from .utils import write_json


class SkillCritic:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.llm = OpenAICompatibleLLM(config.critic)

    def evaluate(self, state: EnvState, log_dir: Path) -> CriticReward:
        system_prompt = render_prompt(
            self.config.prompt_root / "critic_system.md",
            skill_count_limit=self.config.runtime.skill_count_limit,
            trigger_threshold=self.config.runtime.skill_match_threshold,
        )
        user_prompt = render_prompt(
            self.config.prompt_root / "critic_user.md",
            state_json=json.dumps(
                {
                    "task_id": state.task_id,
                    "task_prompt": state.task_prompt,
                    "router_result": {
                        "selected_skill": state.router_result.selected_skill,
                        "has_applicable_skill": state.router_result.has_applicable_skill,
                        "scores": [score.__dict__ for score in state.router_result.scores],
                        "trajectory": state.router_result.trajectory,
                        "notes": state.router_result.notes,
                    },
                    "env_result": {
                        "final_answer": state.env_result.final_answer,
                        "final_choice_label": state.env_result.final_choice_label,
                        "executor_summary": state.env_result.executor_summary,
                        "tool_trajectory": [record.__dict__ for record in state.env_result.tool_trajectory],
                        "evaluation": state.env_result.evaluation.__dict__,
                    },
                    "gold_tool_names": state.gold_tool_names,
                    "gold_trajectory": state.gold_trajectory,
                    "skill_headers": [header.__dict__ for header in state.skill_headers],
                    "active_skill": None if state.active_skill is None else state.active_skill.header.__dict__,
                    "task_context": state.task_context,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
        )
        payload, llm_result = self.llm.chat_json(
            [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]
        )
        log_llm_call(log_dir, "critic", llm_result)
        reward = CriticReward(
            natural_language_reward=str(payload.get("natural_language_reward", "")),
            recommended_action_type=str(payload.get("recommended_action_type", "modify_skill")),
            create_new_skill=bool(payload.get("create_new_skill", False)),
            merge_candidates=[str(item) for item in payload.get("merge_candidates", [])],
            target_skill=str(payload.get("target_skill", "")),
            reward_dimensions=payload.get("reward_dimensions", {}) if isinstance(payload.get("reward_dimensions", {}), dict) else {},
            experience_note=str(payload.get("experience_note", "")),
            summary=str(payload.get("summary", "")),
        )
        write_json(log_dir / "reward.json", reward.__dict__)
        return reward
