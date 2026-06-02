"""Text chunker for the RAG app.

Splits text on line boundaries, greedily filling chunks up to ``chunk_tokens``
estimated tokens, with whole-line overlap between consecutive chunks.
A single line that exceeds ``chunk_tokens`` is hard-split by character count.
"""

from __future__ import annotations

from rag_app.config import CHUNK_OVERLAP, CHUNK_TOKENS


def _est(s: str) -> int:
    """Estimate token count of a string as max(1, len(s) // 4)."""
    return max(1, len(s) // 4)


def _hard_split(line: str, chunk_tokens: int) -> list[str]:
    """Split a single line into character-slices of <= chunk_tokens tokens."""
    char_limit = chunk_tokens * 4
    return [line[i : i + char_limit] for i in range(0, len(line), char_limit)]


def chunk_text(
    text: str,
    chunk_tokens: int = CHUNK_TOKENS,
    overlap: int = CHUNK_OVERLAP,
) -> list[str]:
    """Split *text* into overlapping chunks respecting estimated token limits.

    Parameters
    ----------
    text:
        Raw input text.
    chunk_tokens:
        Maximum estimated tokens per chunk.
    overlap:
        Target estimated tokens carried over from the previous chunk.

    Returns
    -------
    list[str]
        Non-empty, stripped chunks.
    """
    if not text or not text.strip():
        return []

    # Break input into lines; hard-split any line that exceeds the chunk limit.
    raw_lines: list[str] = text.splitlines()
    lines: list[str] = []
    for raw in raw_lines:
        if _est(raw) > chunk_tokens:
            lines.extend(_hard_split(raw, chunk_tokens))
        else:
            lines.append(raw)

    chunks: list[str] = []
    current: list[str] = []
    current_tokens: int = 0

    for line in lines:
        line_tokens = _est(line)

        # Cost of adding this line: the line itself plus the joining newline
        # (if there are already lines in the current chunk).
        join_cost = _est("\n") if current else 0

        if current and current_tokens + join_cost + line_tokens > chunk_tokens:
            # Emit the current chunk.
            chunk_text_val = "\n".join(current).strip()
            if chunk_text_val:
                chunks.append(chunk_text_val)

            # Build overlap carry-over: take trailing lines whose combined
            # estimated tokens (including joining newlines) are about `overlap`.
            carry: list[str] = []
            carry_tokens = 0
            for prev_line in reversed(current):
                t = _est(prev_line) + (_est("\n") if carry else 0)
                if carry_tokens + t > overlap:
                    break
                carry.insert(0, prev_line)
                carry_tokens += t

            current = carry + [line]
            current_tokens = carry_tokens + (_est("\n") if carry else 0) + line_tokens
        else:
            current.append(line)
            current_tokens += join_cost + line_tokens

    # Emit the final chunk.
    if current:
        chunk_text_val = "\n".join(current).strip()
        if chunk_text_val:
            chunks.append(chunk_text_val)

    return chunks
