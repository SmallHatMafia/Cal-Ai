@echo off
REM Ensure venv exists and dependencies are installed
if not exist backend_server\venv (
  py -3 -m venv backend_server\venv
)
call backend_server\venv\Scripts\activate
python -m pip install --upgrade pip
if exist requirements.txt (
  python -m pip install -r requirements.txt
) else (
  python -m pip install openai python-dotenv fastapi uvicorn
)

REM Start the Python FastAPI backend in a new terminal window with venv activated
start cmd /k "call backend_server\venv\Scripts\activate && python -m uvicorn backend_server.main:app --host 127.0.0.1 --port 8000"

REM Start the frontend Vite server in this terminal (port is set via vite.config.js)
cd frontend
npm run dev