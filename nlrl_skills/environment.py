from __future__ import annotations

import json
from pathlib import Path

from .agent_loop import JSONToolAgent
from .config import SystemConfig
from .evaluation import evaluate_execution
from .prompting import render_prompt
from .router import SkillRouter
from .schemas import DatasetTask, EnvRunResult, EnvState, EvaluationResult, RouterResult, RouterSkillScore, SkillDetail, SkillHeader, to_dict
from .skills import load_skill_detail
from .tools import ToolContext, Toolbox
from .utils import ensure_dir, write_json


class SkillEnvironment:
    def __init__(self, config: SystemConfig):
        self.config = config
        self.router = SkillRouter(config)
        tool_context = ToolContext(
            workspace_root=config.workspace_root,
            skill_library_root=config.skill_library_root,
            temp_root=config.run_root / "temp",
            python_executable=config.runtime.python_executable,
            shell_program=config.runtime.shell_program,
        )
        self.toolbox = Toolbox(tool_context)
        self.executor_agent = JSONToolAgent(
            config.executor,
            config.prompt_root,
            self.toolbox,
            max_context_chars=config.runtime.max_context_chars,
        )

    def _pick_active_skill(self, headers: list[SkillHeader], router_result: RouterResult) -> SkillDetail | None:
        if not router_result.selected_skill:
            return None
        for header in headers:
            if header.name == router_result.selected_skill:
                return load_skill_detail(header)
        return None

    def _forced_router_result(self, header: SkillHeader) -> RouterResult:
        return RouterResult(
            selected_skill=header.name,
            has_applicable_skill=True,
            scores=[
                RouterSkillScore(
                    skill_name=header.name,
                    score=100.0,
                    reason="Task-local single-skill execution bypassed router scoring.",
                )
            ],
            trajectory="Router was bypassed because this task-local training loop only maintains one active skill.",
            notes="forced-single-skill",
        )

    def run(
        self,
        task: DatasetTask,
        skill_headers: list[SkillHeader],
        run_dir: Path,
        *,
        forced_active_skill_name: str | None = None,
    ) -> EnvState:
        ensure_dir(run_dir)
        if forced_active_skill_name:
            forced_header = next((header for header in skill_headers if header.name == forced_active_skill_name), None)
            if forced_header is None:
                raise KeyError(f"Forced active skill not found: {forced_active_skill_name}")
            router_result = self._forced_router_result(forced_header)
            active_skill = load_skill_detail(forced_header)
        else:
            router_result = self.router.route(task, skill_headers, run_dir / "router")
            active_skill = self._pick_active_skill(skill_headers, router_result)
        if not router_result.has_applicable_skill or active_skill is None:
            env_result = EnvRunResult(
                final_answer="没有合适的skill",
                executor_summary="Router judged that no skill in the current library is applicable.",
                evaluation=EvaluationResult(
                    accuracy=0.0,
                    efficiency=0.0,
                    tool_any_order=0.0,
                    tool_in_order=0.0,
                    tool_exact_match=0.0,
                    parameter_accuracy=0.0,
                    task_success=False,
                    notes="Execution was skipped because the router found no applicable skill.",
                ),
            )
            state = EnvState(
                task_id=task.task_id,
                task_prompt=task.prompt,
                router_result=router_result,
                env_result=env_result,
                gold_trajectory=task.gold_trajectory,
                gold_tool_names=task.gold_tool_names,
                skill_headers=skill_headers,
                active_skill=None,
                task_context={
                    "data_dir": task.data_dir,
                    "file_list": task.file_list,
                    "choices": task.choices,
                    "gold_answer": task.gold_answer,
                },
            )
            write_json(run_dir / "state.json", to_dict(state))
            return state

        system_prompt = render_prompt(
            self.config.prompt_root / "executor_system.md",
            max_steps=self.config.runtime.max_executor_steps,
        )
        self.toolbox.set_active_skill_dir(active_skill.header.skill_dir)
        try:
            user_prompt = render_prompt(
                self.config.prompt_root / "executor_user.md",
                task_json=json.dumps(
                    {
                        "task_id": task.task_id,
                        "prompt": task.prompt,
                        "choices": task.choices,
                        "data_dir": task.data_dir,
                        "file_count": len(task.file_list),
                        "file_list_preview": task.file_list[:20],
                        "gold_answer_hidden": True,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                skill_name=active_skill.header.name,
                skill_md=active_skill.full_text,
                resources_json=json.dumps(active_skill.resources, ensure_ascii=False, indent=2),
            )
            final_payload, raw_output, tool_records = self.executor_agent.run(
                role_name="executor",
                base_system_prompt=system_prompt,
                user_prompt=user_prompt,
                allowed_tools=active_skill.header.allowed_tools or None,
                max_steps=self.config.runtime.max_executor_steps,
                log_dir=run_dir / "executor",
            )
        finally:
            self.toolbox.set_active_skill_dir(None)
        env_result = EnvRunResult(
            final_answer=str(final_payload.get("final_answer", "")),
            final_choice_label=str(final_payload.get("choice_label", "")),
            tool_trajectory=tool_records,
            executor_summary=str(final_payload.get("summary", "")),
            raw_executor_output=raw_output,
        )
        env_result.evaluation = evaluate_execution(
            final_choice_label=env_result.final_choice_label,
            final_answer=env_result.final_answer,
            executed_steps=tool_records,
            gold_tool_names=task.gold_tool_names,
            gold_trajectory=task.gold_trajectory,
            gold_answer=task.gold_answer,
        )
        state = EnvState(
            task_id=task.task_id,
            task_prompt=task.prompt,
            router_result=router_result,
            env_result=env_result,
            gold_trajectory=task.gold_trajectory,
            gold_tool_names=task.gold_tool_names,
            skill_headers=skill_headers,
            active_skill=active_skill,
            task_context={
                "data_dir": task.data_dir,
                "file_list": task.file_list,
                "choices": task.choices,
                "gold_answer": task.gold_answer,
            },
        )
        write_json(run_dir / "state.json", to_dict(state))
        return state
