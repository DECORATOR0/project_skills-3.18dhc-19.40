from __future__ import annotations

from typing import Any

from .schemas import EvaluationResult, ToolCallRecord


def _normalize_name(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(" ", "_")


def tool_any_order(predicted: list[str], gold: list[str]) -> float:
    if not gold:
        return 1.0
    gold_set = {_normalize_name(item) for item in gold}
    pred_set = {_normalize_name(item) for item in predicted}
    return len(gold_set & pred_set) / len(gold_set)


def tool_in_order(predicted: list[str], gold: list[str]) -> float:
    pred = [_normalize_name(item) for item in predicted]
    target = [_normalize_name(item) for item in gold]
    if not target:
        return 1.0
    dp = [[0] * (len(target) + 1) for _ in range(len(pred) + 1)]
    for i in range(1, len(pred) + 1):
        for j in range(1, len(target) + 1):
            if pred[i - 1] == target[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[-1][-1] / len(target)


def tool_exact_match(predicted: list[str], gold: list[str]) -> float:
    pred = [_normalize_name(item) for item in predicted]
    target = [_normalize_name(item) for item in gold]
    if not target:
        return 1.0
    matched = 0
    for left, right in zip(pred, target):
        if left != right:
            break
        matched += 1
    return matched / len(target)


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _normalize_value(v) for k, v in sorted(value.items(), key=lambda item: str(item[0]))}
    if isinstance(value, list):
        return [_normalize_value(v) for v in value]
    if isinstance(value, str):
        return value.strip().replace("\\", "/")
    return value


def parameter_accuracy(executed_steps: list[ToolCallRecord], gold_trajectory: list[dict[str, Any]]) -> float:
    gold_calls: list[dict[str, Any]] = []
    for turn in gold_trajectory:
        if turn.get("role") == "assistant":
            for call in turn.get("tool_calls", []):
                fn = call.get("function", {})
                gold_calls.append(
                    {
                        "name": fn.get("name", ""),
                        "arguments": fn.get("arguments", {}),
                    }
                )
    if not gold_calls:
        return 1.0
    matched = 0
    for step, gold in zip(executed_steps, gold_calls):
        if _normalize_name(step.tool_name) != _normalize_name(str(gold.get("name", ""))):
            break
        if _normalize_value(step.arguments) != _normalize_value(gold.get("arguments", {})):
            break
        matched += 1
    return matched / len(gold_calls)


def answer_accuracy(predicted_choice: str, gold_answer: str) -> float:
    if not gold_answer:
        return 0.0
    return 1.0 if predicted_choice.strip().upper() == gold_answer.strip().upper() else 0.0


def efficiency(predicted_count: int, gold_count: int) -> float:
    if gold_count == 0:
        return 1.0
    return predicted_count / gold_count


def evaluate_execution(
    *,
    final_choice_label: str,
    final_answer: str,
    executed_steps: list[ToolCallRecord],
    gold_tool_names: list[str],
    gold_trajectory: list[dict[str, Any]],
    gold_answer: str,
) -> EvaluationResult:
    predicted_names = [step.tool_name for step in executed_steps]
    acc = answer_accuracy(final_choice_label or final_answer, gold_answer)
    return EvaluationResult(
        accuracy=round(acc, 4),
        efficiency=round(efficiency(len(predicted_names), len(gold_tool_names)), 4),
        tool_any_order=round(tool_any_order(predicted_names, gold_tool_names), 4),
        tool_in_order=round(tool_in_order(predicted_names, gold_tool_names), 4),
        tool_exact_match=round(tool_exact_match(predicted_names, gold_tool_names), 4),
        parameter_accuracy=round(parameter_accuracy(executed_steps, gold_trajectory), 4),
        task_success=bool(acc == 1.0),
        notes="Earth-Bench style trajectory/result metrics.",
    )
