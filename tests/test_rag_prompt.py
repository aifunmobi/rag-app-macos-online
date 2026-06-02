from rag_app.rag import build_prompt


def test_prompt_prefers_prose_for_document_questions() -> None:
    messages = build_prompt(
        "summarize this document",
        [
            {
                "doc_path": "paper.json",
                "chunk_index": 0,
                "text": '{"orders": [], "overall_reason": "benchmark"}',
            }
        ],
    )

    system = messages[0]["content"]

    assert "answer in natural-language prose" in system
    assert "Do not respond with JSON" in system
    assert "unless the user explicitly asks" in system
    assert "summarize the relevant meaning in prose" in system
