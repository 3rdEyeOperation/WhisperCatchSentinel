"""FastAPI router/app composition for the WhisperCatch Sentinel broker."""
from .factory import AppDependencies, create_app

__all__ = ["AppDependencies", "create_app"]
