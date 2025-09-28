"""Benchmark helpers for the Picture→Calories pipeline."""

from __future__ import annotations

import json
import time
from pathlib import Path
from statistics import mean
from typing import Any, Dict, Iterable, List

from .core import StageMetrics
from ..models.visual_context import analyze_visual_context_from_file
from ..models.dish_determiner import determine_dishes_from_visual_json_and_image
from ..models.resturant_calories import restaurant_calories_pipeline
from ..pipeline import GuardrailViolation


def _percentiles(samples: List[float], percentiles: Iterable[float]) -> Dict[float, float]:
    if not samples:
        return {p: 0.0 for p in percentiles}
    sorted_vals = sorted(samples)
    result = {}
    for p in percentiles:
        k = (len(sorted_vals) - 1) * (p / 100.0)
        f = int(k)
        c = min(f + 1, len(sorted_vals) - 1)
        if f == c:
            result[p] = sorted_vals[int(k)]
        else:
            d0 = sorted_vals[f] * (c - k)
            d1 = sorted_vals[c] * (k - f)
            result[p] = d0 + d1
    return result


def run_benchmark(manifest_path: str, limit: int | None = None) -> Dict[str, Any]:
    """Run the full pipeline over the dataset manifest.

    The manifest should be a JSON list of objects with:
      - image_path: path to the input image
      - expected: optional reference metadata (primary_dish, items, etc.)
    """

    manifest = json.loads(Path(manifest_path).read_text())
    if limit:
        manifest = manifest[:limit]

    e2e_durations: List[float] = []
    stage_metrics: Dict[str, List[int]] = {"stage_A": [], "stage_B": [], "stage_C": [], "stage_D": [], "stage_E": [], "stage_F": []}
    reports: List[Dict[str, Any]] = []

    for entry in manifest:
        image_path = entry["image_path"]
        metrics = StageMetrics()
        start = time.perf_counter()
        visual = analyze_visual_context_from_file(image_path, metrics=metrics)
        dish = determine_dishes_from_visual_json_and_image(visual, visual.get("_image_token"), metrics=metrics)
        try:
            report = restaurant_calories_pipeline(visual, dish, visual.get("_image_token"), metrics=metrics)
        except GuardrailViolation as exc:
            report = {
                "error": exc.message,
                "stage": exc.stage,
                "details": exc.payload,
            }
        duration_ms = int((time.perf_counter() - start) * 1000)
        e2e_durations.append(duration_ms)
        for stage, dur in metrics.to_dict().items():
            stage_metrics.setdefault(stage, []).append(dur)
        report["_metrics_ms"] = metrics.to_dict()
        report["_duration_ms"] = duration_ms
        reports.append(report)

    percentiles = _percentiles(e2e_durations, [50, 90, 99])
    stage_percentiles = {stage: _percentiles(durations, [50, 90, 99]) for stage, durations in stage_metrics.items() if durations}

    return {
        "dataset": Path(manifest_path).stem,
        "runs": len(reports),
        "latency_ms": {
            "stage": stage_percentiles,
            "e2e": percentiles,
            "mean": mean(e2e_durations) if e2e_durations else 0.0,
        },
        "reports": reports,
    }
