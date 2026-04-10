from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
import random
from pathlib import Path
import re

from .schemas import DatasetTask
from .utils import ensure_dir, write_json, write_text

DEFAULT_BUCKET_QUOTAS: dict[str, int] = {
    "drought-tvdi": 2,
    "ati": 3,
    "thermal-retrieval": 5,
    "derived-index": 3,
    "timeseries-aggregation": 3,
    "hotspot-fire": 2,
    "arithmetic": 2,
    "scene": 2,
    "counting": 2,
    "grounding-geometry": 3,
    "area-change": 3,
}

BUCKET_TO_MODALITY: dict[str, str] = {
    "drought-tvdi": "spectrum",
    "ati": "spectrum",
    "thermal-retrieval": "spectrum",
    "derived-index": "products",
    "timeseries-aggregation": "products",
    "hotspot-fire": "products",
    "arithmetic": "products",
    "scene": "rgb",
    "counting": "rgb",
    "grounding-geometry": "rgb",
    "area-change": "rgb",
}


@dataclass(frozen=True)
class TaskBucketInfo:
    task_id: str
    original_question_id: str
    modality: str
    bucket: str
    prompt: str
    file_count: int


def infer_task_modality(task: DatasetTask) -> str:
    question_id = str(task.metadata.get("original_question_id", task.task_id))
    if question_id.isdigit():
        qid = int(question_id)
        if qid <= 100:
            return "spectrum"
        if qid <= 188:
            return "products"
        return "rgb"

    combined = f"{task.prompt} {' '.join(task.file_list)}".lower()
    if re.search(r"\.(png|jpg|jpeg)\b", combined):
        return "rgb"
    if any(
        token in combined
        for token in (
            "lst",
            "split-window",
            "split window",
            "single-channel",
            "single channel",
            "multi-channel",
            "tvdi",
            "ati",
            "thermal inertia",
            "band 31",
            "band 32",
            "modis",
            "emissivity",
        )
    ):
        return "spectrum"
    return "products"


def classify_task_bucket(task: DatasetTask) -> TaskBucketInfo:
    question_id = str(task.metadata.get("original_question_id", task.task_id))
    qid = int(question_id) if question_id.isdigit() else None
    question = task.prompt.lower()
    modality = infer_task_modality(task)

    if modality == "spectrum":
        bucket = "drought-tvdi"
        if re.search(r"\bati\b", question) or "thermal inertia" in question:
            bucket = "ati"
        elif any(
            token in question
            for token in (
                "split-window",
                "split window",
                "single-channel",
                "single channel",
                "multi-channel",
                "tes",
                "emissivity",
                "band 31",
                "band 32",
                "lst",
                "modis",
                "ttm",
                "water vapor",
                "pwv",
            )
        ):
            bucket = "thermal-retrieval"
    elif modality == "products":
        if any(token in question for token in ("hotspot", "fire", "burned", "burn scar", "fire-prone")):
            bucket = "hotspot-fire"
        elif any(
            token in question
            for token in (
                "built_volume",
                "residential volume",
                "non-residential",
                "commercial energy saving",
                "percentage of change",
                "subtract",
                "division",
                "ratio",
            )
        ):
            bucket = "arithmetic"
        elif any(
            token in question
            for token in (
                "ndvi",
                "ndwi",
                "ndti",
                "ndsi",
                "nbr",
                "cloud-masked",
                "cloud masked",
                "turbidity",
                "water body",
            )
        ):
            bucket = "derived-index"
        else:
            bucket = "timeseries-aggregation"
    else:
        if qid is not None and 189 <= qid <= 203:
            bucket = "scene"
        elif "every image belongs to {" in question or "belongs to {" in question:
            bucket = "scene"
        elif any(
            token in question
            for token in (
                "centroid coordinates",
                "distance between",
                "closest pair",
                "farthest pair",
                "westernmost",
                "easternmost",
                "northernmost",
                "southernmost",
                "bounding boxes of the closest pair",
                "bounding boxes of the farthest pair",
                "region that corresponds",
            )
        ):
            bucket = "grounding-geometry"
        elif any(
            token in question
            for token in (
                "total area",
                "area of changed buildings",
                "building area",
                "area change",
                "harbor areas",
                "destroyed building",
                "reduction in total building area",
                "building damage",
                "restore the area",
                "decreased",
                "greater destruction",
                "severe damage",
                "disaster",
            )
        ) or ("difference between" in question and "area" in question):
            bucket = "area-change"
        elif any(
            token in question
            for token in (
                "count the number",
                "how many",
                "number of airplanes",
                "number of ships",
                "largest number of storage tanks",
                "sort the images based on the number",
            )
        ):
            bucket = "counting"
        else:
            bucket = "counting"

    return TaskBucketInfo(
        task_id=task.task_id,
        original_question_id=question_id,
        modality=BUCKET_TO_MODALITY[bucket],
        bucket=bucket,
        prompt=task.prompt,
        file_count=len(task.file_list),
    )


def build_task_set_manifest(
    tasks: list[DatasetTask],
    *,
    seed: int = 20260403,
    bucket_quotas: dict[str, int] | None = None,
    quota_scale: int = 1,
) -> dict:
    if quota_scale <= 0:
        raise ValueError("quota_scale must be >= 1")
    quotas = dict(DEFAULT_BUCKET_QUOTAS)
    quotas = {bucket: count * quota_scale for bucket, count in quotas.items()}
    quotas.update(bucket_quotas or {})
    rng = random.Random(seed)

    by_bucket: dict[str, list[TaskBucketInfo]] = defaultdict(list)
    by_modality: dict[str, list[TaskBucketInfo]] = defaultdict(list)
    for task in tasks:
        info = classify_task_bucket(task)
        by_bucket[info.bucket].append(info)
        by_modality[info.modality].append(info)

    chosen_ids: set[str] = set()
    selected: dict[str, list[TaskBucketInfo]] = defaultdict(list)

    for bucket, count in quotas.items():
        bucket_candidates = [item for item in by_bucket.get(bucket, []) if item.original_question_id not in chosen_ids]
        rng.shuffle(bucket_candidates)
        if len(bucket_candidates) < count:
            raise ValueError(f"Bucket {bucket} only has {len(bucket_candidates)} available tasks, need {count}.")
        selected[bucket] = bucket_candidates[:count]
        chosen_ids.update(item.original_question_id for item in selected[bucket])

    modality_entries: dict[str, list[dict]] = defaultdict(list)
    for bucket, items in selected.items():
        for item in sorted(items, key=lambda value: int(value.original_question_id)):
            modality_entries[item.modality].append(
                {
                    "task_id": item.task_id,
                    "original_question_id": item.original_question_id,
                    "bucket": item.bucket,
                    "file_count": item.file_count,
                    "prompt": item.prompt,
                }
            )

    smoke_candidates = []
    for modality in ("spectrum", "products", "rgb"):
        if modality_entries[modality]:
            smoke_candidates.append(
                min(
                    modality_entries[modality],
                    key=lambda item: (item["file_count"], int(item["original_question_id"])),
                )
            )

    return {
        "seed": seed,
        "quota_scale": quota_scale,
        "selection_policy": "bucket_random_uniform_no_light_bias",
        "smoke_policy": "per_modality_min_file_count_within_selected",
        "bucket_quotas": quotas,
        "modalities": {
            modality: sorted(items, key=lambda item: int(item["original_question_id"]))
            for modality, items in modality_entries.items()
        },
        "all_task_ids": [
            item["original_question_id"]
            for modality in ("spectrum", "products", "rgb")
            for item in modality_entries[modality]
        ],
        "smoke_task_ids": [item["original_question_id"] for item in smoke_candidates],
    }


def export_task_set_manifest(output_dir: Path, manifest: dict) -> None:
    ensure_dir(output_dir)
    write_json(output_dir / "task_set_manifest.json", manifest)
    for modality in ("spectrum", "products", "rgb"):
        lines = [str(item["original_question_id"]) for item in manifest["modalities"].get(modality, [])]
        write_text(output_dir / f"train_{modality}_{len(lines)}.txt", "\n".join(lines) + ("\n" if lines else ""))
    write_text(output_dir / f"train_all_{len(manifest['all_task_ids'])}.txt", "\n".join(manifest["all_task_ids"]) + "\n")
    write_text(output_dir / f"smoke_{len(manifest['smoke_task_ids'])}.txt", "\n".join(manifest["smoke_task_ids"]) + "\n")
