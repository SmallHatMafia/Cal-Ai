from __future__ import annotations

import json
import time
import os
from typing import Any, Dict, Optional

from dotenv import load_dotenv, find_dotenv
from pathlib import Path

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore

from .prompts import get_dish_determiner_prompt
from .visual_context import ImageStore
from .visual_context import _image_to_data_url  # reuse util


def _extract_packaging_cues(visual_json: Dict[str, Any]) -> list[str]:
    try:
        ctx = visual_json.get("context") or {}
        cues = ctx.get("packaging_cues") or []
        return [str(c) for c in cues if isinstance(c, str)]
    except Exception:
        return []


def _drink_size_from_cues(cues: list[str]) -> str:
    joined = " | ".join(cues).lower()
    if "xl lid" in joined:
        return "XL"
    if "l lid" in joined:
        return "L"
    if "m lid" in joined:
        return "M"
    if "s lid" in joined:
        return "S"
    return "UNKNOWN"


def _drink_ounces_from_cues(cues: list[str]) -> Optional[str]:
    import re
    for c in cues:
        m = re.search(r"\b(\d{1,3})\s*fl\s*oz\b", c.lower())
        if m:
            return f"{m.group(1)} fl oz"
    return None


def _has_branded_cup(cues: list[str]) -> bool:
    j = " | ".join(cues).lower()
    return any(k in j for k in ["logo cup", "coca-cola", "sprite", "cup" ] )


 


def _ensure_env_loaded() -> None:
    if os.getenv("OPENAI_API_KEY"):
        return
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / ".env",
        here.parent.parent.parent / ".env",
        Path.cwd() / ".env",
        Path.home() / ".env",
    ]
    loaded = False
    for p in candidates:
        try:
            if p and p.exists():
                load_dotenv(dotenv_path=str(p), override=False)
                loaded = True
        except Exception:
            pass
    if not loaded:
        dotenv_path = find_dotenv(usecwd=True)
        if dotenv_path:
            load_dotenv(dotenv_path=dotenv_path, override=False)


_CLIENT: Optional[OpenAI] = None


def _get_openai_client() -> OpenAI:
    _ensure_env_loaded()
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set in environment/.env")
    if OpenAI is None:
        raise RuntimeError("openai package is not installed. Please install it in the backend venv.")
    global _CLIENT
    if _CLIENT is None:
        _CLIENT = OpenAI(api_key=api_key)
    return _CLIENT


def determine_dishes_from_visual_json(visual_json: Dict[str, Any]) -> Dict[str, Any]:
    client = _get_openai_client()
    system_prompt = get_dish_determiner_prompt()

    def _salvage_json(text: str) -> Dict[str, Any]:
        t = text.strip()
        # Remove common code fences
        t = t.replace("```json", "").replace("```", "").strip()
        # Extract first {...} block
        start_i = t.find("{")
        end_i = t.rfind("}")
        if start_i != -1 and end_i != -1 and end_i > start_i:
            return json.loads(t[start_i:end_i+1])
        return json.loads(t)

    start = time.perf_counter()
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_TEXT_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": json.dumps(visual_json, ensure_ascii=False)},
                ],
            },
        ],
        temperature=float(os.getenv("STEP2_TEMPERATURE", "0.3")),
        top_p=float(os.getenv("STEP2_TOP_P", "0.9")),
        max_tokens=400,
        response_format={"type": "json_object"},
    )
    duration_ms = int((time.perf_counter() - start) * 1000)

    content = response.choices[0].message.content if response.choices else None
    if not content:
        raise RuntimeError("No content returned from OpenAI for dish determination.")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = _salvage_json(content)
    data["_duration_ms"] = duration_ms
    # carry restaurant name into downstream when available
    if "restaurant_name" in data and data.get("restaurant_name"):
        data["restaurant_name"] = data["restaurant_name"].strip()
    return data


def determine_dishes_from_visual_json_and_image(visual_json: Dict[str, Any], image_token: Optional[str]) -> Dict[str, Any]:
    client = _get_openai_client()
    system_prompt = get_dish_determiner_prompt()

    # Prepare message content with both text (JSON) and the image (if available).
    # Always include image when a token is provided, using precomputed data URL to avoid re-encoding.
    content_parts = [
        {"type": "text", "text": json.dumps(visual_json, ensure_ascii=False)}
    ]
    if image_token:
        data_url = ImageStore.get_data_url(image_token)
        if not data_url:
            cached = ImageStore.get(image_token)
            if cached:
                image_bytes, mime = cached
                data_url = _image_to_data_url(image_bytes, mime)
        if data_url:
            content_parts.append({"type": "image_url", "image_url": {"url": data_url}})

    start = time.perf_counter()
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content_parts},
        ],
        temperature=float(os.getenv("STEP2_TEMPERATURE", "0.3")),
        top_p=float(os.getenv("STEP2_TOP_P", "0.9")),
        max_tokens=400,
        response_format={"type": "json_object"},
    )
    duration_ms = int((time.perf_counter() - start) * 1000)

    content = response.choices[0].message.content if response.choices else None
    if not content:
        raise RuntimeError("No content returned from OpenAI for dish determination with image.")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Reuse the same salvage logic from the text-only case
        def _salvage_json(text: str) -> Dict[str, Any]:
            t = text.strip()
            t = t.replace("```json", "").replace("```", "").strip()
            start_i = t.find("{")
            end_i = t.rfind("}")
            if start_i != -1 and end_i != -1 and end_i > start_i:
                return json.loads(t[start_i:end_i+1])
            return json.loads(t)
        data = _salvage_json(content)
    data["_duration_ms"] = duration_ms

    # Post-process: drink recall from Step 1 packaging cues if drinks[] missing
    try:
        comps = data.get("components") or {}
        drinks = comps.get("drinks") or []
        cues = _extract_packaging_cues(visual_json)
        if not drinks and cues and _has_branded_cup(cues):
            size_hint = _drink_size_from_cues(cues)
            vol = _drink_ounces_from_cues(cues)
            drinks.append({
                "name": "soft drink",
                "size_hint": size_hint,
                "volume_estimate": vol or ""
            })
            comps["drinks"] = drinks
            data["components"] = comps
    except Exception:
        # Non-fatal
        pass
    return data

