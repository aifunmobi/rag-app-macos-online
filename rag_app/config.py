"""Shared configuration and paths for the RAG app.

This module is the integration contract: every other module imports paths and
constants from here. Paths are resolved relative to this file (never ``~`` or
hardcoded separators) so the app can be moved between install locations.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths (resolved from this file's location -> project root is ~/rag)
# ---------------------------------------------------------------------------
PROJECT_ROOT: Path = Path(__file__).resolve().parents[1]
INPUT_DIR: Path = PROJECT_ROOT / "input"
DATA_DIR: Path = PROJECT_ROOT / "data"
STATIC_DIR: Path = PROJECT_ROOT / "static"
DB_PATH: Path = DATA_DIR / "rag.db"
CONFIG_PATH: Path = DATA_DIR / "config.json"

# ---------------------------------------------------------------------------
# Ollama / models
# ---------------------------------------------------------------------------
OLLAMA_HOST: str = os.environ.get("RAG_OLLAMA_HOST", "http://localhost:11434")
EMBED_MODEL: str = "nomic-embed-text"
# Explicit embedding context window. nomic-embed-text's window is 2048 tokens;
# setting it here guarantees Ollama never silently truncates a chunk (our chunks
# stay under it) and future-proofs larger CHUNK_TOKENS.
EMBED_NUM_CTX: int = 2048
DEFAULT_CHAT_MODEL: str = "gemma3:4b"

# Newbie first-run picker: smallest-that-works first. (tier, ollama tag, approx size)
NEWBIE_LADDER: list[tuple[str, str, str]] = [
    ("Tiny", "llama3.2:1b", "~1.3 GB"),
    ("Balanced", "llama3.2:3b", "~2.0 GB"),
    ("Best", "gemma3:4b", "~3.3 GB"),
]

# ---------------------------------------------------------------------------
# Retrieval / chunking
# ---------------------------------------------------------------------------
CHUNK_TOKENS: int = 1024
CHUNK_OVERLAP: int = 160
TOP_K: int = 8

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------
SERVER_HOST: str = os.environ.get("RAG_HOST", "127.0.0.1")
SERVER_PORT: int = int(os.environ.get("RAG_PORT", "8000"))


def _ensure_dirs() -> None:
    """Create the input/ and data/ directories if they do not exist."""
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict:
    """Return the persisted config dict, or ``{}`` if none exists yet."""
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def save_config(cfg: dict) -> None:
    """Persist the config dict as UTF-8 JSON."""
    _ensure_dirs()
    CONFIG_PATH.write_text(
        json.dumps(cfg, indent=2, ensure_ascii=False), encoding="utf-8"
    )


def get_chat_model() -> str:
    """Return the configured chat model, falling back to the default."""
    return load_config().get("chat_model") or DEFAULT_CHAT_MODEL


def set_chat_model(model: str) -> None:
    """Persist the selected chat model."""
    cfg = load_config()
    cfg["chat_model"] = model
    save_config(cfg)
