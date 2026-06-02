"""FastAPI integration hub for the RAG app."""

from __future__ import annotations

import json
import os
import re
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

import rag_app.ingest as ingest
import rag_app.loaders as loaders
import rag_app.ollama_client as ollama_client
import rag_app.rag as rag
from rag_app.config import (
    EMBED_MODEL,
    INPUT_DIR,
    STATIC_DIR,
    get_chat_model,
    set_chat_model,
)
from rag_app.store import Store
from rag_app.watcher import InputWatcher

# ---------------------------------------------------------------------------
# Module-level store (tests may replace rag_app.app.store before issuing
# requests; every endpoint reads this global at call time).
# ---------------------------------------------------------------------------
store: Store = Store()

# Track the watcher so /api/status can report it.
_watcher: InputWatcher | None = None

# ---------------------------------------------------------------------------
# Shared ingest progress (drives the UI "ingesting file N of M" banner).
# Updated from worker threads (upload threadpool + watcher), read by polls.
# ---------------------------------------------------------------------------
_ingest_lock = threading.Lock()
_ingest_progress: dict = {"active": False, "current": 0, "total": 0, "file": ""}


def _ingest_begin() -> None:
    with _ingest_lock:
        _ingest_progress.update(active=True, current=0, total=0, file="")


def _ingest_update(current: int, total: int, name: str) -> None:
    with _ingest_lock:
        _ingest_progress.update(active=True, current=current, total=total, file=name)


def _ingest_clear() -> None:
    with _ingest_lock:
        _ingest_progress.update(active=False, current=0, total=0, file="")


def _ingest_snapshot() -> dict:
    with _ingest_lock:
        return dict(_ingest_progress)


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _lifespan(application: FastAPI) -> AsyncIterator[None]:
    global _watcher

    store.init_db()

    if not os.environ.get("RAG_DISABLE_STARTUP"):
        try:
            ingest.ingest_directory(store, INPUT_DIR)
        except Exception as exc:  # noqa: BLE001
            print(f"[startup] ingest error: {exc}")

        try:
            _watcher = InputWatcher(
                store,
                progress=_ingest_update,
                on_start=_ingest_begin,
                on_done=_ingest_clear,
            )
            _watcher.start()
        except Exception as exc:  # noqa: BLE001
            print(f"[startup] watcher error: {exc}")
            _watcher = None

    yield

    # Shutdown
    if _watcher is not None:
        try:
            _watcher.stop()
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------

app = FastAPI(lifespan=_lifespan)


@app.middleware("http")
async def _no_cache_ui(request: Request, call_next):
    """Tell the browser to always revalidate the UI assets.

    StaticFiles/FileResponse only send an ETag; without Cache-Control the
    browser caches heuristically and may show a stale UI after an update. This
    forces revalidation (cheap 304s) so reloads always reflect the latest code.
    """
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    return response


app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class SelectModelRequest(BaseModel):
    model: str


class ChatRequest(BaseModel):
    conversation_id: int | None = None
    message: str


class DeleteDocRequest(BaseModel):
    path: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_is_running() -> bool:
    try:
        return ollama_client.is_running()
    except Exception:
        return False


def _safe_list_model_names() -> list[str]:
    try:
        return ollama_client.list_model_names()
    except ollama_client.OllamaError:
        return []
    except Exception:
        return []


def _safe_filename(name: str) -> str:
    """Reduce an uploaded filename to a safe basename (no path traversal)."""
    base = os.path.basename((name or "").replace("\\", "/"))
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip()
    return base or "upload"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/")
async def index() -> FileResponse:
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/status")
async def api_status() -> JSONResponse:
    is_up = _safe_is_running()
    models: list[str] = []
    if is_up:
        raw = _safe_list_model_names()
        models = [n for n in raw if not n.startswith(EMBED_MODEL)]
    return JSONResponse({
        "ollama": is_up,
        "index": store.stats(),
        "watcher": _watcher is not None,
        "chat_model": get_chat_model(),
        "models": models,
        "ingest": _ingest_snapshot(),
    })


@app.get("/api/ingest-status")
async def api_ingest_status() -> JSONResponse:
    """Lightweight endpoint polled by the UI to drive the progress banner."""
    return JSONResponse(_ingest_snapshot())


@app.get("/api/documents")
async def api_documents() -> JSONResponse:
    """List indexed documents (for the Index -> file list modal)."""
    docs = store.list_documents()
    for d in docs:
        # Add a display-friendly basename alongside the full path.
        d["name"] = os.path.basename(d.get("path", "")) or d.get("path", "")
    return JSONResponse(docs)


@app.post("/api/documents/delete")
async def api_documents_delete(req: DeleteDocRequest) -> JSONResponse:
    """Delete one indexed file: remove it from input/ and from the index.

    Only files inside the input/ directory may be deleted (no path traversal).
    """
    try:
        target = Path(req.path).resolve()
        input_root = INPUT_DIR.resolve()
        if target != input_root and input_root not in target.parents:
            return JSONResponse({"error": "path outside input directory"}, status_code=400)
        if target.is_file():
            target.unlink()
        store.delete_document(str(target))
        store.delete_document(req.path)  # in case the stored form differs
        return JSONResponse({"ok": True, "index": store.stats()})
    except Exception as exc:  # noqa: BLE001
        return JSONResponse({"error": str(exc)}, status_code=500)


@app.get("/api/models")
async def api_models() -> JSONResponse:
    try:
        names = ollama_client.list_model_names()
        return JSONResponse([n for n in names if not n.startswith(EMBED_MODEL)])
    except ollama_client.OllamaError:
        return JSONResponse([])


@app.post("/api/models/select")
async def api_models_select(req: SelectModelRequest) -> JSONResponse:
    try:
        set_chat_model(req.model)
        return JSONResponse({"ok": True})
    except ollama_client.OllamaError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@app.get("/api/history")
async def api_history_list() -> JSONResponse:
    try:
        return JSONResponse(store.list_conversations())
    except ollama_client.OllamaError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@app.post("/api/history")
async def api_history_create() -> JSONResponse:
    try:
        conv_id = store.create_conversation()
        return JSONResponse({"id": conv_id})
    except ollama_client.OllamaError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@app.get("/api/history/{conv_id}")
async def api_history_get(conv_id: int) -> JSONResponse:
    try:
        return JSONResponse(store.get_messages(conv_id))
    except ollama_client.OllamaError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@app.delete("/api/history/{conv_id}")
async def api_history_delete(conv_id: int) -> JSONResponse:
    try:
        store.delete_conversation(conv_id)
        return JSONResponse({"ok": True})
    except ollama_client.OllamaError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)


@app.post("/api/upload")
async def api_upload(files: list[UploadFile] = File(...)) -> JSONResponse:
    """Save uploaded files into the input/ folder and index them immediately."""
    saved: list[str] = []
    skipped: list[dict] = []
    for f in files:
        name = _safe_filename(f.filename or "upload")
        dest = INPUT_DIR / name
        if not loaders.is_supported(dest):
            skipped.append({"name": name, "reason": "unsupported type"})
            continue
        INPUT_DIR.mkdir(parents=True, exist_ok=True)
        data = await f.read()
        dest.write_bytes(data)
        saved.append(name)
    _ingest_begin()
    try:
        summary = await run_in_threadpool(
            ingest.ingest_directory, store, INPUT_DIR, _ingest_update
        )
    except ollama_client.OllamaError as exc:
        return JSONResponse(
            {"saved": saved, "skipped": skipped, "error": str(exc)}, status_code=503
        )
    finally:
        _ingest_clear()
    return JSONResponse(
        {"saved": saved, "skipped": skipped, "index": store.stats(), "summary": summary}
    )


@app.post("/api/reindex")
async def api_reindex() -> JSONResponse:
    _ingest_begin()
    try:
        summary = await run_in_threadpool(
            ingest.ingest_directory, store, INPUT_DIR, _ingest_update
        )
        return JSONResponse(summary)
    except ollama_client.OllamaError as exc:
        return JSONResponse({"error": str(exc)}, status_code=503)
    finally:
        _ingest_clear()


@app.post("/api/chat")
async def api_chat(req: ChatRequest) -> StreamingResponse:
    # Capture the module-global store at call time so tests can replace it.
    active_store = store

    def gen():
        # Determine (or create) the conversation.
        if req.conversation_id is None:
            title = req.message[:40]
            conv_id = active_store.create_conversation(title)
        else:
            conv_id = req.conversation_id

        # Persist the user message.
        active_store.add_message(conv_id, "user", req.message)

        # Stream the RAG answer.
        buffer: list[str] = []
        sources: list[dict] = []

        try:
            for event in rag.stream_answer(req.message, active_store, get_chat_model()):
                event_type = event.get("type")

                if event_type == "sources":
                    sources = event.get("sources", [])
                elif event_type == "token":
                    buffer.append(event.get("text", ""))
                elif event_type == "error":
                    yield f"data: {json.dumps(event)}\n\n"
                    # Still persist what we have so far, then return.
                    assistant_text = "".join(buffer)
                    active_store.add_message(conv_id, "assistant", assistant_text, sources)
                    yield f"data: {json.dumps({'type': 'done', 'conversation_id': conv_id})}\n\n"
                    return
                elif event_type == "done":
                    # rag emits its own done; we emit the authoritative one
                    # (carrying conversation_id) after the loop. Don't forward.
                    continue

                yield f"data: {json.dumps(event)}\n\n"

        except Exception as exc:  # noqa: BLE001
            err_event = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(err_event)}\n\n"

        # Persist assistant message.
        assistant_text = "".join(buffer)
        active_store.add_message(conv_id, "assistant", assistant_text, sources)

        # Final done with conversation_id.
        yield f"data: {json.dumps({'type': 'done', 'conversation_id': conv_id})}\n\n"

    return StreamingResponse(gen(), media_type="text/event-stream")
