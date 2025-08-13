## Project Context

Concise, reusable overview of the backend + frontend app to accelerate future tasks and testing.

### Overview
- **Backend**: FastAPI service in `backend_server` with REST and WebSocket endpoints. It runs a 3-step image → meal → calories pipeline using OpenAI and Nutritionix.
- **Frontend**: Svelte + Vite app on port 3000, proxying `/api` to the backend on 8000.
- **Dev runner**: `server.bat` creates/activates a venv, installs Python deps, starts the backend, then runs the frontend dev server.

### Run locally
1. From `backend/`, run `server.bat` (Windows).
2. Open `http://localhost:3000`.

### Environment variables (.env)
- **Required**: `OPENAI_API_KEY`
- **Nutritionix (for restaurant calories)**: `NUTRITIONIX_APP_ID`, `NUTRITIONIX_API_KEY`
- **Models (optional)**: `OPENAI_VISION_MODEL` (default `gpt-4o-mini`), `OPENAI_TEXT_MODEL` (default `gpt-4o-mini`)
- **Step tuning (optional)**: `STEP1_TEMPERATURE`, `STEP1_TOP_P`, `STEP2_TEMPERATURE`, `STEP2_TOP_P`, `STEP3_TEMPERATURE`, `STEP3_TOP_P`
- **Discovery**: modules attempt `backend_server/.env`, `backend/.env`, CWD `.env`, and user home `.env`.

### Backend
- Entrypoint: `backend_server/main.py`
  - CORS: `*` for local dev
  - Logging: unified file `backend/backend.log`; `print` is patched to also log
  - Startup: calls `preload_model_and_indices()` (currently a no-op)

- Endpoints
  - `GET /` → health check `{ "status": "Backend running" }`
  - `WS /ws/terminal` → runs shell commands; returns `[OUTPUT]` and `[LOGS]` (dev-only)
  - `POST /cli` → command router → `backend_server/cli.py::handle_command`
  - `GET /logs` → returns and then clears `backend.log`
  - `POST /bots/visual-context` (multipart `file`) → returns Visual Context JSON and `_image_token`
  - `POST /bots/dish-determiner` (JSON: `visual_json`, optional `image_token`) → returns dish/source JSON
  - `POST /bots/restaurant-calories` (JSON: `visual_json`, `dish_json`, optional `image_token`) → returns `{ itemized, macros }`

- CLI commands (`backend_server/cli.py`)
  - `visual_context <imagePath|dataURL>` → run visual context analysis
  - `dish_determiner <visual_json_str> [image_token]` → classify dish
  - `restaurant_calories <visual_json_str> <dish_json_str> [image_token]` → itemize + Nutritionix lookup

### Core pipeline and models
1) `models/visual_context.py` (Step 1: Visual Context)
   - `analyze_visual_context_from_bytes(image_bytes, mime)` calls OpenAI Vision with a strict JSON prompt from `models/prompts.py`.
   - Output (strict single JSON object; no extra text):
     - `items`: array of `{ name, estimated_quantity, size_hint(=XS|S|M|L|XL|UNKNOWN), physical_description }`
     - `context`: `{ environment, background_elements[], packaging_cues[], notable_cues[] }`
     - `_duration_ms`: timing metadata
   - `run_visual_context_and_forward(image_bytes, mime)` returns the JSON and attaches an `_image_token` stored in-memory via `ImageStore`.
   - Downstream image inclusion: stages 2 and 3 will include the image automatically when an `image_token` is provided via `ImageStore`.

2) `models/dish_determiner.py` (Step 2: Dish Determiner)
   - `determine_dishes_from_visual_json(visual_json)` and `determine_dishes_from_visual_json_and_image(visual_json, image_token)` call OpenAI (text/vision) using a strict JSON system prompt; salvage logic handles fenced/non-JSON replies; returns `_duration_ms`.
   - Output (strict single JSON object; no extra text):
     - `source`: "RESTAURANT" | "HOME"
     - `restaurant_type`: "FAST_FOOD" | "SIT_DOWN" | "UNKNOWN"
     - `restaurant_name`: exact brand if clearly indicated; else empty string
     - `dish_name`: recognizable main item
     - `components`:
       - `main[] | sides[] | drinks[] | extras[]` with elements `{ name, size_hint }`, and for drinks `{ volume_estimate? }`
     - `_duration_ms`: timing metadata
   - Brand reliability: set `restaurant_name` only with strong cues (see prompts rules);
     value is trimmed and propagated downstream when present.

3) `models/resturant_calories.py` (Step 3: Restaurant Itemizer + Nutritionix)
   - `itemize_restaurant_items(visual_json, dish_json, image_token)`
     - Calls OpenAI (vision if `RI_INCLUDE_IMAGE=1` and image available) with a strict JSON itemizer prompt.
     - Output (strict single JSON object; no extra text):
       - `restaurant_name`: must equal Step 2 `restaurant_name` (brand lock)
      - `nl_query`: semicolon-separated phrases for Nutritionix built from per-item `nutritionix_query` values (no quantities), e.g., "McDonald's Coca-Cola, 21 fl oz; McDonald's Chicken McNuggets, 6 Piece"
       - `items[]`: objects with fields
         - `item_name`, `quantity`, `size`(=XS|S|M|L|XL|UNKNOWN), `portion_detail`, `description`, `confidence`(0..1), `mapped_from_component`(main|sides|drinks|extras)
       - `validation`: `{ brand_lock: true, detected_conflict: boolean, notes: string }`
       - `_duration_ms`: timing metadata
   - `fetch_nutritionix_macros(itemized)`
     - Performs Nutritionix lookup using Instant and Natural endpoints.
     - Brand normalization (`_norm_brand`) and candidate query generation (`_generate_item_queries`).
     - Scoring (`_score_candidate`) prioritizes likely matches; falls back to natural-language nutrients when needed.
     - Caching by a stable brand/name/description key; bounded concurrency (up to 8 workers); returns `{ results[], _duration_ms }`.
   - `restaurant_calories_pipeline(visual_json, dish_json, image_token)`
     - Guard: only if `dish_json.source == "RESTAURANT"`.
     - Runs itemization (respecting brand lock), then Nutritionix lookup. Returns `{ itemized, macros }`.

### Frontend
- `frontend/vite.config.js` proxies `/api` → `http://127.0.0.1:8000` and serves on port 3000.
- `MainTerminal.svelte` handles the typical flow: upload → visual context → dish determiner → (if restaurant) restaurant calories; shows step timings.
- `Terminal.svelte` is a dev WebSocket terminal tied to `/ws/terminal` and `/logs`.

### Data flow (summary)
1. Visual Context → JSON + `_image_token`
2. Dish Determiner → `{ source, restaurant_type, restaurant_name?, dish_name, components, _duration_ms }`
3. If restaurant: Itemizer → `{ restaurant_name, nl_query, items[], validation, _duration_ms }` then Nutritionix → macros per item

### Key files
- `backend_server/main.py`, `backend_server/cli.py`
- `backend_server/models/*.py` (LLM + Nutritionix logic)
- `frontend/*` (Svelte app, Vite config)
- `server.bat`, `backend.bat`, `requirements.txt`
- Data snapshots in `backend_server/data/` (not used in current code paths)

### Logging and dev notes
- Unified `backend/backend.log`; some endpoints clear it after reading.
- `/ws/terminal` executes arbitrary commands; for local development only.

### Known gaps
- `models/return_foods.py` is not present; `preload_model_and_indices()` safely falls back to a no-op.
- `models/meal_creator.py` is empty.
- `models/home_cooked_calories.py` is empty.

### Example requests
- Visual Context (multipart):
```
POST /bots/visual-context
file: <image>
```

- Dish Determiner:
```
POST /bots/dish-determiner
{
  "visual_json": { ... },
  "image_token": "..." // optional
}
```

- Restaurant Calories:
```
POST /bots/restaurant-calories
{
  "visual_json": { ... },
  "dish_json": { ... },
  "image_token": "..." // optional
}
```

### Dependencies
### Prompts (centralized)
- Location: `backend_server/models/prompts.py`
- Exposes `get_visual_context_prompt()`, `get_dish_determiner_prompt()`, `get_restaurant_itemizer_prompt()` and a `PROMPTS` registry.
- All prompts enforce: return exactly one valid JSON object; no extra text; double quotes; no comments or trailing commas.
- Step-specific highlights:
  - Visual Context: capture only visible facts; strong packaging/size cues; avoid classification/calories.
  - Dish Determiner: conservative brand rules; generic component naming with size/count hints.
  - Restaurant Itemizer: brand lock to Step 2 brand; outputs `nl_query`, detailed `items[]`, and `validation` with conflict handling.
- Python (see `requirements.txt`): FastAPI, Uvicorn, OpenAI SDK v1+, python-dotenv, requests, python-multipart, Pillow
- Frontend: `svelte`, `@sveltejs/vite-plugin-svelte`, `vite`

