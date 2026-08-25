"""Convenience local server launcher: `python run.py`."""
import os
from pathlib import Path

from dotenv import load_dotenv
import uvicorn

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def main() -> None:
    uvicorn.run(
        "app.main:app",
        host="127.0.0.1",
        port=int(os.getenv("RADIO_CATCH_API_PORT", "8001")),
        reload=True,
    )


if __name__ == "__main__":
    main()
