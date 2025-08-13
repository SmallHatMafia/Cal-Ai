from __future__ import annotations

import base64
import json
import re
import time
import os
from typing import Any, Dict, Optional, Tuple

from dotenv import load_dotenv, find_dotenv
from pathlib import Path

# OpenAI SDK v1+ (compatible with multimodal responses)
try:
    from openai import OpenAI
except Exception as exc:  # pragma: no cover - if not installed yet
    OpenAI = None  # type: ignore

from .prompts import get_visual_context_prompt

# Optional image processing for faster uploads/inference
try:  # pragma: no cover - optional perf enhancement
    from PIL import Image
    from io import BytesIO
except Exception:  # Pillow not installed
    Image = None  # type: ignore
    BytesIO = None  # type: ignore


def _ensure_env_loaded() -> None:
    # If already set in OS env, respect it
    if os.getenv("OPENAI_API_KEY"):
        return
    here = Path(__file__).resolve()
    candidates = [
        here.parent.parent / ".env",            # backend_server/.env
        here.parent.parent.parent / ".env",      # backend/.env
        Path.cwd() / ".env",                     # CWD .env
        Path.home() / ".env",                    # user global .env
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
        # Final fallback: walk up from CWD
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


def _maybe_downscale_and_reencode(image_bytes: bytes, mime_type: str) -> Tuple[bytes, str]:
    """Reduce image size to speed up network+LLM processing.

    - Downscale to max 896px on the longest side
    - Re-encode to JPEG quality=80 for most formats
    Fallback to original if Pillow is unavailable or an error occurs.
    """
    if Image is None or BytesIO is None:
        return image_bytes, mime_type
    try:
        with Image.open(BytesIO(image_bytes)) as img:
            img = img.convert("RGB")
            w, h = img.size
            max_dim = 896
            if max(w, h) > max_dim:
                scale = max_dim / float(max(w, h))
                new_size = (int(w * scale), int(h * scale))
                img = img.resize(new_size)
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=80, optimize=True)
            return buf.getvalue(), "image/jpeg"
    except Exception:
        return image_bytes, mime_type


def _image_to_data_url(image_bytes: bytes, mime_type: str) -> str:
    optimized_bytes, optimized_mime = _maybe_downscale_and_reencode(image_bytes, mime_type)
    b64 = base64.b64encode(optimized_bytes).decode("ascii")
    return f"data:{optimized_mime};base64,{b64}"


def analyze_visual_context_from_bytes(image_bytes: bytes, mime_type: str = "image/jpeg") -> Dict[str, Any]:
    """
    Calls OpenAI Vision model with the Visual Context prompt and an image.

    Returns a Python dict parsed from the strict-JSON response.
    """
    client = _get_openai_client()

    system_prompt = get_visual_context_prompt()
    image_data_url = _image_to_data_url(image_bytes, mime_type)

    start = time.perf_counter()
    response = client.chat.completions.create(
        model=os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini"),
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze the image and follow the above rules strictly."},
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            },
        ],
        temperature=float(os.getenv("STEP1_TEMPERATURE", "0.2")),
        top_p=float(os.getenv("STEP1_TOP_P", "0.9")),
        max_tokens=500,
        response_format={"type": "json_object"},
    )
    duration_ms = int((time.perf_counter() - start) * 1000)

    content: Optional[str] = None
    if response and response.choices:
        content = response.choices[0].message.content
    if not content:
        raise RuntimeError("No content returned from OpenAI for visual context analysis.")

    def _salvage_json(text: str) -> Dict[str, Any]:
        # Remove common non-JSON wrappers
        t = text.strip()
        t = re.sub(r"^```(json)?", "", t, flags=re.IGNORECASE).strip()
        t = re.sub(r"```$", "", t).strip()
        # Extract first {...} block
        start = t.find("{")
        end = t.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidate = t[start : end + 1]
            return json.loads(candidate)
        # Fallback: raise to outer
        return json.loads(t)

    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        data = _salvage_json(content)
    # Attach duration meta for downstream visibility
    data["_duration_ms"] = duration_ms
    return data


 

def analyze_visual_context_from_file(image_path: str) -> Dict[str, Any]:
    with open(image_path, "rb") as f:
        image_bytes = f.read()
    # basic mime inference
    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/jpeg"
    if ext in {".png"}:
        mime = "image/png"
    elif ext in {".webp"}:
        mime = "image/webp"
    elif ext in {".gif"}:
        mime = "image/gif"
    return analyze_visual_context_from_bytes(image_bytes, mime)


def _store_image_in_memory(image_bytes: bytes, mime_type: str) -> str:
    """Store image (pre-optimized) in an in-memory cache and return an opaque token."""
    token = base64.urlsafe_b64encode(os.urandom(12)).decode("ascii").rstrip("=")
    ImageStore.set(token, image_bytes, mime_type)
    return token


def run_visual_context_and_forward(image_bytes: bytes, mime_type: str = "image/jpeg") -> Dict[str, Any]:
    """
    Entry used by pipeline: analyze image, then forward to next bot (Dish Determiner).
    Returns the Visual Context JSON for convenience; the next stage can be invoked separately.
    """
    visual_json = analyze_visual_context_from_bytes(image_bytes, mime_type)

    # Store the image and attach token for downstream stages
    image_token = _store_image_in_memory(image_bytes, mime_type)
    visual_json["_image_token"] = image_token

    return visual_json

class ImageStore:
    _cache: Dict[str, Dict[str, Any]] = {}

    @classmethod
    def set(cls, token: str, image_bytes: bytes, mime_type: str) -> None:
        # Optimize once and cache both the optimized payload and its data URL
        optimized_bytes, optimized_mime = _maybe_downscale_and_reencode(image_bytes, mime_type)
        b64 = base64.b64encode(optimized_bytes).decode("ascii")
        data_url = f"data:{optimized_mime};base64,{b64}"
        cls._cache[token] = {
            "bytes": optimized_bytes,
            "mime": optimized_mime,
            "data_url": data_url,
        }

    @classmethod
    def get(cls, token: str) -> Optional[Tuple[bytes, str]]:
        entry = cls._cache.get(token)
        if not entry:
            return None
        return entry.get("bytes"), entry.get("mime")  # type: ignore[return-value]

    @classmethod
    def get_data_url(cls, token: str) -> Optional[str]:
        entry = cls._cache.get(token)
        if not entry:
            return None
        return entry.get("data_url")

    @classmethod
    def pop(cls, token: str) -> Optional[Tuple[bytes, str]]:
        entry = cls._cache.pop(token, None)
        if not entry:
            return None
        return entry.get("bytes"), entry.get("mime")  # type: ignore[return-value]



