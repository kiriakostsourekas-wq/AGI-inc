"""ASGI entrypoint used by local Compose and deployment commands."""

from .api import app

__all__ = ["app"]
