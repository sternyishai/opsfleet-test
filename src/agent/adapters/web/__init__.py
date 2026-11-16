"""The optional web channel (ADR 0010): needs the ``web`` extra (FastAPI, Uvicorn)."""

from .app import create_app
from .sse import SseChannel

__all__ = ["SseChannel", "create_app"]
