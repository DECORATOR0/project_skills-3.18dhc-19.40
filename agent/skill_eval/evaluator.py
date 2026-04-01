"""
Full Earth-Bench-style evaluation for skill execution.

Implements:
- Accuracy
- Efficiency
- Tools-Any-Order (TAO)
- Tools-In-Order (TIO)
- Tool-Exact-Match (TEM)
- Parameter Accuracy
"""
from __future__ import annotations

import os
import random
from typing import Any

from .schemas import SkillExecutionRecord


def _normalize_name(name: str) -> str:
    return name.strip().lower().replace("-", "_").replace(" ", "_")


def tool_any_order(predicted: list[str], gold: list[str]) -> float:
    if not gold:
        return 1.0
    pred_set = set(_normalize_name(n) for n in predicted)
    gold_set = set(_normalize_name(n) for n in gold)
    if not gold_set:
        return 1.0
    return len(gold_set & pred_set) / len(gold_set)


def tool_in_order(predicted: list[str], gold: list[str]) -> float:
    pred_norm = [_normalize_name(n) for n in predicted]
    gold_norm = [_normalize_name(n) for n in gold]
    m = len(gold_norm)
    if m == 0:
        return 1.0
    n = len(pred_norm)
    dp = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            if pred_norm[i - 1] == gold_norm[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    return dp[n][m] / m


def tool_exact_match(predicted: list[str], gold: list[str]) -> float:
    pred_norm = [_normalize_name(n) for n in predicted]
    gold_norm = [_normalize_name(n) for n in gold]
    m = len(gold_norm)
    if m == 0:
        return 1.0
    lcp = 0
    for p, g in zip(pred_norm, gold_norm):
        if p == g:
            lcp += 1
        else:
            break
    return lcp / m


def _normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _normalize_value(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, list):
        return [_normalize_value(v) for v in value]
    if isinstance(value, tuple):
        return [_normalize_value(v) for v in value]
    if isinstance(value, str):
        text = value.strip()
        for prefix in ("Result saved at ", "Result save at "):
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        text = text.replace("\\", "/")
        text = text.replace("//", "/")
        # Normalize absolute workspace paths to benchmark-relative style when possible.
        lower = text.lower()
        marker = "/benchmark/"
        if marker in lower:
            idx = lower.index(marker)
            text = text[idx + 1 :]
        text = os.path.normpath(text).replace("\\", "/")
        return text
    return value


def _structural_equal(a: Any, b: Any) -> bool:
    return _normalize_value(a) == _normalize_value(b)


def parameter_accuracy(predicted_steps: list[dict], gold_calls: list[dict]) -> float:
    m = len(gold_calls)
    if m == 0:
        return 1.0
    l_param = 0
    for pred, gold in zip(predicted_steps, gold_calls):
        pred_name = _normalize_name(pred.get("name", ""))
        gold_name = _normalize_name(gold.get("name", ""))
        pred_args = pred.get("arguments", {})
        gold_args = gold.get("arguments", {})
        if pred_name == gold_name and _structural_equal(pred_args, gold_args):
            l_param += 1
        else:
            break
    return l_param / m


def answer_accuracy(predicted_label: str, gold_label: str) -> float:
    if not gold_label:
        return 0.0
    return 1.0 if predicted_label.strip().upper() == gold_label.strip().upper() else 0.0


def efficiency(predicted_count: int, gold_count: int) -> float:
    if gold_count == 0:
        return 1.0
    return predicted_count / gold_count


def evaluate_execution(record: SkillExecutionRecord, gold_tool_calls: list[dict], gold_answer: str) -> dict[str, Any]:
    predicted_tools = [step.chosen_tool_name for step in record.executed_steps]
    gold_tools = [call.get("name", "") for call in gold_tool_calls]
    predicted_calls = [
        {"name": step.chosen_tool_name, "arguments": step.arguments}
        for step in record.executed_steps
    ]
    metrics = {
        "accuracy": round(answer_accuracy(record.final_choice_label, gold_answer), 4),
        "efficiency": round(efficiency(len(predicted_calls), len(gold_tool_calls)), 4),
        "tool_any_order": round(tool_any_order(predicted_tools, gold_tools), 4),
        "tool_in_order": round(tool_in_order(predicted_tools, gold_tools), 4),
        "tool_exact_match": round(tool_exact_match(predicted_tools, gold_tools), 4),
        "parameter_accuracy": round(parameter_accuracy(predicted_calls, gold_tool_calls), 4),
        "predicted_count": len(predicted_calls),
        "gold_count": len(gold_tool_calls),
    }
    return metrics


def aggregate_execution_metrics(records: list[SkillExecutionRecord]) -> dict[str, Any]:
    if not records:
        return {}
    fields = [
        "accuracy",
        "efficiency",
        "tool_any_order",
        "tool_in_order",
        "tool_exact_match",
        "parameter_accuracy",
    ]
    summary = {"count": len(records)}
    for field in fields:
        summary[f"avg_{field}"] = round(sum(r.metrics.get(field, 0.0) for r in records) / len(records), 4)
    return summary


def safe_choice_fallback(choices: list[str], seed: str) -> tuple[int, str, str]:
    rng = random.Random(seed)
    idx = rng.randrange(len(choices)) if choices else 0
    label = chr(ord("A") + idx) if choices else ""
    text = str(choices[idx]) if choices else ""
    return idx + 1, label, text
