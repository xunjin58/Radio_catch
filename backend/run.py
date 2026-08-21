"""Convenience local server launcher: `python run.py`."""
from pathlib import Path

from dotenv import load_dotenv
import uvicorn

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
