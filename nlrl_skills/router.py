from __future__ import annotations

import json
from pathlib import Path

from .config import SystemConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import render_prompt
from .schemas import DatasetTask, RouterResult, RouterSkillScore, SkillHeader, LLMMessage


class SkillRouter:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.llm = OpenAICompatibleLLM(config.router)

    def route(self, task: DatasetTask, skill_headers: list[SkillHeader], log_dir: Path) -> RouterResult:
        if not skill_headers:
            return RouterResult(
                selected_skill="",
                has_applicable_skill=False,
                scores=[],
                trajectory="No skills were available in the current skill library.",
                notes="Router stopped at selection stage because the library is empty.",
            )

        system_prompt = render_prompt(
            self.config.prompt_root / "router_system.md",
            threshold=self.config.runtime.skill_match_threshold,
        )
        user_prompt = render_prompt(
            self.config.prompt_root / "router_user.md",
            task_json=json.dumps(
                {
                    "task_id": task.task_id,
                    "prompt": task.prompt,
                    "choices": task.choices,
                    "data_dir": task.data_dir,
                    "file_list": task.file_list[:20],
                },
                ensure_ascii=False,
                indent=2,
            ),
            skills_json=json.dumps([header.__dict__ for header in skill_headers], ensure_ascii=False, indent=2),
        )
        payload, llm_result = self.llm.chat_json(
            [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]
        )
        log_llm_call(log_dir, "router", llm_result)

        scores = [
            RouterSkillScore(
                skill_name=str(item.get("skill_name", "")),
                score=float(item.get("score", 0.0)),
                reason=str(item.get("reason", "")),
            )
            for item in payload.get("scores", [])
        ]
        selected = str(payload.get("selected_skill", "")).strip()
        has_skill = bool(payload.get("has_applicable_skill", False)) and any(
            item.score >= self.config.runtime.skill_match_threshold for item in scores
        )
        if selected and not any(item.skill_name == selected and item.score >= self.config.runtime.skill_match_threshold for item in scores):
            selected = ""
            has_skill = False
        return RouterResult(
            selected_skill=selected,
            has_applicable_skill=has_skill,
            scores=scores,
            trajectory=str(payload.get("trajectory", "")),
            notes=str(payload.get("notes", "")),
        )
