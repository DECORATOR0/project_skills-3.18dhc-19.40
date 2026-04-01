"""
Structured records for skill-based benchmark execution.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class BenchmarkItem:
    question_id: str
    question_text: str
    data_dir: str = ""
    file_list: list[str] = field(default_factory=list)
    gold_tool_names: list[str] = field(default_factory=list)
    gold_tool_calls: list[dict] = field(default_factory=list)
    choices: list = field(default_factory=list)
    gt_answer: str = ""


@dataclass
class PlannedSkillStep:
    tool_name: str
    rationale: str = ""


@dataclass
class ExecutedSkillStep:
    step_index: int
    planned_tool_name: str
    chosen_tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    raw_result: Any = None
    result_summary: str = ""
    success: bool = True
    error: str = ""
    worker_rationale: str = ""


@dataclass
class SkillExecutionRecord:
    question_id: str
    question_text: str
    data_dir: str
    skill_id: str
    planning_source: str
    planned_tool_sequence: list[PlannedSkillStep] = field(default_factory=list)
    executed_steps: list[ExecutedSkillStep] = field(default_factory=list)
    final_choice_index: int = 0
    final_choice_label: str = ""
    final_choice_text: str = ""
    final_choice_reason: str = ""
    final_answer_fallback_used: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SkillPlanRecord:
    question_id: str
    question_text: str
    data_dir: str
    skill_id: str
    planning_source: str
    shortlisted_tool_names: list[str] = field(default_factory=list)
    focus_tools: list[str] = field(default_factory=list)
    planner_system_prompt: str = ""
    planner_user_prompt: str = ""
    planner_raw_output: str = ""
    fallback_reason: str = ""
    planned_tool_sequence: list[PlannedSkillStep] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SkillParameterRecord:
    question_id: str
    question_text: str
    data_dir: str
    skill_id: str
    planning_source: str
    shortlisted_tool_names: list[str] = field(default_factory=list)
    planned_tool_sequence: list[PlannedSkillStep] = field(default_factory=list)
    executed_steps: list[ExecutedSkillStep] = field(default_factory=list)
    execution_summary: str = ""
    metrics: dict[str, Any] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class SkillAnswerRecord:
    question_id: str
    question_text: str
    data_dir: str
    skill_id: str
    planning_source: str
    execution_summary: str = ""
    final_choice_index: int = 0
    final_choice_label: str = ""
    final_choice_text: str = ""
    final_choice_reason: str = ""
    final_answer_fallback_used: bool = False
    metrics: dict[str, Any] = field(default_factory=dict)
    logs: list[dict[str, Any]] = field(default_factory=list)
