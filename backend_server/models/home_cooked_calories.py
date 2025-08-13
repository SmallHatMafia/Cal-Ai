from __future__ import annotations

import base64
import json
import os
import time
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv, find_dotenv
from pathlib import Path

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore

from .visual_context import ImageStore, _image_to_data_url
from .prompts import get_home_cooked_prompt


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


def analyze_home_cooked_from_context_and_image(dish_determiner_context: Dict[str, Any], image_token: Optional[str]) -> Dict[str, Any]:
    """Runs the Home-Cooked analyzer LLM using the image (if available) and dish-determiner context text.

    dish_determiner_context is the full JSON from Step 2; we will send a concise text summary plus the JSON for reference.
    """
    client = _get_openai_client()
    system_prompt = get_home_cooked_prompt()

    # Prepare content with EXACT dish determiner JSON, and the image if token available
    parts: list[Dict[str, Any]] = [
        {"type": "text", "text": json.dumps({
            "dish_determiner_json": dish_determiner_context,
        }, ensure_ascii=False)}
    ]

    if image_token:
        data_url = ImageStore.get_data_url(image_token)
        if not data_url:
            cached = ImageStore.get(image_token)
            if cached:
                image_bytes, mime = cached
                data_url = _image_to_data_url(image_bytes, mime)
        if data_url:
            parts.append({"type": "image_url", "image_url": {"url": data_url}})

    start = time.perf_counter()
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": parts},
        ],
        temperature=float(os.getenv("STEP2_TEMPERATURE", "0.3")),
        top_p=float(os.getenv("STEP2_TOP_P", "0.9")),
        max_tokens=700,
        response_format={"type": "json_object"},
    )

    content = response.choices[0].message.content if response.choices else None
    if not content:
        raise RuntimeError("No content returned from OpenAI for home-cooked analysis.")
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Strip code fences and extract the first JSON object
        t = (content or "").strip().replace("```json", "").replace("```", "").strip()
        start_i = t.find("{")
        end_i = t.rfind("}")
        data = json.loads(t[start_i:end_i+1] if (start_i != -1 and end_i != -1 and end_i > start_i) else t)
    # Attach timing metadata like other bots
    data["_duration_ms"] = int((time.perf_counter() - start) * 1000)
    return data


