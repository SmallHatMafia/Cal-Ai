from fastapi import FastAPI, WebSocket, Request, UploadFile, File
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import subprocess
import asyncio
import sys
sys.path.append('.')
from .cli import handle_command
import logging
from typing import List
import threading
import concurrent.futures
import os
try:
    # Legacy import (module may have been removed). Provide no-op fallback.
    from .models.return_foods import preload_model_and_indices  # type: ignore
except Exception:
    def preload_model_and_indices() -> None:  # type: ignore
        return None

app = FastAPI()

# Allow frontend dev server
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set up logging to file
LOG_FILE = "backend.log"
logger = logging.getLogger()
logger.setLevel(logging.INFO)
file_handler = logging.FileHandler(LOG_FILE, mode='a', encoding='utf-8')
file_handler.setFormatter(logging.Formatter('%(asctime)s %(levelname)s: %(message)s'))
logger.handlers = [file_handler]

logger.info("Backend server started!")

# Load .env early so all bots see OPENAI_API_KEY, etc.
try:
    from dotenv import load_dotenv
    from pathlib import Path
    backend_dir = Path(__file__).resolve().parent
    explicit_env = backend_dir / ".env"
    if explicit_env.exists():
        load_dotenv(dotenv_path=str(explicit_env), override=True)
    else:
        # fallback to default search
        load_dotenv(override=True)
    logger.info(f".env loaded; OPENAI_API_KEY set={bool(os.getenv('OPENAI_API_KEY'))}")
except Exception as _env_exc:
    logger.error(f"Error loading .env: {_env_exc}")

@app.get("/")
def read_root():
    logger.info("GET / called")
    return {"status": "Backend running"}

@app.websocket("/ws/terminal")
async def websocket_terminal(websocket: WebSocket):
    logger.info("WebSocket /ws/terminal connection opened")
    await websocket.accept()
    try:
        while True:
            data = await websocket.receive_text()
            logger.info(f"Received terminal command: {data}")
            # Run the command and collect output
            proc = await asyncio.create_subprocess_shell(
                data,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            output = ""
            if proc.stdout is not None:
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    output += line.decode()
            await proc.wait()
            # Read logs/errors
            logs = ""
            if os.path.exists(LOG_FILE):
                with open(LOG_FILE, 'r', encoding='utf-8') as f:
                    logs = f.read()
                # Optionally clear the log after reading
                open(LOG_FILE, 'w').close()
            # Send both output and logs
            await websocket.send_text(f"[OUTPUT]\n{output}\n[LOGS]\n{logs}")
    except Exception as e:
        logger.error(f"Terminal WebSocket error: {e}")
        await websocket.send_text(f"Error: {e}")
    finally:
        logger.info("WebSocket /ws/terminal connection closed")
        await websocket.close()

@app.post("/cli")
async def cli_endpoint(request: Request):
    data = await request.json()
    command = data.get("command", "")
    args = data.get("args", [])
    logger.info(f"/cli called with command={command}, args={args}")
    result = handle_command(command, args)
    logger.info(f"/cli result: {result}")
    return JSONResponse(content=result)

@app.get("/logs")
async def get_logs():
    logs = ""
    if os.path.exists(LOG_FILE):
        with open(LOG_FILE, 'r', encoding='utf-8') as f:
            logs = f.read()
        open(LOG_FILE, 'w').close()
    return JSONResponse(content={"logs": logs})

# Patch print to also log
import builtins
old_print = print
def new_print(*args, **kwargs):
    old_print(*args, **kwargs)
    logger.info(' '.join(str(a) for a in args))
builtins.print = new_print

@app.on_event("startup")
def preload_on_startup():
    preload_model_and_indices() 


# Bots API
@app.post("/bots/visual-context")
async def visual_context_endpoint(file: UploadFile = File(...)):
    try:
        logger.info(f"/bots/visual-context called filename={file.filename} content_type={file.content_type}")
        content = await file.read()
        from .models.visual_context import run_visual_context_and_forward
        result = run_visual_context_and_forward(content, file.content_type or "image/jpeg")
        logger.info(f"/bots/visual-context success _duration_ms={result.get('_duration_ms')}")
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"/bots/visual-context error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/bots/dish-determiner")
async def dish_determiner_endpoint(request: Request):
    try:
        body = await request.json()
        visual_json = body.get("visual_json")
        image_token = body.get("image_token") or (visual_json or {}).get("_image_token")
        if not visual_json:
            return JSONResponse(content={"error": "Missing visual_json in request body"}, status_code=400)
        logger.info(f"/bots/dish-determiner called image_token={bool(image_token)}")
        from .models.dish_determiner import determine_dishes_from_visual_json_and_image
        result = determine_dishes_from_visual_json_and_image(visual_json, image_token)
        logger.info(f"/bots/dish-determiner success _duration_ms={result.get('_duration_ms')}")
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"/bots/dish-determiner error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@app.post("/bots/restaurant-calories")
async def restaurant_calories_endpoint(request: Request):
    try:
        body = await request.json()
        visual_json = body.get("visual_json")
        dish_json = body.get("dish_json")
        image_token = body.get("image_token") or (visual_json or {}).get("_image_token") or (dish_json or {}).get("_image_token")
        if not visual_json or not dish_json:
            return JSONResponse(content={"error": "Missing visual_json or dish_json"}, status_code=400)
        logger.info(f"/bots/restaurant-calories called source={(dish_json or {}).get('source')} image_token={bool(image_token)}")
        from .models.resturant_calories import itemize_restaurant_items, fetch_nutritionix_macros
        # Run itemization and Nutritionix lookup concurrently when possible
        start = asyncio.get_event_loop().time()
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            loop = asyncio.get_event_loop()
            itemized_task = loop.run_in_executor(pool, itemize_restaurant_items, visual_json, dish_json, image_token)
            itemized = await itemized_task
            macros_task = loop.run_in_executor(pool, fetch_nutritionix_macros, itemized)
            macros = await macros_task
        result = {"itemized": itemized, "macros": macros}
        return JSONResponse(content=result)
    except Exception as e:
        logger.error(f"/bots/restaurant-calories error: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)