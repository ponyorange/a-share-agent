"""Load environment variables from .env files."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv

_LOADED = False


def load_env() -> None:
    """Load backend/.env then repo-root/.env (backend wins on conflicts)."""
    global _LOADED
    if _LOADED:
        return
    backend_dir = Path(__file__).resolve().parents[1]
    repo_dir = backend_dir.parent
    # Root first, then backend overrides
    load_dotenv(repo_dir / ".env", override=False)
    load_dotenv(backend_dir / ".env", override=True)
    _LOADED = True
