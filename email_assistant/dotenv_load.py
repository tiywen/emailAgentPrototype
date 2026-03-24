from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv


def load_project_dotenv(*, override: bool = True) -> Path:
    """Load `.env` from the repository root (parent of this package)."""
    root = Path(__file__).resolve().parent.parent
    env_path = root / ".env"
    load_dotenv(env_path, override=override)
    return env_path
