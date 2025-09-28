"""Pipeline utilities for calorie estimation stages."""

from .schemas import (
    VisualContextSchema,
    DishDeterminationSchema,
    ItemizedMealSchema,
    NutritionLookupResult,
    FinalMealReport,
)

from .core import (
    GuardrailViolation,
    StageMetrics,
    build_final_report,
    canonicalize_component_role,
    canonicalize_label,
    estimate_portions,
    normalize_dish_determination,
    normalize_visual_context,
    reconcile_stage_outputs,
    stage_timer,
)

__all__ = [
    "VisualContextSchema",
    "DishDeterminationSchema",
    "ItemizedMealSchema",
    "NutritionLookupResult",
    "FinalMealReport",
    "GuardrailViolation",
    "StageMetrics",
    "build_final_report",
    "canonicalize_component_role",
    "canonicalize_label",
    "estimate_portions",
    "normalize_dish_determination",
    "normalize_visual_context",
    "reconcile_stage_outputs",
    "stage_timer",
]
