from __future__ import annotations

import json
import logging
from pathlib import Path

from .agent_loop import PhaseExecutorAgent
from .config import SystemConfig
from .evaluation import evaluate_execution
from .prompting import render_prompt
from .schemas import (
    DatasetTask,
    EnvRunResult,
    EnvState,
    SkillDetail,
    SkillPhase,
    to_dict,
)
from .tools import ToolContext, Toolbox
from .utils import ensure_dir, write_json

_logger = logging.getLogger("nlrl_skills.environment")

_DEFAULT_INIT_PHASE = SkillPhase(
    name="INIT",
    content=(
        "Explore the task data directory with `list_dir` to understand the available files.\n"
        "Then decide which processing phase to enter based on file types.\n\n"
        "Available actions:\n"
        "- <CALL>list_dir</CALL><ARGS>{\"path\": \"DATA_DIR\"}</ARGS>\n"
        "- <NEXT>PROCESS</NEXT> to begin processing\n"
        "- <ANSWER>X</ANSWER> if you already know the answer"
    ),
    order=0,
)

_DEFAULT_PROCESS_PHASE = SkillPhase(
    name="PROCESS",
    content=(
        "Process the task using the available EO tools.\n"
        "Follow the tool chain suggested by the task type.\n\n"
        "When you have enough evidence, move to CONCLUDE:\n"
        "- <NEXT>CONCLUDE</NEXT>"
    ),
    order=1,
)

_DEFAULT_CONCLUDE_PHASE = SkillPhase(
    name="CONCLUDE",
    content=(
        "Review all evidence collected so far.\n"
        "Provide your final answer as a single choice letter.\n\n"
        "You MUST output: <ANSWER>A</ANSWER> (or B, C, D, etc.)"
    ),
    order=2,
)


def _ensure_phases(phases: dict[str, SkillPhase]) -> dict[str, SkillPhase]:
    """Guarantee at least INIT and CONCLUDE phases exist."""
    result = dict(phases)
    if "INIT" not in result:
        result["INIT"] = _DEFAULT_INIT_PHASE
    if "CONCLUDE" not in result:
        result["CONCLUDE"] = _DEFAULT_CONCLUDE_PHASE
    return result


class SkillEnvironment:
    """Phase-based progressive disclosure executor environment.

    Instead of injecting the full SKILL.md into the prompt, this environment:
      1. Parses ``## Phase: NAME`` sections from the skill body.
      2. Injects only the INIT phase content at startup (Level 2 activation).
      3. On ``<NEXT>PHASE</NEXT>`` tags, injects the next phase content.
      4. Scripts and references are loaded on demand (Level 3) via tool calls.
      5. Captures the final answer via ``<ANSWER>X</ANSWER>`` tags.
    """

    def __init__(self, config: SystemConfig, *, shared_eo_runtime=None):
        self.config = config
        tool_context = ToolContext(
            workspace_root=config.workspace_root,
            skill_library_root=config.skill_library_root,
            temp_root=config.run_root / "temp",
            python_executable=config.runtime.python_executable,
            shell_program=config.runtime.shell_program,
            eo_runtime=shared_eo_runtime,
        )
        self.toolbox = Toolbox(tool_context)
        self.executor_agent = PhaseExecutorAgent(
            config.executor,
            config.prompt_root,
            self.toolbox,
            max_context_chars=config.runtime.max_context_chars,
        )

    def _build_initial_prompt(
        self,
        task: DatasetTask,
        skill: SkillDetail,
        init_phase: SkillPhase,
    ) -> str:
        task_info = json.dumps(
            {
                "task_id": task.task_id,
                "prompt": task.prompt,
                "choices": task.choices,
                "data_dir": task.data_dir,
                "file_count": len(task.file_list),
                "file_list_preview": task.file_list[:20],
            },
            ensure_ascii=False,
            indent=2,
        )
        resources_list = ", ".join(skill.resources) if skill.resources else "(none)"

        return (
            f"## Task\n\n{task_info}\n\n"
            f"## Activated Skill: {skill.header.name}\n\n"
            f"**Description**: {skill.header.description}\n\n"
            f"**Bundled resources** (use `read_file` or `run_python_script` to access):\n"
            f"{resources_list}\n\n"
            f"=== Phase: INIT ===\n\n"
            f"{init_phase.content}\n\n"
            "Begin by following the INIT phase instructions above."
        )

    def run(
        self,
        task: DatasetTask,
        active_skill: SkillDetail,
        run_dir: Path,
    ) -> EnvState:
        ensure_dir(run_dir)

        phases = _ensure_phases(active_skill.phases)
        phase_names = sorted(phases.keys(), key=lambda n: phases[n].order)
        _logger.debug(
            "Skill '%s' has %d phases: %s",
            active_skill.header.name, len(phases), phase_names,
        )

        system_prompt = render_prompt(
            self.config.prompt_root / "executor_system.md",
            max_steps=self.config.runtime.max_executor_steps,
            phase_list=", ".join(phase_names),
        )

        init_phase = phases["INIT"]
        initial_prompt = self._build_initial_prompt(task, active_skill, init_phase)

        self.toolbox.set_active_skill_dir(active_skill.header.skill_dir)
        self.toolbox.set_active_task_data_dir(task.data_dir)
        try:
            final_action, raw_output, tool_records, transitions = (
                self.executor_agent.run(
                    role_name="executor",
                    system_prompt=system_prompt,
                    initial_user_prompt=initial_prompt,
                    phases=phases,
                    allowed_tools=active_skill.header.allowed_tools or None,
                    max_steps=self.config.runtime.max_executor_steps,
                    log_dir=run_dir / "executor",
                )
            )
        finally:
            self.toolbox.set_active_skill_dir(None)
            self.toolbox.set_active_task_data_dir(None)

        final_answer = final_action.answer or ""
        choice_label = final_answer if len(final_answer) == 1 else ""

        env_result = EnvRunResult(
            final_answer=final_answer,
            final_choice_label=choice_label,
            tool_trajectory=tool_records,
            phase_transitions=transitions,
            executor_summary=final_action.thought,
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
            env_result=env_result,
            gold_trajectory=task.gold_trajectory,
            gold_tool_names=task.gold_tool_names,
            active_skill_name=active_skill.header.name,
            task_context={
                "data_dir": task.data_dir,
                "file_list": task.file_list,
                "choices": task.choices,
                "gold_answer": task.gold_answer,
            },
        )
        write_json(run_dir / "state.json", to_dict(state))
        return state
