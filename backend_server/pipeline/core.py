"""Core helpers for the calorie estimation pipeline."""

from __future__ import annotations

import json
import re
import time
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .schemas import (
    DishDeterminationSchema,
    FinalItem,
    FinalMealReport,
    ItemizedMealSchema,
    NutritionBreakdown,
    StageFlags,
    Totals,
    VisualContextSchema,
)
from .schemas import ensure_list

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "canonical_labels.json"


@lru_cache(maxsize=1)
def _load_canonical() -> Dict[str, Dict[str, Iterable[str]]]:
    if not DATA_PATH.exists():
        return {"items": {}, "component_roles": {}}
    with DATA_PATH.open("r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        "items": {k: tuple(v) for k, v in data.get("items", {}).items()},
        "component_roles": {k: tuple(v) for k, v in data.get("component_roles", {}).items()},
    }


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def canonicalize_label(label: str) -> str:
    if not label:
        return label
    norm = _norm(label)
    data = _load_canonical()
    for canonical, aliases in data["items"].items():
        if norm == canonical or norm in aliases:
            return canonical
        if any(norm == _norm(alias) for alias in aliases):
            return canonical
    return norm or label


def canonicalize_component_role(role: str | None, label: str) -> str:
    base = (role or "").lower() or "main"
    data = _load_canonical()["component_roles"]
    for canonical_role, members in data.items():
        if label in members and canonical_role != base:
            return canonical_role
    if base in {"main", "sides", "drinks", "extras"}:
        return base
    return "main"


@dataclass
class GuardrailViolation(Exception):
    stage: str
    message: str
    payload: Optional[Dict[str, Any]] = None


def normalize_visual_context(raw: Dict[str, Any]) -> Dict[str, Any]:
    schema = VisualContextSchema.model_validate(raw)
    detections = []
    for det in schema.detections:
        clean_name = canonicalize_label(det.name)
        det_dict = det.model_copy(update={"name": clean_name}).model_dump()
        detections.append(det_dict)
    data = schema.model_copy(update={"detections": detections}).model_dump()
    return data


def normalize_dish_determination(raw: Dict[str, Any]) -> Dict[str, Any]:
    schema = DishDeterminationSchema.model_validate(raw)
    comps = schema.components
    normalized_components = {}
    for role_name in ["main", "sides", "drinks", "extras"]:
        items = []
        for entry in ensure_list(getattr(comps, role_name)):
            canonical_name = canonicalize_label(entry.name)
            normalized_role = canonicalize_component_role(role_name, canonical_name)
            items.append(
                entry.model_copy(
                    update={"name": canonical_name, "mapped_role": normalized_role}
                ).model_dump()
            )
        normalized_components[role_name] = items
    updated = schema.model_copy(update={"components": normalized_components})
    return updated.model_dump()


def _component_name_set(dish_json: Dict[str, Any]) -> Dict[str, List[str]]:
    comps = dish_json.get("components") or {}
    result: Dict[str, List[str]] = {}
    for role, entries in comps.items():
        names = []
        for item in ensure_list(entries):
            name = canonicalize_label(item.get("name")) if isinstance(item, dict) else str(item)
            if name:
                names.append(name)
        result[role] = names
    return result


def reconcile_stage_outputs(
    dish_json: Dict[str, Any],
    itemized: Dict[str, Any],
    confidence_floor: float = 0.4,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Reconcile Stage B and C outputs."""

    dish_components = _component_name_set(dish_json)
    schema = ItemizedMealSchema.model_validate(itemized)
    normalized_items = []
    mismatches: List[str] = []
    low_confidence = False

    for entry in schema.items:
        canonical_name = canonicalize_label(entry.item_name)
        mapped_role = canonicalize_component_role(
            entry.mapped_from_component or entry.category, canonical_name
        )
        entry_conf = entry.confidence if entry.confidence is not None else 0.5
        if entry_conf < confidence_floor:
            low_confidence = True
        allowed_names = dish_components.get(mapped_role, [])
        if allowed_names and canonical_name not in allowed_names:
            mismatches.append(f"{canonical_name} not in {mapped_role}")
        normalized_items.append(
            entry.model_copy(
                update={
                    "item_name": canonical_name,
                    "mapped_from_component": mapped_role,
                }
            ).model_dump()
        )

    report = {
        "status": "OK" if not mismatches else "REMAPPED",
        "actions": [],
        "details": {
            "mismatches": mismatches,
            "low_confidence": low_confidence,
        },
    }
    if mismatches:
        report["actions"].append("MAP_SYNONYM")
    if low_confidence:
        report["actions"].append("FLAG_LOW_CONFIDENCE")

    if mismatches and len(mismatches) >= len(schema.items):
        raise GuardrailViolation(
            "StageC",
            "All itemized entries mismatched components",
            payload=report,
        )

    normalized = schema.model_copy(update={"items": normalized_items}).model_dump()
    normalized.setdefault("reconciliation", report)
    return normalized, report


@dataclass
class StageMetrics:
    durations_ms: Dict[str, int]

    def __init__(self) -> None:
        self.durations_ms = {}

    def add(self, stage: str, duration_ms: int) -> None:
        self.durations_ms[stage] = duration_ms

    def to_dict(self) -> Dict[str, int]:
        return dict(self.durations_ms)


@contextmanager
def stage_timer(stage: str, metrics: StageMetrics | None = None):
    start = time.perf_counter()
    yield
    duration_ms = int((time.perf_counter() - start) * 1000)
    if metrics is not None:
        metrics.add(stage, duration_ms)


DEFAULT_PORTION_TABLE = {
    "cheeseburger": 150.0,
    "fried chicken sandwich": 180.0,
    "chicken nuggets": 17.0,
    "french fries": 110.0,
    "soft drink": 240.0,
    "barbecue sauce": 28.0,
    "ranch sauce": 28.0,
}

_OUNCE_TO_GRAMS = 29.57


def _infer_from_portion_detail(name: str, portion_detail: Optional[str]) -> Optional[float]:
    if not portion_detail:
        return None
    detail = portion_detail.lower()
    m = re.search(r"(\d+(?:\.\d+)?)\s*(?:pc|piece|pieces|ct)", detail)
    if m:
        count = float(m.group(1))
        per_piece = DEFAULT_PORTION_TABLE.get(name, 0)
        if per_piece:
            return count * per_piece
    m2 = re.search(r"(\d+(?:\.\d+)?)\s*fl\s*oz", detail)
    if m2:
        ounces = float(m2.group(1))
        return ounces * _OUNCE_TO_GRAMS
    return None


def estimate_portions(
    itemized: Dict[str, Any],
    metrics: Optional[StageMetrics] = None,
) -> Dict[str, float]:
    estimates: Dict[str, float] = {}
    items = itemized.get("items", []) or []
    with stage_timer("stage_D", metrics):
        for entry in items:
            name = entry.get("item_name")
            canonical_name = name.lower() if isinstance(name, str) else ""
            qty = entry.get("quantity") or 1
            detail = entry.get("portion_detail") or entry.get("description")
            grams = _infer_from_portion_detail(canonical_name, detail)
            if grams is None:
                grams = DEFAULT_PORTION_TABLE.get(canonical_name)
            if grams is not None:
                estimates[canonical_name] = grams * qty
    return estimates


def compute_totals(final_items: List[Dict[str, Any]]) -> Tuple[Dict[str, float], bool]:
    totals = {"calories": 0.0, "protein_g": 0.0, "carb_g": 0.0, "fat_g": 0.0}
    nutrition_violation = False
    for item in final_items:
        nutrition = item.get("nutrition") or {}
        cal = nutrition.get("calories")
        protein = nutrition.get("protein_g")
        carbs = nutrition.get("carb_g")
        fat = nutrition.get("fat_g")
        if isinstance(cal, (int, float)) and cal >= 0:
            totals["calories"] += cal
        if isinstance(protein, (int, float)) and protein >= 0:
            totals["protein_g"] += protein
        if isinstance(carbs, (int, float)) and carbs >= 0:
            totals["carb_g"] += carbs
        if isinstance(fat, (int, float)) and fat >= 0:
            totals["fat_g"] += fat
        if isinstance(cal, (int, float)):
            if cal <= 0 or cal > 3000:
                nutrition_violation = True
    return totals, nutrition_violation


def build_final_report(
    dish_json: Dict[str, Any],
    itemized: Dict[str, Any],
    macros: Dict[str, Any],
    reconciliation: Dict[str, Any],
) -> Dict[str, Any]:
    source = dish_json.get("source") or "UNKNOWN"
    restaurant_category = dish_json.get("restaurant_type")
    primary_dish = canonicalize_label(dish_json.get("dish_name") or "")

    results = macros.get("results") or macros.get("items") or []
    final_items: List[Dict[str, Any]] = []
    label_mismatch = bool(reconciliation.get("details", {}).get("mismatches"))
    low_confidence = bool(reconciliation.get("details", {}).get("low_confidence"))

    for idx, entry in enumerate(results):
        macros_payload = entry.get("macros") or entry.get("nutrition") or {}
        nutrition = NutritionBreakdown.model_validate(macros_payload).model_dump()
        final_items.append(
            {
                "canonical_name": canonicalize_label(
                    entry.get("item_name") or f"item_{idx}"
                ),
                "brand": entry.get("nutritionix_match", {}).get("brand_name")
                or macros_payload.get("brand_name")
                or itemized.get("restaurant_name"),
                "portion": {
                    "grams": macros_payload.get("serving_weight_grams"),
                    "household": entry.get("description") or entry.get("portion_detail"),
                },
                "confidence": float(entry.get("confidence") or 0.6),
                "nutrition": nutrition,
            }
        )

    totals, nutrition_violation = compute_totals(final_items)
    flags = StageFlags(
        label_mismatch=label_mismatch,
        nutrition_range_violation=nutrition_violation,
        low_confidence=low_confidence,
    )

    audit = {
        "stage_flags": flags.model_dump(),
        "notes": _build_audit_notes(final_items, reconciliation),
        "reconciliation": reconciliation,
    }

    payload = FinalMealReport(
        source=source,
        restaurant_category=restaurant_category,
        primary_dish=primary_dish,
        items=[FinalItem(**item) for item in final_items],
        totals=Totals(**totals),
        audit=audit,
    ).model_dump()
    payload["items"] = final_items
    payload["totals"] = totals
    payload["audit"] = audit
    return payload


def _build_audit_notes(items: List[Dict[str, Any]], reconciliation: Dict[str, Any]) -> str:
    parts: List[str] = []
    if reconciliation.get("details", {}).get("mismatches"):
        parts.append("Reconciled item synonyms across stages.")
    if reconciliation.get("details", {}).get("low_confidence"):
        parts.append("One or more items below confidence floor; review portions.")
    if not parts:
        parts.append("All stages aligned with canonical labels.")
    return " ".join(parts)
