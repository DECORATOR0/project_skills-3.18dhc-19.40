from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .config import SystemConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import render_prompt
from .schemas import CriticReward, EnvState, LLMMessage, SkillDetail
from .utils import write_json


class SkillCritic:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.llm = OpenAICompatibleLLM(config.critic)

    @staticmethod
    def _truncate_gold_args(value: Any) -> Any:
        if isinstance(value, dict):
            return {
                str(k): SkillCritic._truncate_gold_args(v)
                for k, v in list(value.items())[:3]
            }
        if isinstance(value, list):
            return [SkillCritic._truncate_gold_args(v) for v in value[:3]]
        return value

    @staticmethod
    def _summarize_gold_trajectory(gold_trajectory: list[dict]) -> list[dict]:
        steps = []
        for turn in gold_trajectory:
            if turn.get("role") != "assistant":
                continue
            for call in turn.get("tool_calls", []):
                fn = call.get("function", {})
                raw_args = fn.get("arguments", {})
                if isinstance(raw_args, str):
                    try:
                        raw_args = json.loads(raw_args)
                    except Exception:
                        raw_args = {"raw": raw_args}
                steps.append({
                    "tool_name": str(fn.get("name", "")),
                    "arguments": SkillCritic._truncate_gold_args(raw_args),
                })
        return steps

    def _serialize_batch_states(self, states: list[EnvState]) -> list[dict]:
        summaries = []
        for state in states:
            summaries.append({
                "task_id": state.task_id,
                "task_prompt": state.task_prompt,
                "choices": state.task_context.get("choices", []),
                "active_skill_name": state.active_skill_name,
                "final_answer": state.env_result.final_answer,
                "final_choice_label": state.env_result.final_choice_label,
                "executor_summary": state.env_result.executor_summary,
                "tool_trajectory": [
                    {
                        "step": record.step_index,
                        "tool": record.tool_name,
                        "success": record.success,
                        "error": record.error[:200] if record.error else "",
                    }
                    for record in state.env_result.tool_trajectory
                ],
                "phase_transitions": [
                    {
                        "step": t.step_index,
                        "from": t.from_phase,
                        "to": t.to_phase,
                    }
                    for t in state.env_result.phase_transitions
                ],
                "evaluation": state.env_result.evaluation.__dict__,
                "gold_tool_names": state.gold_tool_names,
                "gold_trajectory_summary": self._summarize_gold_trajectory(
                    state.gold_trajectory
                ),
                "gold_answer": state.task_context.get("gold_answer", ""),
            })
        return summaries

    def evaluate_batch(
        self,
        states: list[EnvState],
        skill: SkillDetail,
        history_poll: list[dict],
        log_dir: Path,
    ) -> CriticReward:
        system_prompt = render_prompt(
            self.config.prompt_root / "critic_system.md",
        )
        user_prompt = render_prompt(
            self.config.prompt_root / "critic_user.md",
            batch_states_json=json.dumps(
                self._serialize_batch_states(states),
                ensure_ascii=False,
                indent=2,
                default=str,
            ),
            skill_json=json.dumps(
                {
                    "header": skill.header.__dict__,
                    "body": skill.body,
                    "phase_order": sorted(skill.phases.keys(), key=lambda n: skill.phases[n].order),
                    "resources": skill.resources,
                },
                ensure_ascii=False,
                indent=2,
            ),
            history_poll_json=json.dumps(
                history_poll[-5:] if history_poll else [],
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
            reward_dimensions=payload.get("reward_dimensions", {}) if isinstance(payload.get("reward_dimensions", {}), dict) else {},
            experience_note=str(payload.get("experience_note", "")),
            summary=str(payload.get("summary", "")),
        )
        write_json(log_dir / "reward.json", reward.__dict__)
        return reward
