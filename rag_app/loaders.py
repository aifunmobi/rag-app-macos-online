from __future__ import annotations

import re
from html.parser import HTMLParser
from pathlib import Path

SUPPORTED_EXTS: set[str] = {
    ".pdf",
    ".docx",
    ".txt",
    ".md",
    ".markdown",
    ".html",
    ".htm",
    ".py",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".json",
    ".css",
    ".scss",
    ".sh",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".csv",
    ".rst",
}


def is_supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTS


def load_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_EXTS:
        raise ValueError(f"unsupported: {suffix}")
    if suffix == ".pdf":
        return _load_pdf(path)
    if suffix == ".docx":
        return _load_docx(path)
    if suffix in {".html", ".htm"}:
        return _load_html(path)
    return _load_plain(path)


def _load_pdf(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n".join(pages)


def _load_docx(path: Path) -> str:
    from docx import Document

    doc = Document(str(path))
    return "\n".join(paragraph.text for paragraph in doc.paragraphs)


class _HTMLStripper(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def get_text(self) -> str:
        text = " ".join(self._parts)
        # collapse whitespace
        return re.sub(r"\s+", " ", text).strip()


def _load_html(path: Path) -> str:
    raw = path.read_text(encoding="utf-8", errors="replace")
    stripper = _HTMLStripper()
    stripper.feed(raw)
    return stripper.get_text()


def _load_plain(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")
