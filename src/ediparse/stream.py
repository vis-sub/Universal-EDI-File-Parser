"""Streaming entry points: feed bytes as they arrive and get one event per completed document.

    for event in stream("big_837_batch.edi"):
        if event.kind == "message":
            handle(event.message)          # one business document (ST/SE, UNH/UNT, ...)

Memory use is bounded by the largest single message, not the file size.
"""
from __future__ import annotations

import codecs
import os
from collections.abc import AsyncIterable, AsyncIterator, Iterable, Iterator
from typing import BinaryIO, Union

from .envelope import EnvelopeBuilder
from .model import Document, Event, Issue, Segment
from .tokenizer import Tokenizer

DEFAULT_CHUNK_SIZE = 1 << 16

Source = Union[str, "os.PathLike[str]", bytes, bytearray, BinaryIO, Iterable[bytes], Iterable[str]]


class _Decoder:
    """Incremental decoding. ``auto`` = UTF-8, falling back to Latin-1 at the first invalid byte."""

    def __init__(self, encoding: str = "auto"):
        self.auto = encoding == "auto"
        self.encoding = "utf-8" if self.auto else encoding
        self._dec = codecs.getincrementaldecoder(self.encoding)()
        self.switched = False

    def decode(self, data: bytes, final: bool = False) -> str:
        try:
            return self._dec.decode(data, final)
        except UnicodeDecodeError:
            if not self.auto or self.switched:
                raise
            pending = self._dec.getstate()[0]
            self.encoding, self.switched = "latin-1", True
            self._dec = codecs.getincrementaldecoder("latin-1")()
            return self._dec.decode(pending + data, final)


class StreamParser:
    """Push parser. Call ``feed()`` with each chunk (bytes or str) and ``close()`` at the end.

    Both return the events completed by that input. With ``retain=True`` the full
    tree is also kept and available from ``document()`` after ``close()``.
    """

    def __init__(self, encoding: str = "auto", retain: bool = False):
        self._decoder = _Decoder(encoding)
        self._tokenizer = Tokenizer(keep_prefix=retain)
        self._builder = EnvelopeBuilder(retain=retain)
        self._segments: list[Segment] | None = [] if retain else None
        self._closed = False

    @property
    def encoding(self) -> str:
        return self._decoder.encoding

    def feed(self, data: bytes | str) -> list[Event]:
        if isinstance(data, str):
            return self._consume(self._tokenizer.feed(data))
        was_switched = self._decoder.switched
        text = self._decoder.decode(bytes(data))
        if self._decoder.switched and not was_switched:
            self._builder.add_issue(Issue("info", "encoding_fallback",
                                          "Input is not valid UTF-8; decoding the rest as Latin-1"))
        return self._consume(self._tokenizer.feed(text))

    def close(self) -> list[Event]:
        if self._closed:
            return []
        self._closed = True
        tail = self._decoder.decode(b"", final=True)
        items = (self._tokenizer.feed(tail) if tail else []) + self._tokenizer.close()
        events = self._consume(items)
        self._builder.finish()
        return events + self._builder.take_events()

    def _consume(self, items: list) -> list[Event]:
        for item in items:
            if isinstance(item, Issue):
                self._builder.add_issue(item)
            else:
                if self._segments is not None:
                    self._segments.append(item)
                self._builder.feed(item)
        return self._builder.take_events()

    def document(self) -> Document:
        if self._segments is None or not self._closed:
            raise RuntimeError("document() needs StreamParser(retain=True) and close()")
        b = self._builder
        return Document(prefix=self._tokenizer.prefix, segments=self._segments, encoding=self.encoding,
                        interchanges=b.interchanges, stray=b.stray, issues=b.issues)


def iter_chunks(source: Source, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterator[bytes | str]:
    """Yield chunks from a path, bytes, binary file object, or iterable of bytes/str chunks."""
    if isinstance(source, (str, os.PathLike)):
        with open(source, "rb") as f:
            yield from iter(lambda: f.read(chunk_size), b"")
    elif isinstance(source, (bytes, bytearray, memoryview)):
        data = memoryview(source)
        for i in range(0, len(data), chunk_size):
            yield bytes(data[i:i + chunk_size])
    elif hasattr(source, "read"):
        yield from iter(lambda: source.read(chunk_size), source.read(0))
    else:
        yield from source


def stream(source: Source, *, chunk_size: int = DEFAULT_CHUNK_SIZE, encoding: str = "auto") -> Iterator[Event]:
    """Parse ``source`` incrementally, yielding each event as soon as it is complete.

    A ``str`` source is a file path; to parse EDI text, pass ``[text]``.
    """
    parser = StreamParser(encoding)
    for chunk in iter_chunks(source, chunk_size):
        yield from parser.feed(chunk)
    yield from parser.close()


async def astream(source: AsyncIterable[bytes | str], *, encoding: str = "auto") -> AsyncIterator[Event]:
    """Async variant of ``stream`` for async byte sources (sockets, HTTP bodies, object storage)."""
    parser = StreamParser(encoding)
    async for chunk in source:
        for event in parser.feed(chunk):
            yield event
    for event in parser.close():
        yield event
