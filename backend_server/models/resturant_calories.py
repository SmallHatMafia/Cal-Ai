from __future__ import annotations

import json
import time
import os
from typing import Any, Dict, List, Optional, Tuple
import re

import requests
from dotenv import load_dotenv, find_dotenv
from pathlib import Path
import concurrent.futures
import logging

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore

# Defer importing prompts until runtime to avoid import-time failures
from .visual_context import ImageStore, _image_to_data_url

# Nutritionix search tuning to minimize stalls while enforcing brand correctness
_NUTRITIONIX_REQ_TIMEOUT_S: int = 6
_NUTRITIONIX_MAX_SEEDS: int = 8
_NUTRITIONIX_ITEM_BUDGET_S: float = 6.0
_GOOD_SCORE_THRESHOLD: int = 7


def _ensure_env_loaded() -> None:
    if os.getenv("OPENAI_API_KEY") and os.getenv("NUTRITIONIX_APP_ID"):
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


def _nutritionix_headers() -> Dict[str, str]:
    _ensure_env_loaded()
    app_id = os.getenv("NUTRITIONIX_APP_ID")
    api_key = os.getenv("NUTRITIONIX_API_KEY")
    if not app_id or not api_key:
        raise RuntimeError("NUTRITIONIX_APP_ID and NUTRITIONIX_API_KEY must be set in .env")
    return {
        "x-app-id": app_id,
        "x-app-key": api_key,
        "Content-Type": "application/json",
    }


_NUTRITIONIX_SESSION: Optional[requests.Session] = None


def _get_nutritionix_session() -> requests.Session:
    global _NUTRITIONIX_SESSION
    if _NUTRITIONIX_SESSION is None:
        s = requests.Session()
        s.headers.update(_nutritionix_headers())
        _NUTRITIONIX_SESSION = s
    return _NUTRITIONIX_SESSION


# Persistent, deterministic SKU catalog for exact calories
 


def _norm_raw(s: Optional[str]) -> str:
    if not s:
        return ""
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _norm_brand(s: Optional[str]) -> str:
    n = _norm_raw(s)
    aliases = {
        # Fast food common aliases
        "mcdonald": "mcdonalds",
        "mcdonalds": "mcdonalds",
        "chickfila": "chickfila",
        "chickfilamenu": "chickfila",
        "chickfilachicken": "chickfila",
        "wendys": "wendys",
        "wendy": "wendys",
        "dominos": "dominos",
        "domino": "dominos",
        "papajohns": "papajohns",
        "papajohn": "papajohns",
        "burgerking": "burgerking",
        "bk": "burgerking",
        "kfc": "kfc",
        "kentuckyfriedchicken": "kfc",
        "arbys": "arbys",
        "arby": "arbys",
        "tacobell": "tacobell",
        "pandaexpress": "pandaexpress",
        "popeyes": "popeyes",
        "popeyeslouisianakitchen": "popeyes",
        "subway": "subway",
        "jackinthebox": "jackinthebox",
        "jackbox": "jackinthebox",
        "chipotle": "chipotle",
        "chipotlemexicangrill": "chipotle",
        # Add more as needed
    }
    return aliases.get(n, n)


def _has_branded_cup_in_visual(visual_json: Dict[str, Any]) -> bool:
    try:
        ctx = visual_json.get("context") or {}
        cues = ctx.get("packaging_cues") or []
        joined = " | ".join([str(c) for c in cues]) .lower()
        return any(k in joined for k in ["logo cup", "coca-cola", "sprite", "cup"]) and any(k in joined for k in ["lid", "fl oz"]) or ("logo cup" in joined)
    except Exception:
        return False


def _component_counts_from_dish(dish_json: Dict[str, Any]) -> Dict[str, int]:
    comps = (dish_json or {}).get("components") or {}
    return {
        "main": len(comps.get("main") or []),
        "sides": len(comps.get("sides") or []),
        "drinks": len(comps.get("drinks") or []),
        "extras": len(comps.get("extras") or []),
    }


def _is_generic_brand(brand_name: Optional[str]) -> bool:
    n = _norm_raw(brand_name)
    return n in {"", "home", "casualdining", "upscalerestaurant", "restaurant"}


def _generate_item_queries(brand_name: Optional[str], item_name: str, description: Optional[str]) -> List[str]:
    base_name = item_name or ""
    desc = description or ""
    candidates = []

    def add(q: str) -> None:
        q = q.strip()
        if q and q not in candidates:
            candidates.append(q)

    # Primary
    add(f"{brand_name or ''} {base_name} {desc}")
    add(f"{brand_name or ''} {base_name}")
    add(base_name)

    # Parse hints from the text for count/size/ounces
    try:
        hints = _parse_expected_from_item(item_name, description, None)
    except Exception:
        hints = {"count": None, "size": None, "ounces": None, "drink_type": None}

    # Simple synonyms and qualified variants
    name_l = base_name.lower()
    if "fries" in name_l or "french" in name_l:
        add(f"{brand_name or ''} fries {desc}")
        add(f"{brand_name or ''} french fries")
        if hints.get("size"):
            add(f"{brand_name or ''} fries {hints['size']}")
    if "soda" in name_l or "cola" in name_l or "coke" in name_l:
        add(f"{brand_name or ''} coca cola {desc}")
        add(f"{brand_name or ''} coke {desc}")
        add(f"{brand_name or ''} soft drink {desc}")
        if hints.get("ounces"):
            add(f"{brand_name or ''} coca cola {hints['ounces']} fl oz")
        if hints.get("size"):
            add(f"{brand_name or ''} soft drink {hints['size']}")
    if "chicken" in name_l and "sandwich" in name_l:
        add(f"{brand_name or ''} crispy chicken sandwich")
        add(f"{brand_name or ''} chicken sandwich {desc}")
    if "nugget" in name_l:
        if hints.get("count"):
            add(f"{brand_name or ''} chicken nuggets {hints['count']} piece")
            add(f"{brand_name or ''} nuggets {hints['count']} pc")
    # Casual dining helpers
    if any(k in name_l for k in ["steak", "sirloin", "ribeye", "filet"]):
        add(f"{brand_name or ''} steak")
        add(f"grilled steak {desc}")
    if any(k in name_l for k in ["shrimp", "prawn"]):
        add(f"{brand_name or ''} grilled shrimp")
        add(f"shrimp {desc}")
    if "baked potato" in name_l or ("potato" in name_l and "baked" in name_l):
        add(f"baked potato")
        add(f"{brand_name or ''} baked potato {desc}")
    if any(k in name_l for k in ["sauce", "dipping", "dip"]):
        add(f"dipping sauce")
        add(f"sauce {desc}")
    return [q for q in candidates if q]


def _derive_expectations(item_name: str, category: Optional[str], required: Optional[List[str]], forbidden: Optional[List[str]]) -> Dict[str, List[str]]:
    name = (_norm_raw(item_name) or "").lower()
    include: List[str] = []
    exclude: List[str] = ["shake", "mcflurry", "smoothie", "nugget dipping"]

    def inc(*words: str) -> None:
        for w in words:
            if w not in include:
                include.append(w)

    def exc(*words: str) -> None:
        for w in words:
            if w not in exclude:
                exclude.append(w)

    if category == "entree" or "sandwich" in name:
        inc("sandwich", "mcchicken", "chicken sandwich", "crispy chicken")
        exc("nugget", "tender", "wrap")
    if category == "entree" or "burger" in name or "cheeseburger" in name:
        inc("burger", "cheeseburger", "big mac", "quarter pounder")
        exc("nugget", "wrap")
    if "nugget" in name:
        inc("nugget")
        exc("sandwich", "burger")
    if category == "side" or "fries" in name or "french" in name:
        inc("fries")
        exc("hash brown", "onion ring")
    if category == "drink" or any(k in name for k in ["soda", "cola", "coke", "sprite", "drink"]):
        inc("coke", "cola", "soft drink", "sprite", "dr pepper", "fanta")
        # Strongly exclude coffee for soft drink category to prevent iced coffee mismatches
        exc("shake", "smoothie", "coffee", "iced coffee", "latte", "mccafe")

    # Always exclude dessert words for savory items
    if any(k in name for k in ["sandwich", "burger", "fries", "nugget"]):
        exc("shake", "mcflurry", "sundae")

    # Sauces: prefer packet; avoid grocery/dressing matches
    if category == "sauce" or "sauce" in name or "packet" in name:
        inc("sauce", "packet")
        exc("dressing", "bottle", "grocery")

    if required:
        for k in required:
            inc(k.lower())
    if forbidden:
        for k in forbidden:
            exc(k.lower())
    return {"include": include, "exclude": exclude}


def _parse_expected_from_item(item_name: Optional[str], description: Optional[str], category: Optional[str]) -> Dict[str, Optional[str]]:
    """Extract expected qualifiers (count, size, ounces, drink_type) from item_name/description.
    Returns keys: count(str digits), size(one of Kids/Small/Medium/Large), ounces(str digits), drink_type(soft_drink|coffee|None).
    """
    text = f"{item_name or ''} {description or ''}".lower()
    expected: Dict[str, Optional[str]] = {"count": None, "size": None, "ounces": None, "drink_type": None}
    # count patterns: "(6 pc)", "6 piece", "10 pc", "8 ct"
    m = re.search(r"\b(\d{1,3})\s*(?:pc|piece|pieces|ct)\b", text)
    if not m:
        m = re.search(r"\((\d{1,3})\s*pc\)", text)
    if m:
        expected["count"] = m.group(1)
    # size words
    m2 = re.search(r"\b(kids|small|medium|large)\b", text)
    if m2:
        expected["size"] = m2.group(1).title()
    # ounces
    m3 = re.search(r"\b(\d{1,3})\s*fl\s*oz\b", text)
    if m3:
        expected["ounces"] = m3.group(1)
    # drink type
    if (category == "drink") or ("drink" in text or "cola" in text or "sprite" in text or "soda" in text):
        expected["drink_type"] = "soft_drink"
    if any(w in text for w in ["coffee", "iced coffee", "latte", "mccafe"]):
        expected["drink_type"] = "coffee"
    return expected


def _parse_candidate_modifiers(food_name: str) -> Dict[str, Optional[str]]:
    """Extract qualifiers from a Nutritionix candidate's food_name."""
    t = (food_name or "").lower()
    mods: Dict[str, Optional[str]] = {"count": None, "size": None, "ounces": None, "drink_type": None}
    m = re.search(r"\b(\d{1,3})\s*(?:pc|piece|pieces|ct)\b", t)
    if m:
        mods["count"] = m.group(1)
    m2 = re.search(r"\b(kids|small|medium|large)\b", t)
    if m2:
        mods["size"] = m2.group(1).title()
    m3 = re.search(r"\b(\d{1,3})\s*fl\s*oz\b", t)
    if m3:
        mods["ounces"] = m3.group(1)
    if any(w in t for w in ["coffee", "iced coffee", "latte", "mccafe"]):
        mods["drink_type"] = "coffee"
    elif any(w in t for w in ["cola", "coke", "sprite", "soft drink", "soda"]):
        mods["drink_type"] = "soft_drink"
    return mods


def _score_candidate(item_name: str, description: Optional[str], category: Optional[str], required: Optional[List[str]], forbidden: Optional[List[str]], cand: Dict[str, Any]) -> int:
    food = (cand.get("food_name") or "").lower()
    exp = _derive_expectations(item_name, category, required, forbidden)
    score = 0
    # include keywords
    for kw in exp["include"]:
        if kw and kw in food:
            score += 3
    # exclude penalties
    for kw in exp["exclude"]:
        if kw and kw in food:
            score -= 5
    # token overlap
    tokens_item = set(re.findall(r"[a-z0-9]+", _norm_raw(item_name)))
    tokens_food = set(re.findall(r"[a-z0-9]+", _norm_raw(food)))
    score += len(tokens_item & tokens_food)
    # description assist
    if description:
        desc_l = description.lower()
        for kw in exp["include"]:
            if kw and kw in desc_l and kw in food:
                score += 1
    # Qualifier alignment (count/size/ounces, drink type)
    try:
        expected = _parse_expected_from_item(item_name, description, category)
        mods = _parse_candidate_modifiers(cand.get("food_name") or "")
        # penalize mismatches
        if expected.get("count") and mods.get("count") and expected["count"] != mods["count"]:
            score -= 6
        if expected.get("size") and mods.get("size") and expected["size"] != mods["size"]:
            score -= 4
        if expected.get("ounces") and mods.get("ounces") and expected["ounces"] != mods["ounces"]:
            score -= 3
        # drink type strictness: if soft drink expected but candidate is coffee, heavy penalty
        if expected.get("drink_type") == "soft_drink" and mods.get("drink_type") == "coffee":
            score -= 12
    except Exception:
        pass
    # Burger-specific mismatch penalties to avoid wrong brand items (e.g., selecting Double for single Cheeseburger)
    try:
        item_l = (item_name or "").lower()
        if "cheeseburger" in item_l and not ("double cheeseburger" in item_l or "mcdouble" in item_l):
            if any(k in food for k in ["double", "quarter", "big mac", "double quarter", "quarter pounder"]):
                score -= 14
        if ("mcdouble" in item_l or "double cheeseburger" in item_l) and ("double" not in food and "mcdouble" not in food):
            score -= 10
        if "whopper" in item_l and "whopper" not in food:
            score -= 12
    except Exception:
        pass
    return score


def _nutritionix_search_item(brand_name: Optional[str], item_name: str, description: Optional[str], category: Optional[str], required: Optional[List[str]], forbidden: Optional[List[str]], nutritionix_query: Optional[str]) -> Optional[Dict[str, Any]]:
    """Single-pass, deterministic-first lookup.
    1) Try Instant with nutritionix_query (preferred). 2) If no good match, try Instant with compact seeds once. 3) If still no match, return None.
    """
    # 1) Deterministic query if present
    if nutritionix_query:
        best = _instant_search_best(brand_name, item_name, description, category, required, forbidden, nutritionix_query)
        if best is not None:
            return best
    # 2) Compact seeds once
    seeds = _generate_item_queries(brand_name, item_name, description)
    generic_brand = _is_generic_brand(brand_name)
    start_search = time.perf_counter()
    best_overall: Optional[Dict[str, Any]] = None
    best_score_overall = -10**9
    for query in seeds[:_NUTRITIONIX_MAX_SEEDS]:
        if (time.perf_counter() - start_search) > _NUTRITIONIX_ITEM_BUDGET_S:
            break
        candidate = _instant_search_best(brand_name, item_name, description, category, required, forbidden, query)
        if candidate is None:
            continue
        sc = _score_candidate(item_name, description, category, required, forbidden, candidate)
        if sc > best_score_overall:
            best_score_overall = sc
            best_overall = candidate
        if best_score_overall >= _GOOD_SCORE_THRESHOLD:
            break
    return best_overall


def _nutritionix_nutrients_from_item(item: Dict[str, Any], require_branded: bool, brand_hint: Optional[str] = None) -> Optional[Dict[str, Any]]:
    # If item has nix_item_id (branded), use item endpoint, else use natural language endpoint
    if item.get("nix_item_id"):
        url = f"https://trackapi.nutritionix.com/v2/search/item?nix_item_id={item['nix_item_id']}"
        sess = _get_nutritionix_session()
        resp = sess.get(url, timeout=12)
        if resp.status_code != 200:
            return None
        full = resp.json().get("foods", [{}])[0]
        # If a brand is required, ensure the returned brand matches the hint
        if require_branded and brand_hint and full.get("brand_name"):
            if _norm_brand(full.get("brand_name")) != _norm_brand(brand_hint):
                return None
        # Cheeseburger calorie sanity: reject implausible serving sizes for standard cheeseburger
        try:
            food_l = (full.get("food_name") or "").lower()
            brand_l = _norm_brand(full.get("brand_name") or "")
            if brand_l == "mcdonalds" and "cheeseburger" in food_l and not any(k in food_l for k in ["double", "quarter", "big mac"]):
                cal = full.get("nf_calories")
                # Typical McDonald's Cheeseburger ~300 kcal; reject extreme outliers (>450 kcal)
                if cal is not None and cal > 450:
                    return None
        except Exception:
            pass
        return _extract_macro_fields(full)

    # Fallback: natural language (allow even when branded to avoid nulls, bias with brand)
    nl_url = "https://trackapi.nutritionix.com/v2/natural/nutrients"
    base_text = item.get("food_name") or item.get("food_name_input") or item.get("food_name_common") or item.get("food_name_raw") or item.get("food_name") or ""
    hint = None if _is_generic_brand(brand_hint) else brand_hint
    text = (f"{hint} {base_text}").strip() if hint else base_text
    if not text:
        return None
    sess = _get_nutritionix_session()
    resp = sess.post(nl_url, json={"query": text}, timeout=12)
    if resp.status_code != 200:
        return None
    full = resp.json().get("foods", [{}])[0]
    # If a brand hint is provided and the returned brand contradicts it, discard
    if hint and full.get("brand_name") and _norm_brand(full.get("brand_name")) != _norm_brand(hint):
        return None
    return _extract_macro_fields(full)


def _nutritionix_nl_from_name_desc(brand_name: Optional[str], item_name: Optional[str], description: Optional[str]) -> Optional[Dict[str, Any]]:
    if not item_name:
        return None
    nl_url = "https://trackapi.nutritionix.com/v2/natural/nutrients"
    hint = None if _is_generic_brand(brand_name) else brand_name
    text = f"{(hint or '').strip()} {item_name} {(description or '').strip()}".strip()
    sess = _get_nutritionix_session()
    resp = sess.post(nl_url, json={"query": text}, timeout=12)
    if resp.status_code != 200:
        return None
    full = resp.json().get("foods", [{}])[0]
    # If a brand is required and the returned brand contradicts the hint, discard
    if not _is_generic_brand(brand_name) and full.get("brand_name"):
        if _norm_brand(full.get("brand_name")) != _norm_brand(brand_name):
            return None
    return _extract_macro_fields(full)


def _extract_macro_fields(food: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "calories": food.get("nf_calories"),
        "protein_g": food.get("nf_protein"),
        "carbs_g": food.get("nf_total_carbohydrate"),
        "fat_g": food.get("nf_total_fat"),
        "serving_qty": food.get("serving_qty"),
        "serving_unit": food.get("serving_unit"),
        "serving_weight_grams": food.get("serving_weight_grams"),
        "brand_name": food.get("brand_name"),
        "food_name": food.get("food_name"),
    }

def _instant_search_best(
    brand_name: Optional[str],
    item_name: str,
    description: Optional[str],
    category: Optional[str],
    required: Optional[List[str]],
    forbidden: Optional[List[str]],
    nutritionix_query: str,
) -> Optional[Dict[str, Any]]:
    """Call Instant endpoint once with the deterministic nutritionix_query and pick best matching branded/common candidate."""
    url = "https://trackapi.nutritionix.com/v2/search/instant"
    sess = _get_nutritionix_session()
    try:
        resp = sess.get(url, params={"query": nutritionix_query}, timeout=_NUTRITIONIX_REQ_TIMEOUT_S)
        if resp.status_code != 200:
            return None
        data = resp.json()
    except Exception:
        return None

    bn_norm = _norm_brand(brand_name) if brand_name else None
    best: Optional[Dict[str, Any]] = None
    best_score = -10**9

    def _reject_mismatch(item_name_text: str, candidate_food: str) -> bool:
        try:
            it = (item_name_text or "").lower()
            cf = (candidate_food or "").lower()
            # Burger strictness
            if "cheeseburger" in it and not ("double" in it or "mcdouble" in it):
                if any(k in cf for k in ["double", "quarter", "big mac", "double quarter", "quarter pounder"]):
                    return True
            if ("mcdouble" in it or "double cheeseburger" in it) and not ("double" in cf or "mcdouble" in cf):
                return True
        except Exception:
            return False
        return False

    def consider(c: Dict[str, Any]) -> None:
        nonlocal best, best_score
        food_name_cand = c.get("food_name") or ""
        # Hard reject certain mismatches (e.g., Cheeseburger vs Double)
        if _reject_mismatch(item_name, food_name_cand):
            return
        sc = _score_candidate(item_name, description, category, required, forbidden, c)
        if sc > best_score:
            best_score = sc
            best = c

    branded = data.get("branded") or []
    common = data.get("common") or []
    # Prefer branded and enforce brand when we have one
    if brand_name and not _is_generic_brand(brand_name):
        for c in branded:
            bnorm = _norm_brand(c.get("brand_name"))
            if bn_norm and bnorm and (bnorm == bn_norm or (bn_norm in bnorm) or (bnorm in bn_norm)):
                consider(c)
        if best_score >= _GOOD_SCORE_THRESHOLD:
            return best
    else:
        # No brand: consider branded by score too
        for c in branded:
            consider(c)
    # Consider common if no strict brand
    for c in common:
        if brand_name and not _is_generic_brand(brand_name):
            continue
        consider(c)
    return best

def _natural_search_best(brand_name: Optional[str], nutritionix_query: str) -> Optional[Dict[str, Any]]:
    """Call Natural endpoint once with a compact query. Returns macro fields or None."""
    nl_url = "https://trackapi.nutritionix.com/v2/natural/nutrients"
    hint = None if _is_generic_brand(brand_name) else brand_name
    text = f"{(hint or '').strip()} {nutritionix_query}".strip()
    try:
        sess = _get_nutritionix_session()
        resp = sess.post(nl_url, json={"query": text}, timeout=_NUTRITIONIX_REQ_TIMEOUT_S)
        if resp.status_code != 200:
            return None
        full = resp.json().get("foods", [{}])[0]
        if hint and full.get("brand_name") and _norm_brand(full.get("brand_name")) != _norm_brand(hint):
            return None
        return _extract_macro_fields(full)
    except Exception:
        return None


def _title_size_from_enum(size_enum: Optional[str]) -> Optional[str]:
    if not size_enum:
        return None
    m = (size_enum or "").strip().upper()
    if m == "XS":
        return "XS"
    if m == "S":
        return "Small"
    if m == "M":
        return "Medium"
    if m == "L":
        return "Large"
    if m == "XL":
        return "XL"
    if m == "UNKNOWN":
        return None
    return size_enum


def _build_nutritionix_query_for_item(brand_name: Optional[str], entry: Dict[str, Any]) -> Optional[str]:
    name = (entry.get("item_name") or "").strip()
    desc = (entry.get("description") or "").strip()
    portion = (entry.get("portion_detail") or "").strip()
    size_enum = entry.get("size")
    size_word = _title_size_from_enum(size_enum)
    brand_n = _norm_raw(brand_name)
    text = f"{name} {desc} {portion}".lower()

    # Extract qualifiers
    expected = _parse_expected_from_item(name, f"{desc} {portion}", entry.get("category"))

    def with_brand(s: str) -> str:
        return f"{brand_name} {s}".strip() if brand_name and not _is_generic_brand(brand_name) else s

    # Nuggets (prefer explicit count; allow override by box text or visible piece count)
    if "nugget" in text:
        count = expected.get("count")
        # If portion mentions "6 pc box" or "10 pc box", override count
        if not count and ("pc box" in portion.lower() or "ct" in portion.lower()):
            mbox = re.search(r"\b(\d{1,3})\s*(?:pc|piece|pieces|ct)\b", portion.lower())
            if mbox:
                count = mbox.group(1)
        # If description contains a visible piece count (e.g., "6 pcs"), use it
        if not count and desc:
            mvis = re.search(r"\b(\d{1,3})\s*(?:pc|pcs|pieces)\b", desc.lower())
            if mvis:
                count = mvis.group(1)
        if brand_n == "mcdonalds":
            if count:
                return with_brand(f"Chicken McNuggets {count} Piece")
            return with_brand("Chicken McNuggets")
        # generic
        if count:
            return with_brand(f"chicken nuggets {count} piece")
        return with_brand("chicken nuggets")

    # Fries
    if "fries" in text or "french fries" in text:
        if brand_n == "mcdonalds":
            if size_word:
                return with_brand(f"World Famous Fries {size_word}")
            return with_brand("World Famous Fries")
        # generic
        if size_word:
            return with_brand(f"French fries {size_word}")
        return with_brand("French fries")

    # Drinks (strict soda vs coffee rules)
    if any(k in text for k in ["drink", "cola", "coke", "sprite", "soda", "cup"]):
        ounces = expected.get("ounces")
        if "coffee" in text or "iced coffee" in text or "latte" in text or "mccafé" in text or "mccafe" in text:
            # Coffee is only allowed with explicit cues; otherwise do not classify as drink here
            return with_brand((f"Iced Coffee {ounces} fl oz" if ounces else "Iced Coffee")).strip()
        if "sprite" in text:
            return with_brand((f"Sprite {ounces} fl oz" if ounces else (size_word or "")).strip())
        if any(k in text for k in ["coca-cola", "coke", "cola"]):
            return with_brand((f"Coca-Cola {ounces} fl oz" if ounces else (size_word or "")).strip())
        # Default to brand fountain lineup naming for soda (no generic "soft drink")
        return with_brand((f"Coca-Cola {ounces} fl oz" if ounces else (size_word or "Medium"))).strip()

    # Sauces
    if any(k in text for k in ["barbecue", "bbq"]) and "sauce" in text:
        return with_brand("Barbecue Sauce Packet")
    if "ranch" in text and "sauce" in text:
        return with_brand("Creamy Ranch Packet")

    # Burgers / Sandwiches
    if "cheeseburger" in text:
        return with_brand("Cheeseburger")
    if "mccrispy" in text:
        return with_brand("McCrispy")

    # Carrots
    if "carrot" in text:
        if brand_n == "mcdonalds":
            return with_brand("Carrot Sticks Kids Bag")
        return with_brand("Carrot sticks")

    # Fallback to name
    return with_brand(name)


def _normalize_item_name_for_brand(brand_name: Optional[str], entry: Dict[str, Any]) -> None:
    # No-op: per guidance, Nutritionix is only for calories; naming should come from AI output
    return None


def itemize_restaurant_items(visual_json: Dict[str, Any], dish_json: Dict[str, Any], image_token: Optional[str]) -> Dict[str, Any]:
    """Use OpenAI to produce Nutritionix-ready list of items from visual + dish JSON and image."""
    client = _get_openai_client()
    # Lazy import to prevent module import errors if prompts has transient issues
    try:
        from .prompts import get_restaurant_itemizer_prompt  # type: ignore
        system_prompt = get_restaurant_itemizer_prompt()
    except Exception:
        # Minimal fallback prompt to avoid total failure; expects same schema
        system_prompt = (
            "You are the Restaurant Meal Itemizer. Return JSON with keys restaurant_name, nl_query, items[]."
        )

    # Prepare content with JSONs; always add image when token provided, using precomputed data URL
    parts = [
        {"type": "text", "text": json.dumps({"visual_context": visual_json, "dish_determiner": dish_json}, ensure_ascii=False)}
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
        temperature=float(os.getenv("STEP3_TEMPERATURE", "0.15")),
        top_p=float(os.getenv("STEP3_TOP_P", "0.85")),
        max_tokens=800,
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content if response.choices else None
    if not content:
        raise RuntimeError("No content from OpenAI for restaurant itemization")
    # Log raw LLM output to diagnose JSON formatting issues
    try:
        logging.getLogger(__name__).info("ITEMIZER_RAW: %s", content)
    except Exception:
        pass
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        # Minimal salvage: strip common code fences and extract first {...}
        t = (content or "").strip().replace("```json", "").replace("```", "").strip()
        start_i = t.find("{")
        end_i = t.rfind("}")
        data = json.loads(t[start_i:end_i+1] if (start_i != -1 and end_i != -1 and end_i > start_i) else t)
    # Back-compat: allow either "itemizer_items" or legacy "items"
    if isinstance(data, dict) and "itemizer_items" in data and "items" not in data:
        data["items"] = data.get("itemizer_items")
    # Pass through AI-produced items as-is; compute nl_query from per-item nutritionix_query
    try:
        items = data.get("items") or []
        # Compose nl_query from per-item nutritionix_query (no quantities)
        phrases: List[str] = []
        for entry in items:
            nq = (entry.get("nutritionix_query") or "").strip()
            if nq:
                phrases.append(nq)
        if phrases:
            data["nl_query"] = "; ".join(phrases)
    except Exception:
        pass
    data["_duration_ms"] = int((time.perf_counter() - start) * 1000)
    return data


_CACHE_MACROS: Dict[str, Any] = {}


def _cache_key_for_item(brand: Optional[str], name: Optional[str], desc: Optional[str]) -> str:
    return f"{_norm_brand(brand or '')}|{(name or '').strip().lower()}|{(desc or '').strip().lower()}"


def fetch_nutritionix_macros(itemized: Dict[str, Any]) -> Dict[str, Any]:
    brand = itemized.get("restaurant_name")
    results: List[Dict[str, Any]] = []
    start = time.perf_counter()
    items = itemized.get("items", []) or []

    def process_entry(entry: Dict[str, Any]) -> Dict[str, Any]:
        name = entry.get("item_name")
        desc = entry.get("description")
        category = entry.get("category")
        req = entry.get("required_keywords")
        forb = entry.get("forbidden_keywords")
        nq = entry.get("nutritionix_query")
        qty = entry.get("quantity") or 1
        # Cache by brand/name/desc
        key = _cache_key_for_item(brand, name, desc)
        cached = _CACHE_MACROS.get(key)
        if cached is not None:
            return cached
        # Instant/Natural deterministic-first
        match = _nutritionix_search_item(brand, name, desc, category, req, forb, nq)
        nutrients = _nutritionix_nutrients_from_item(match, require_branded=bool(brand), brand_hint=brand) if match else None
        if nutrients is None and nq:
            nutrients = _natural_search_best(brand, nq)

        # Refinement loop: retry with deterministic query if still missing or implausible
        attempts = 0
        while attempts < 2 and nutrients is None:
            attempts += 1
            det_q = _build_nutritionix_query_for_item(brand, entry) or (nq or (name or ""))
            refined = _instant_search_best(brand, name or "", desc, category, req, forb, det_q)
            if refined is not None:
                match = refined
                nutrients = _nutritionix_nutrients_from_item(refined, require_branded=bool(brand), brand_hint=brand)
            if nutrients is None:
                nutrients = _natural_search_best(brand, det_q)

        # Cheeseburger strict retry for McDonald's when calories look like a double or larger
        try:
            if nutrients is not None and _norm_brand(brand or "") == "mcdonalds":
                nm_l = (name or "").lower()
                if "cheeseburger" in nm_l and not any(x in nm_l for x in ["double", "quarter", "big mac"]):
                    cal = nutrients.get("calories")
                    if cal is not None and cal > 450:
                        strict_q = f"{brand} Cheeseburger"
                        refined2 = _instant_search_best(brand, name or "", desc, category, req, forb, strict_q)
                        if refined2 is not None:
                            match = refined2
                            nutrients = _nutritionix_nutrients_from_item(refined2, require_branded=True, brand_hint=brand) or _natural_search_best(brand, strict_q)
        except Exception:
            pass
        final_name = name
        name_l = (name or "").strip().lower()
        if name_l in {"unidentified item", "unidentified", "unknown"} or "best guess" in name_l:
            if match and match.get("food_name"):
                final_name = match.get("food_name")
            elif nutrients and nutrients.get("food_name"):
                final_name = nutrients.get("food_name")
        result = {
            "item_name": final_name,
            "description": desc,
            "quantity": qty,
            "nutritionix_match": match,
            "macros": nutrients,
        }
        _CACHE_MACROS[key] = result
        return result

    # Bound concurrency to avoid API rate limits; 6 is a good starting point
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(8, max(1, len(items)))) as pool:
        futures = [pool.submit(process_entry, entry) for entry in items]
        for fut in concurrent.futures.as_completed(futures):
            results.append(fut.result())
    duration_ms = int((time.perf_counter() - start) * 1000)
    return {"restaurant_name": brand, "results": results, "_duration_ms": duration_ms}


def restaurant_calories_pipeline(visual_json: Dict[str, Any], dish_json: Dict[str, Any], image_token: Optional[str]) -> Dict[str, Any]:
    # Only proceed if dish_json indicates restaurant
    src = (dish_json or {}).get("source")
    if src != "RESTAURANT":
        return {"error": "Not a restaurant meal", "results": []}

    # Ensure restaurant name carries through; prefer Dish Determiner's brand if present
    itemized = itemize_restaurant_items(visual_json, dish_json, image_token)
    dd_brand = (dish_json or {}).get("restaurant_name")
    if dd_brand:
        itemized["restaurant_name"] = dd_brand
    macros = fetch_nutritionix_macros(itemized)
    return {"itemized": itemized, "macros": macros}


