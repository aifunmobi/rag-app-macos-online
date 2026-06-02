"""Synchronous HTTP client helpers for the local Ollama instance.

Every public function accepts an optional *host* parameter so callers and tests
can point at a different base URL. All functions share ``_client(host)`` as
their sole httpx.Client factory — monkeypatch that in tests to inject a
MockTransport without touching the network.
"""

from __future__ import annotations

import json
from typing import Iterator

import httpx

from rag_app.config import EMBED_MODEL, EMBED_NUM_CTX, OLLAMA_HOST


# ---------------------------------------------------------------------------
# Error type
# ---------------------------------------------------------------------------

class OllamaError(Exception):
    """Raised when an Ollama API call fails (non-200 or connection error)."""


# ---------------------------------------------------------------------------
# Private factory (monkeypatch point for tests)
# ---------------------------------------------------------------------------

def _client(host: str) -> httpx.Client:
    """Return an httpx.Client rooted at *host* with a generous timeout.

    A large model's cold-load plus long generations can take well over a minute,
    so httpx's 5s default would abort the first token. ``is_running`` passes a
    short per-request timeout to stay snappy.
    """
    return httpx.Client(base_url=host, timeout=httpx.Timeout(600.0, connect=10.0))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_running(host: str = OLLAMA_HOST, timeout: float = 2.0) -> bool:
    """Return True iff Ollama answers GET /api/version with HTTP 200."""
    try:
        with _client(host) as client:
            resp = client.get("/api/version", timeout=timeout)
            return resp.status_code == 200
    except Exception:
        return False


def list_models(host: str = OLLAMA_HOST) -> list[dict]:
    """Return the raw list of model dicts from GET /api/tags."""
    try:
        with _client(host) as client:
            resp = client.get("/api/tags")
    except httpx.ConnectError as exc:
        raise OllamaError(f"Cannot connect to Ollama at {host}") from exc
    if resp.status_code != 200:
        raise OllamaError(f"list_models: HTTP {resp.status_code}")
    return resp.json()["models"]


def list_model_names(host: str = OLLAMA_HOST) -> list[str]:
    """Return just the model name strings from /api/tags."""
    return [m["name"] for m in list_models(host=host)]


def pull_model(
    model: str,
    host: str = OLLAMA_HOST,
    progress=None,
) -> None:
    """Stream-pull *model* from the Ollama registry.

    If *progress* is not None it is called as ``progress(status, completed, total)``
    for each NDJSON line that contains meaningful keys.
    Raises OllamaError on connection error or non-200.
    """
    try:
        with _client(host) as client:
            with client.stream(
                "POST",
                "/api/pull",
                json={"model": model, "stream": True},
            ) as resp:
                if resp.status_code != 200:
                    raise OllamaError(f"pull_model: HTTP {resp.status_code}")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    obj = json.loads(line)
                    if progress is not None:
                        status = obj.get("status", "")
                        completed = obj.get("completed", 0)
                        total = obj.get("total", 0)
                        progress(status, completed, total)
    except OllamaError:
        raise
    except httpx.ConnectError as exc:
        raise OllamaError(f"Cannot connect to Ollama at {host}") from exc


def embed(
    texts: list[str],
    model: str = EMBED_MODEL,
    host: str = OLLAMA_HOST,
    options: dict | None = None,
) -> list[list[float]]:
    """Return embeddings for *texts* via POST /api/embed.

    Sends an explicit ``num_ctx`` (the embedder's context window) so Ollama never
    silently truncates a chunk. Pass *options* to override.

    Raises OllamaError on connection error or non-200.
    """
    opts = options if options is not None else {"num_ctx": EMBED_NUM_CTX}
    try:
        with _client(host) as client:
            resp = client.post(
                "/api/embed",
                json={"model": model, "input": texts, "options": opts},
            )
    except httpx.ConnectError as exc:
        raise OllamaError(f"Cannot connect to Ollama at {host}") from exc
    if resp.status_code != 200:
        raise OllamaError(f"embed: HTTP {resp.status_code}")
    return resp.json()["embeddings"]


def chat_stream(
    messages: list[dict],
    model: str,
    host: str = OLLAMA_HOST,
    options: dict | None = None,
) -> Iterator[str]:
    """Yield assistant content tokens from POST /api/chat (streaming).

    Stops as soon as a line with ``"done": true`` is seen.
    Raises OllamaError on connection error or non-200.
    """
    payload: dict = {"model": model, "messages": messages, "stream": True}
    if options is not None:
        payload["options"] = options
    try:
        with _client(host) as client:
            with client.stream("POST", "/api/chat", json=payload) as resp:
                if resp.status_code != 200:
                    raise OllamaError(f"chat_stream: HTTP {resp.status_code}")
                for line in resp.iter_lines():
                    if not line:
                        continue
                    obj = json.loads(line)
                    content = obj.get("message", {}).get("content", "")
                    if content:
                        yield content
                    if obj.get("done"):
                        return
    except OllamaError:
        raise
    except httpx.ConnectError as exc:
        raise OllamaError(f"Cannot connect to Ollama at {host}") from exc
