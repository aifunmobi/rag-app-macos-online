"""Ingestion pipeline: hash, chunk, embed, and store documents."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Callable

from rag_app import loaders, chunker, ollama_client
from rag_app.config import INPUT_DIR
from rag_app.store import Store


def file_hash(path: Path) -> str:
    """Return the SHA-256 hex digest of the file at *path*."""
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def ingest_file(path: Path, store: Store) -> dict:
    """Index a single file into *store*.

    Returns a dict with keys:
        path   – resolved path string
        status – "indexed" | "skipped" | "error"
        chunks – number of chunks (0 on error or when skipped with no prior chunks)
        error  – error message (only present when status=="error")
    """
    try:
        resolved = str(path.resolve())
        current_hash = file_hash(path)
        stored_hash = store.get_doc_hash(resolved)

        if stored_hash == current_hash:
            # Count existing chunks via list_documents lookup
            existing_chunks = 0
            for doc in store.list_documents():
                if doc["path"] == resolved:
                    existing_chunks = doc["n_chunks"]
                    break
            return {"path": resolved, "status": "skipped", "chunks": existing_chunks}

        # Load and chunk
        text = loaders.load_text(path)
        chunks = chunker.chunk_text(text)

        # Embed (with nomic document prefix) if there are chunks
        embeddings: list[list[float]] = []
        if chunks:
            embeddings = ollama_client.embed(
                ["search_document: " + c for c in chunks]
            )

        # Persist
        doc_id = store.upsert_document(resolved, current_hash)
        store.replace_chunks(
            doc_id,
            [(i, chunks[i], embeddings[i]) for i in range(len(chunks))],
        )

        return {"path": resolved, "status": "indexed", "chunks": len(chunks)}

    except Exception as exc:  # noqa: BLE001
        return {
            "path": str(path.resolve()),
            "status": "error",
            "chunks": 0,
            "error": str(exc),
        }


def ingest_directory(
    store: Store,
    input_dir: Path = INPUT_DIR,
    progress: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Ingest all supported files under *input_dir* and remove stale documents.

    *progress*, if given, is called once per file BEFORE it is processed as
    ``progress(current, total, filename)`` (1-based) so callers can surface a
    live "ingesting file N of M" indicator.

    Returns:
        {"indexed": int, "skipped": int, "errors": int, "removed": int,
         "documents": int, "chunks": int}
    """
    input_dir = Path(input_dir)

    # Collect supported files
    supported_files = [
        p for p in input_dir.rglob("*")
        if p.is_file() and loaders.is_supported(p)
    ]

    # Build set of resolved path strings for deletion detection
    current_resolved: set[str] = {str(p.resolve()) for p in supported_files}

    indexed = skipped = errors = 0
    total = len(supported_files)

    for idx, file_path in enumerate(supported_files, start=1):
        if progress is not None:
            progress(idx, total, file_path.name)
        result = ingest_file(file_path, store)
        status = result["status"]
        if status == "indexed":
            indexed += 1
        elif status == "skipped":
            skipped += 1
        else:
            errors += 1

    # Deletion detection: remove docs whose files are no longer present
    removed = 0
    for doc in store.list_documents():
        if doc["path"] not in current_resolved:
            store.delete_document(doc["path"])
            removed += 1

    stats = store.stats()
    return {
        "indexed": indexed,
        "skipped": skipped,
        "errors": errors,
        "removed": removed,
        "documents": stats["documents"],
        "chunks": stats["chunks"],
    }
