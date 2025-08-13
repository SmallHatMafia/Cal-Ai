@echo off
REM Start the Python FastAPI backend in a new terminal window with venv activated
start cmd /k "call backend_server\venv\Scripts\activate && python -m uvicorn backend_server.main:app --host 127.0.0.1 --port 8000"