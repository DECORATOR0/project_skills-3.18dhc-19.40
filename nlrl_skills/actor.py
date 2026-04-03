from __future__ import annotations

import json
from pathlib import Path

from .config import SystemConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import render_prompt
from .schemas import ActorDecision, CriticReward, EnvState, ExperienceEntry, LLMMessage, SkillHeader
from .skills import (
    append_experience,
    delete_skill_dirs,
    load_experience_buffer,
    load_skill_detail,
    retrieve_similar_experiences,
    write_skill_bundle,
)
from .utils import utc_timestamp, write_json


class SkillActor:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.llm = OpenAICompatibleLLM(config.actor)

    def _select_action_type(self, state: EnvState, reward: CriticReward, log_dir: Path) -> str:
        if len(state.skill_headers) > self.config.runtime.skill_count_limit and len(state.skill_headers) >= 2:
            return "merge_skills"
        system_prompt = render_prompt(
            self.config.prompt_root / "actor_system.md",
            skill_count_limit=self.config.runtime.skill_count_limit,
        )
        user_prompt = render_prompt(
            self.config.prompt_root / "actor_action_selection.md",
            state_json=json.dumps(
                {
                    "task_id": state.task_id,
                    "router_result": {
                        "selected_skill": state.router_result.selected_skill,
                        "scores": [score.__dict__ for score in state.router_result.scores],
                    },
                    "active_skill": None if state.active_skill is None else state.active_skill.header.__dict__,
                    "skill_headers": [header.__dict__ for header in state.skill_headers],
                },
                ensure_ascii=False,
                indent=2,
            ),
            reward_json=json.dumps(reward.__dict__, ensure_ascii=False, indent=2),
        )
        payload, llm_result = self.llm.chat_json(
            [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]
        )
        log_llm_call(log_dir, "actor_action_selection", llm_result)
        return str(payload.get("action_type", reward.recommended_action_type or "modify_skill"))

    def _model_json(self, system_prompt: str, user_prompt: str, log_dir: Path, log_name: str) -> dict:
        payload, llm_result = self.llm.chat_json(
            [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]
        )
        log_llm_call(log_dir, log_name, llm_result)
        return payload

    def _load_headers_map(self, headers: list[SkillHeader]) -> dict[str, SkillHeader]:
        return {header.name: header for header in headers}

    def act(
        self,
        state: EnvState,
        reward: CriticReward,
        log_dir: Path,
        *,
        forced_action_type: str | None = None,
    ) -> ActorDecision:
        experience_buffer = load_experience_buffer(self.config.experience_buffer_path)
        action_type = forced_action_type or self._select_action_type(state, reward, log_dir)
        system_prompt = render_prompt(
            self.config.prompt_root / "actor_system.md",
            skill_count_limit=self.config.runtime.skill_count_limit,
        )
        headers_map = self._load_headers_map(state.skill_headers)

        if action_type == "create_skill":
            user_prompt = render_prompt(
                self.config.prompt_root / "actor_create_skill.md",
                task_json=json.dumps(
                    {
                        "task_id": state.task_id,
                        "task_prompt": state.task_prompt,
                        "task_context": state.task_context,
                        "gold_tool_names": state.gold_tool_names,
                        "gold_trajectory": state.gold_trajectory,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                reward_json=json.dumps(reward.__dict__, ensure_ascii=False, indent=2),
                existing_skills_json=json.dumps([header.__dict__ for header in state.skill_headers], ensure_ascii=False, indent=2),
            )
            payload = self._model_json(system_prompt, user_prompt, log_dir, "actor_create_skill")
        elif action_type == "merge_skills":
            merge_names = reward.merge_candidates or [header.name for header in state.skill_headers[:2]]
            merge_details = [load_skill_detail(headers_map[name]) for name in merge_names if name in headers_map]
            user_prompt = render_prompt(
                self.config.prompt_root / "actor_merge_skill.md",
                reward_json=json.dumps(reward.__dict__, ensure_ascii=False, indent=2),
                merge_skills_json=json.dumps(
                    [
                        {
                            "header": detail.header.__dict__,
                            "body": detail.body,
                            "resources": detail.resources,
                        }
                        for detail in merge_details
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                task_json=json.dumps(
                    {
                        "task_id": state.task_id,
                        "task_prompt": state.task_prompt,
                        "gold_tool_names": state.gold_tool_names,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
            )
            payload = self._model_json(system_prompt, user_prompt, log_dir, "actor_merge_skill")
        else:
            target_name = reward.target_skill or (state.active_skill.header.name if state.active_skill else "")
            if not target_name and state.skill_headers:
                target_name = state.skill_headers[0].name
            target_detail = load_skill_detail(headers_map[target_name]) if target_name in headers_map else None
            similar = retrieve_similar_experiences(experience_buffer, reward.experience_note or reward.natural_language_reward)
            user_prompt = render_prompt(
                self.config.prompt_root / "actor_modify_skill.md",
                reward_json=json.dumps(reward.__dict__, ensure_ascii=False, indent=2),
                task_json=json.dumps(
                    {
                        "task_id": state.task_id,
                        "task_prompt": state.task_prompt,
                        "gold_tool_names": state.gold_tool_names,
                        "gold_trajectory": state.gold_trajectory,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                skill_json=json.dumps(
                    None
                    if target_detail is None
                    else {
                        "header": target_detail.header.__dict__,
                        "body": target_detail.body,
                        "resources": target_detail.resources,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                experiences_json=json.dumps([item.__dict__ for item in similar], ensure_ascii=False, indent=2),
            )
            payload = self._model_json(system_prompt, user_prompt, log_dir, "actor_modify_skill")
            action_type = "modify_skill"

        entry_payload = payload.get("experience_entry", {}) if isinstance(payload.get("experience_entry", {}), dict) else {}
        experience_entry = ExperienceEntry(
            task_id=state.task_id,
            failure_signature=str(entry_payload.get("failure_signature", reward.experience_note or reward.summary)),
            action_type=action_type,
            affected_skills=[str(item) for item in entry_payload.get("affected_skills", payload.get("merged_from", []))],
            modification_summary=str(entry_payload.get("modification_summary", payload.get("summary", ""))),
            reward_excerpt=reward.natural_language_reward[:500],
            created_at=utc_timestamp(),
        )

        decision = ActorDecision(
            action_type=action_type,
            summary=str(payload.get("summary", "")),
            target_skill_name=str(payload.get("target_skill_name", "")),
            merged_from=[str(item) for item in payload.get("merged_from", [])],
            files_to_write={str(k): str(v) for k, v in payload.get("files_to_write", {}).items()},
            files_to_delete=[str(item) for item in payload.get("files_to_delete", [])],
            experience_entry=experience_entry,
            raw_model_output=json.dumps(payload, ensure_ascii=False, indent=2),
        )
        write_json(log_dir / "actor_decision.json", decision.__dict__ | {"experience_entry": experience_entry.__dict__})
        return decision

    def apply(self, decision: ActorDecision) -> None:
        target_skill = decision.target_skill_name
        if decision.action_type == "merge_skills" and not target_skill:
            raise ValueError("merge_skills requires target_skill_name")
        if decision.action_type == "create_skill" and not target_skill:
            raise ValueError("create_skill requires target_skill_name")
        if decision.action_type == "modify_skill" and not target_skill:
            raise ValueError("modify_skill requires target_skill_name")
        write_skill_bundle(self.config.skill_library_root, target_skill, decision.files_to_write)
        delete_skill_dirs(self.config.skill_library_root, decision.files_to_delete or decision.merged_from)
        if decision.experience_entry is not None:
            append_experience(self.config.experience_buffer_path, decision.experience_entry)
