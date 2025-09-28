"""Typed schemas for each pipeline stage.

These Pydantic models provide structured validation while allowing
extra fields so that upstream prompts can evolve without breaking the
backend. Each schema exposes `model_dump` helpers to convert back to
plain dictionaries for FastAPI responses.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, ConfigDict, validator


class BaseSchema(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump(mode="json")


class VisualDetection(BaseSchema):
    name: str
    bbox: Optional[List[float]] = None
    mask: Optional[Any] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    estimated_quantity: Optional[str] = None
    size_hint: Optional[str] = None
    physical_description: Optional[str] = None


class CalibrationPayload(BaseSchema):
    plate_diameter_px: Optional[float] = Field(default=None, ge=0)
    utensil_present: Optional[bool] = None
    reference_objects: Optional[List[str]] = None


class VisualContextSchema(BaseSchema):
    caption: Optional[str] = None
    detections: List[VisualDetection] = Field(default_factory=list)
    calibration: CalibrationPayload = Field(default_factory=CalibrationPayload)
    context: Optional[Dict[str, Any]] = None
    _image_token: Optional[str] = Field(default=None, alias="image_token")

    @validator("detections", pre=True, each_item=False)
    def _coerce_detections(cls, value: Any) -> List[Any]:  # type: ignore[override]
        if value is None:
            return []
        if isinstance(value, list):
            return value
        return []


class ComponentEntry(BaseSchema):
    name: str
    size_hint: Optional[str] = None
    volume_estimate: Optional[str] = None
    notes: Optional[str] = None


class DishComponents(BaseSchema):
    main: List[ComponentEntry] = Field(default_factory=list)
    sides: List[ComponentEntry] = Field(default_factory=list)
    drinks: List[ComponentEntry] = Field(default_factory=list)
    extras: List[ComponentEntry] = Field(default_factory=list)


class DishDeterminationSchema(BaseSchema):
    source: str = Field(pattern=r"^(RESTAURANT|HOME|HOME_COOKED|HOMECOOKED)$")
    restaurant_type: Optional[str] = None
    restaurant_name: Optional[str] = None
    dish_name: Optional[str] = None
    components: DishComponents = Field(default_factory=DishComponents)
    _image_token: Optional[str] = Field(default=None, alias="image_token")


class ItemizedEntry(BaseSchema):
    item_name: str
    quantity: int = Field(default=1, ge=1)
    size: Optional[str] = None
    portion_detail: Optional[str] = None
    description: Optional[str] = None
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)
    mapped_from_component: Optional[str] = Field(default=None, alias="component")
    nutritionix_query: Optional[str] = None
    required_keywords: Optional[List[str]] = None
    forbidden_keywords: Optional[List[str]] = None
    category: Optional[str] = None


class ItemizedMealSchema(BaseSchema):
    restaurant_name: Optional[str] = None
    nl_query: Optional[str] = None
    items: List[ItemizedEntry] = Field(default_factory=list)
    validation: Optional[Dict[str, Any]] = None


class NutritionBreakdown(BaseSchema):
    calories: Optional[float] = None
    protein_g: Optional[float] = Field(default=None, alias="protein")
    carb_g: Optional[float] = Field(default=None, alias="carbs")
    fat_g: Optional[float] = Field(default=None, alias="fat")
    serving_weight_grams: Optional[float] = None
    serving_qty: Optional[float] = None
    serving_unit: Optional[str] = None
    brand_name: Optional[str] = None
    food_name: Optional[str] = None


class NutritionLookupResult(BaseSchema):
    restaurant_name: Optional[str] = None
    items: List[Dict[str, Any]] = Field(default_factory=list)
    _duration_ms: Optional[int] = None


class FinalItem(BaseSchema):
    canonical_name: str
    brand: Optional[str]
    portion: Dict[str, Any]
    confidence: float
    nutrition: NutritionBreakdown


class StageFlags(BaseSchema):
    label_mismatch: bool = False
    nutrition_range_violation: bool = False
    low_confidence: bool = False


class Totals(BaseSchema):
    calories: float = 0.0
    protein_g: float = 0.0
    carb_g: float = 0.0
    fat_g: float = 0.0


class FinalMealReport(BaseSchema):
    source: str
    restaurant_category: Optional[str]
    primary_dish: Optional[str]
    items: List[FinalItem]
    totals: Totals
    audit: Dict[str, Any]


def ensure_list(obj: Optional[Any]) -> List[Any]:
    if obj is None:
        return []
    if isinstance(obj, list):
        return obj
    return [obj]
