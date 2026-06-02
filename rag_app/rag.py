"""RAG pipeline: retrieval and streamed answer generation.

Public surface
--------------
retrieve(question, store, top_k) -> list[dict]
build_prompt(question, chunks)   -> list[dict]   (messages for ollama_client.chat_stream)
stream_answer(question, store, model, top_k) -> Iterator[dict]
"""

from __future__ import annotations

from typing import Iterator

from rag_app import ollama_client
from rag_app.config import TOP_K


def retrieve(question: str, store, top_k: int = TOP_K) -> list[dict]:
    """Embed *question* and return the top-k chunks from *store*."""
    emb = ollama_client.embed(["search_query: " + question])[0]
    return store.query(emb, top_k)


def build_prompt(question: str, chunks: list[dict]) -> list[dict]:
    """Return a messages list (system + user) for ollama_client.chat_stream.

    Each chunk block in the user message includes the source filename,
    chunk index, and text so the model can cite its sources.
    """
    system_content = (
        "You answer questions using ONLY the provided context. "
        "When the context supports an answer, state it directly and confidently — "
        "do NOT add disclaimers like 'the context doesn't say' or 'I can't be sure' "
        "when the answer is actually present in the context. "
        "Only say you don't know when the context genuinely lacks the information. "
        "Never invent facts beyond the context. Be concise and accurate."
    )

    context_blocks: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        doc_path = chunk.get("doc_path", "unknown")
        chunk_index = chunk.get("chunk_index", 0)
        text = chunk.get("text", "")
        context_blocks.append(
            f"[{i}] File: {doc_path} | Chunk: {chunk_index}\n{text}"
        )

    context_str = "\n\n".join(context_blocks)
    user_content = f"Context:\n{context_str}\n\nQuestion: {question}"

    return [
        {"role": "system", "content": system_content},
        {"role": "user", "content": user_content},
    ]


def stream_answer(
    question: str,
    store,
    model: str,
    top_k: int = TOP_K,
) -> Iterator[dict]:
    """Stream a RAG answer as a sequence of typed event dicts.

    Event protocol (in order):
      {"type": "sources", "sources": [...]}
      {"type": "token",   "text": "..."}   (zero or more)
      {"type": "done"}
      {"type": "error",   "message": "..."} (replaces done on failure)
    """
    # 1. Retrieve chunks
    try:
        chunks = retrieve(question, store, top_k)
    except ollama_client.OllamaError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    # 2. Emit sources
    sources = [
        {
            "doc_path": c.get("doc_path"),
            "chunk_index": c.get("chunk_index"),
            "score": c.get("score"),
        }
        for c in chunks
    ]
    yield {"type": "sources", "sources": sources}

    # 3. Empty index fast-path
    if not chunks:
        yield {
            "type": "token",
            "text": (
                "I don't have any indexed documents yet. "
                "Drop files into the input/ folder, then re-index and ask again."
            ),
        }
        yield {"type": "done"}
        return

    # 4. Build messages
    messages = build_prompt(question, chunks)

    # 5. Stream tokens
    try:
        for delta in ollama_client.chat_stream(messages, model):
            yield {"type": "token", "text": delta}
    except ollama_client.OllamaError as exc:
        yield {"type": "error", "message": str(exc)}
        return

    # 6. Done
    yield {"type": "done"}
