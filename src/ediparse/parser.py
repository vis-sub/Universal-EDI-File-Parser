"""Whole-document entry points: bytes/text/file in, Document (full tree) out.

For large inputs prefer ``ediparse.stream``, which never holds more than one message.
"""
from __future__ import annotations

from pathlib import Path

from .model import Document
from .stream import StreamParser


def decode(data: bytes) -> tuple[str, str]:
    """Decode losslessly: UTF-8 if valid, else Latin-1 (which maps every byte)."""
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin-1"


def parse_text(text: str, encoding: str = "utf-8") -> Document:
    p = StreamParser(retain=True)
    p.feed(text)
    p.close()
    doc = p.document()
    doc.encoding = encoding
    return doc


def parse_bytes(data: bytes) -> Document:
    text, encoding = decode(data)
    return parse_text(text, encoding)


def parse_file(path: str | Path) -> Document:
    return parse_bytes(Path(path).read_bytes())
