from __future__ import annotations

import json
from pathlib import Path

from .config import SystemConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import render_prompt
from .schemas import CriticReward, EnvState, LLMMessage
from .tool_hints import render_relevant_tools_json
from .utils import write_json


class SkillCritic:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.llm = OpenAICompatibleLLM(config.critic)

    def _fallback_reward_payload(self, state: EnvState) -> dict:
        evaluation = state.env_result.evaluation
        if state.active_skill is None:
            tool_names = ", ".join(state.gold_tool_names) if state.gold_tool_names else "the gold tool trajectory"
            return {
                "natural_language_reward": (
                    "No applicable skill was available, so execution stopped before any tool calls. "
                    f"Create a new skill that reliably triggers for this task family and teaches the executor to follow the gold tool path: {tool_names}."
                ),
                "recommended_action_type": "create_skill",
                "create_new_skill": True,
                "merge_candidates": [],
                "target_skill": "",
                "reward_dimensions": {
                    "task_alignment": "Task failed because the library had no applicable skill.",
                    "trigger_precision": "Router had nothing to select; coverage is missing.",
                    "efficiency_and_hallucination": "Execution was skipped, so the missing capability is the main issue.",
                    "progressive_disclosure": "New skill should expose a compact end-to-end workflow with explicit defaults.",
                    "skill_count_pressure": "No merge pressure; prioritize adding coverage.",
                },
                "experience_note": "no applicable skill; create coverage for this task family",
                "summary": "Create a new skill because the current library had no applicable coverage.",
            }

        active_skill_name = state.active_skill.header.name
        failure_parts: list[str] = []
        if float(evaluation.tool_exact_match or 0.0) >= 0.99:
            failure_parts.append("the executor followed the right tool chain")
        elif float(evaluation.tool_in_order or 0.0) >= 0.99:
            failure_parts.append("the executor mostly followed the right ordered tool chain")
        else:
            failure_parts.append("the current skill still misguides tool selection or ordering")
        if float(evaluation.parameter_accuracy or 0.0) < 1.0:
            failure_parts.append("parameters, thresholds, units, or output formatting still diverged from the gold trajectory")
        if not evaluation.task_success:
            failure_parts.append("the final answer was still incorrect")
        diagnosis = "; ".join(failure_parts)
        return {
            "natural_language_reward": (
                f"Modify `{active_skill_name}`. {diagnosis}. "
                "Preserve any tool steps that already match the gold path, and tighten the skill so the executor uses the correct defaults, exact parameter values, and final-answer formatting."
            ),
            "recommended_action_type": "modify_skill",
            "create_new_skill": False,
            "merge_candidates": [],
            "target_skill": active_skill_name,
            "reward_dimensions": {
                "task_alignment": "Task is still incorrect even after skill-guided execution.",
                "trigger_precision": "The active skill is relevant, so refinement is better than creating a new skill.",
                "efficiency_and_hallucination": "Preserve correct steps and remove drift in parameters or answer synthesis.",
                "progressive_disclosure": "Add clearer defaults/examples only where they directly fix the observed failure.",
                "skill_count_pressure": "No merge pressure; keep the fix scoped to the active skill.",
            },
            "experience_note": f"modify {active_skill_name}: correct skill family but wrong parameters/output",
            "summary": "Modify the active skill to keep the right tool path but fix parameter/default/output mismatches.",
        }

    def _normalize_reward_payload(self, payload: dict, state: EnvState) -> dict:
        if not isinstance(payload, dict):
            return self._fallback_reward_payload(state)
        has_structured_signal = any(
            bool(payload.get(key))
            for key in ("natural_language_reward", "summary", "target_skill", "reward_dimensions")
        )
        if has_structured_signal:
            return payload
        return self._fallback_reward_payload(state)

    def evaluate(self, state: EnvState, log_dir: Path) -> CriticReward:
        system_prompt = render_prompt(
            self.config.prompt_root / "critic_system.md",
            skill_count_limit=self.config.runtime.skill_count_limit,
            trigger_threshold=self.config.runtime.skill_match_threshold,
        )
        relevant_tools_json = render_relevant_tools_json(
            self.config,
            task_prompt=state.task_prompt,
            gold_tool_names=state.gold_tool_names,
            skill_headers=state.skill_headers,
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
            relevant_tools_json=relevant_tools_json,
        )
        payload, llm_result = self.llm.chat_json(
            [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]
        )
        log_llm_call(log_dir, "critic", llm_result)
        payload = self._normalize_reward_payload(payload, state)
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
