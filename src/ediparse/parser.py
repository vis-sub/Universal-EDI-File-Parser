"""Whole-document entry points: bytes/text/file in, Document (full tree) out.

For large inputs prefer ``ediparse.stream``, which never holds more than one message.
"""
from __future__ import annotations

from pathlib import Path

from .model import Document
from .stream import StreamParser


def parse_text(text: str, encoding: str = "utf-8") -> Document:
    p = StreamParser(retain=True)
    p.feed(text)
    p.close()
    doc = p.document()
    doc.encoding = encoding
    return doc


def parse_bytes(data: bytes, encoding: str = "auto") -> Document:
    """Parse raw bytes. ``auto``: UTF-8, with any invalid bytes read as Latin-1 (still round-trips exactly)."""
    p = StreamParser(encoding, retain=True)
    p.feed(data)
    p.close()
    return p.document()


def parse_file(path: str | Path) -> Document:
    return parse_bytes(Path(path).read_bytes())
