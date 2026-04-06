from __future__ import annotations

from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any


def to_dict(value: Any) -> Any:
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, list):
        return [to_dict(v) for v in value]
    if isinstance(value, dict):
        return {k: to_dict(v) for k, v in value.items()}
    return value


@dataclass
class LLMMessage:
    role: str
    content: str


@dataclass
class ToolCallRecord:
    step_index: int
    thought: str
    tool_name: str
    arguments: dict[str, Any]
    observation: str
    success: bool
    raw_result: Any = None
    error: str = ""


@dataclass
class SkillHeader:
    name: str
    description: str
    skill_dir: str
    skill_md_path: str
    compatibility: str = ""
    allowed_tools: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class SkillDetail:
    header: SkillHeader
    body: str
    resources: list[str] = field(default_factory=list)
    full_text: str = ""


@dataclass
class RouterSkillScore:
    skill_name: str
    score: float
    reason: str


@dataclass
class RouterResult:
    selected_skill: str
    has_applicable_skill: bool
    scores: list[RouterSkillScore] = field(default_factory=list)
    trajectory: str = ""
    notes: str = ""


@dataclass
class EvaluationResult:
    accuracy: float = 0.0
    efficiency: float = 0.0
    tool_any_order: float = 0.0
    tool_in_order: float = 0.0
    tool_exact_match: float = 0.0
    parameter_accuracy: float = 0.0
    task_success: bool = False
    notes: str = ""


@dataclass
class EnvRunResult:
    final_answer: str
    final_choice_label: str = ""
    tool_trajectory: list[ToolCallRecord] = field(default_factory=list)
    executor_summary: str = ""
    raw_executor_output: str = ""
    evaluation: EvaluationResult = field(default_factory=EvaluationResult)


@dataclass
class EnvState:
    task_id: str
    task_prompt: str
    router_result: RouterResult
    env_result: EnvRunResult
    gold_trajectory: list[dict[str, Any]]
    gold_tool_names: list[str]
    skill_headers: list[SkillHeader]
    active_skill: SkillDetail | None
    task_context: dict[str, Any] = field(default_factory=dict)
    evaluation_mode: str = "skill-executor"


@dataclass
class CriticReward:
    natural_language_reward: str
    recommended_action_type: str
    create_new_skill: bool = False
    merge_candidates: list[str] = field(default_factory=list)
    target_skill: str = ""
    reward_dimensions: dict[str, Any] = field(default_factory=dict)
    experience_note: str = ""
    summary: str = ""


@dataclass
class ExperienceEntry:
    task_id: str
    failure_signature: str
    action_type: str
    affected_skills: list[str]
    modification_summary: str
    reward_excerpt: str
    created_at: str


@dataclass
class ActorDecision:
    action_type: str
    summary: str
    target_skill_name: str = ""
    merged_from: list[str] = field(default_factory=list)
    files_to_write: dict[str, str] = field(default_factory=dict)
    files_to_delete: list[str] = field(default_factory=list)
    experience_entry: ExperienceEntry | None = None
    raw_model_output: str = ""


@dataclass
class DatasetTask:
    task_id: str
    source_type: str
    prompt: str
    choices: list[str]
    gold_answer: str
    data_dir: str
    file_list: list[str]
    gold_trajectory: list[dict[str, Any]]
    gold_tool_names: list[str]
    original_record: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TrainIterationRecord:
    iteration_index: int
    state: EnvState
    reward: CriticReward
    actor_decision: ActorDecision | None = None
