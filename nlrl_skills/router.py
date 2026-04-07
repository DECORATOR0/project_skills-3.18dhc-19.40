from __future__ import annotations

from difflib import get_close_matches
import json
from pathlib import Path

from .config import SystemConfig
from .llm import OpenAICompatibleLLM, log_llm_call
from .prompting import render_prompt
from .schemas import DatasetTask, RouterResult, RouterSkillScore, SkillHeader, LLMMessage


def _resolve_skill_name(name: str, valid_names: set[str]) -> str:
    candidate = str(name).strip()
    if not candidate:
        return ""
    if candidate in valid_names:
        return candidate
    matches = get_close_matches(candidate, list(valid_names), n=1, cutoff=0.88)
    return matches[0] if matches else ""


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
            skills_json=json.dumps(
                [
                    {
                        "name": header.name,
                        "description": header.description,
                    }
                    for header in skill_headers
                ],
                ensure_ascii=False,
                indent=2,
            ),
        )
        payload, llm_result = self.llm.chat_json(
            [
                LLMMessage(role="system", content=system_prompt),
                LLMMessage(role="user", content=user_prompt),
            ]
        )
        log_llm_call(log_dir, "router", llm_result)

        valid_names = {header.name for header in skill_headers}
        score_by_name: dict[str, RouterSkillScore] = {}
        for item in payload.get("scores", []):
            resolved_name = _resolve_skill_name(item.get("skill_name", ""), valid_names)
            if not resolved_name:
                continue
            score = RouterSkillScore(
                skill_name=resolved_name,
                score=float(item.get("score", 0.0)),
                reason=str(item.get("reason", "")),
            )
            existing = score_by_name.get(resolved_name)
            if existing is None or score.score > existing.score:
                score_by_name[resolved_name] = score
        scores = [
            score_by_name.get(
                header.name,
                RouterSkillScore(
                    skill_name=header.name,
                    score=0.0,
                    reason="Router response omitted this catalog skill; defaulted to score 0.",
                ),
            )
            for header in skill_headers
        ]
        selected = _resolve_skill_name(payload.get("selected_skill", ""), valid_names)
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
