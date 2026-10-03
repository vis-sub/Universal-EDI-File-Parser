"""HTTP service: POST an EDI file, get back one JSON object per business document as a stream.

Run from a checkout:  pip install -e ".[service]" && ediparse serve
Run in Docker:        make up   (or docker compose up)

Uploads are written to a spool (memory up to EDIPARSE_SPOOL_MEMORY_MB, then a temp
file), and the response streams NDJSON as documents are parsed. Results start after
the upload finishes. Answering while the upload is still running can deadlock clients
that don't read until their upload completes, so the service doesn't do it.

Every stage is bounded: upload size, decompressed size, segment length, segments kept
per message, and issues kept per message/interchange (see Settings). CPU-heavy work
runs in worker threads, never on the event loop.
"""
from __future__ import annotations

import codecs
import contextlib
import os
import tempfile
import zlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, fields
from itertools import islice
from typing import BinaryIO, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import __version__
from .dialect import EDIDetectionError
from .model import Event, InterchangeEvent, MessageEvent
from .output import Summary, dumps, event_to_dict
from .stream import StreamParser
from .tokenizer import Tokenizer

EVENT_TYPES = ("message", "interchange", "issue", "summary")
_FLUSH_BYTES = 1 << 16
_PROBE_BYTES = 1 << 20
_BATCH = 500  # events per worker-thread step for /v1/validate and /v1/detect
_MB = 1024 * 1024


class BodyError(ValueError):
    """The request body can't be read (corrupt or truncated gzip, decompressed size over the limit)."""


@dataclass(frozen=True)
class Settings:
    max_upload_bytes: int = 1024 * _MB
    max_decompressed_bytes: int = 4096 * _MB
    spool_memory_bytes: int = 16 * _MB
    chunk_bytes: int = 64 * 1024
    max_segment_bytes: int = 8 * _MB
    max_message_segments: int = 250_000
    max_issues: int = 1000  # per message/interchange in records, and in total for /v1/validate

    def __post_init__(self):
        for f in fields(self):
            value = getattr(self, f.name)
            if not isinstance(value, int) or value <= 0:
                raise ValueError(f"Setting {f.name} must be a positive integer, got {value!r}")

    @classmethod
    def from_env(cls) -> Settings:
        def read(name: str, default: int, unit: int = 1) -> int:
            raw = os.environ.get(name)
            if raw is None:
                return default
            try:
                return int(float(raw) * unit)
            except ValueError:
                raise ValueError(f"Environment variable {name}={raw!r} is not a number") from None

        return cls(max_upload_bytes=read("EDIPARSE_MAX_UPLOAD_MB", cls.max_upload_bytes, _MB),
                   max_decompressed_bytes=read("EDIPARSE_MAX_DECOMPRESSED_MB", cls.max_decompressed_bytes, _MB),
                   spool_memory_bytes=read("EDIPARSE_SPOOL_MEMORY_MB", cls.spool_memory_bytes, _MB),
                   chunk_bytes=read("EDIPARSE_CHUNK_KB", cls.chunk_bytes, 1024),
                   max_segment_bytes=read("EDIPARSE_MAX_SEGMENT_MB", cls.max_segment_bytes, _MB),
                   max_message_segments=read("EDIPARSE_MAX_MESSAGE_SEGMENTS", cls.max_message_segments),
                   max_issues=read("EDIPARSE_MAX_ISSUES", cls.max_issues))


class _Upload:
    """A spooled request body plus how to read it back (decompressed, in bounded chunks)."""

    def __init__(self, spool: BinaryIO, gzip: bool, settings: Settings):
        self.spool, self.gzip, self.settings = spool, gzip, settings

    def chunks(self) -> Iterator[bytes]:
        self.spool.seek(0)
        read = self.settings.chunk_bytes
        blocks = iter(lambda: self.spool.read(read), b"")
        if not self.gzip:
            yield from blocks
            return
        # Bounded decompression: never more than `read` bytes of output per step, so a compression
        # bomb can't produce one huge chunk. Handles multi-member gzip (cat a.gz b.gz) and detects truncation.
        total, cap = 0, self.settings.max_decompressed_bytes
        dec = zlib.decompressobj(wbits=31)
        try:
            for block in blocks:
                data = block
                while data:
                    out = dec.decompress(data, read)
                    total += len(out)
                    if total > cap:
                        raise BodyError(f"Decompressed body exceeds {cap} bytes")
                    if out:
                        yield out
                    if dec.eof:  # end of one gzip member; anything after it is the next member
                        data = dec.unused_data
                        dec = zlib.decompressobj(wbits=31) if data else dec
                    else:
                        data = dec.unconsumed_tail
            if not dec.eof:
                raise BodyError("Truncated gzip body")
        except zlib.error as e:
            raise BodyError(f"Body is not valid gzip data: {e}") from e

    def head(self, limit: int = _PROBE_BYTES) -> tuple[bytes, bool]:
        """The first ``limit`` decompressed bytes, and whether that is the whole body."""
        out = bytearray()
        for c in self.chunks():
            out += c
            if len(out) > limit:
                return bytes(out[:limit]), False
        return bytes(out), True

    def close(self) -> None:
        self.spool.close()


def _check_encoding(encoding: str) -> str:
    """422 unless ``encoding`` is "auto" or an ASCII-compatible codec (EDI delimiters are ASCII)."""
    if encoding == "auto":
        return encoding
    try:
        codecs.lookup(encoding)
        sample = "ISA*~'+:|^UNB\r\n"
        ok = sample.encode(encoding) == sample.encode("ascii")
    except (LookupError, UnicodeError):
        ok = False
    if not ok:
        raise HTTPException(422, f"Unsupported encoding {encoding!r}: use 'auto' or an ASCII-compatible codec "
                                 "such as latin-1 or cp1252")
    return encoding


async def _spool(request: Request, settings: Settings, encoding: str) -> _Upload:
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/"):
        raise HTTPException(415, "Send the EDI file as the raw request body (e.g. curl --data-binary @file.edi), "
                                 "not as multipart form data")
    content_encoding = request.headers.get("content-encoding", "identity").lower()
    if content_encoding not in ("identity", "gzip"):
        raise HTTPException(415, f"Unsupported Content-Encoding {content_encoding!r}; use gzip or none")
    declared = request.headers.get("content-length")
    if declared and declared.isascii() and declared.isdigit() and int(declared) > settings.max_upload_bytes:
        raise HTTPException(413, f"Upload exceeds {settings.max_upload_bytes} bytes")
    spool = tempfile.SpooledTemporaryFile(max_size=settings.spool_memory_bytes)  # noqa: SIM115 (closed by _Upload)
    size = 0
    try:
        async for chunk in request.stream():
            size += len(chunk)
            if size > settings.max_upload_bytes:
                raise HTTPException(413, f"Upload exceeds {settings.max_upload_bytes} bytes")
            try:
                spool.write(chunk)
            except OSError as e:  # e.g. /tmp full
                raise HTTPException(507, "Not enough temporary storage to accept this upload") from e
    except BaseException:
        spool.close()
        raise
    if size == 0:
        spool.close()
        raise HTTPException(400, "Empty request body")
    upload = _Upload(spool, content_encoding == "gzip", settings)
    try:
        await run_in_threadpool(_probe, upload, encoding)  # CPU work stays off the event loop
    except BodyError as e:
        upload.close()
        raise HTTPException(400, str(e)) from e
    except EDIDetectionError as e:
        upload.close()
        raise HTTPException(422, f"Not usable EDI: {e}") from e
    return upload


def _probe(upload: _Upload, encoding: str) -> None:
    """Tokenize the start of the body so unusable input is a 422 *before* a 200 stream begins.

    Raises EDIDetectionError if there is no header in the first 1 MiB, or the only headers are unreadable.
    Problems further into a large body are reported in-band as records.
    """
    head, complete = upload.head()
    text = head.decode("utf-8" if encoding == "auto" else encoding, "surrogateescape")
    t = Tokenizer(keep_prefix=False)
    t.feed(text)
    if complete:
        t.close()
    elif not t.started:
        raise EDIDetectionError(f"No EDI interchange header in the first {_PROBE_BYTES} bytes")


def _events(upload: _Upload, encoding: str, settings: Settings) -> Iterator[Event]:
    parser = StreamParser(encoding, max_segment=settings.max_segment_bytes,
                          max_message_segments=settings.max_message_segments, max_issues=settings.max_issues)
    try:
        for chunk in upload.chunks():
            yield from parser.feed(chunk)
        yield from parser.close()
    finally:
        upload.close()


async def _consume(request: Request, upload: _Upload, encoding: str, settings: Settings,
                   handle: Callable[[Event], None]) -> None:
    """Run the parse in worker-thread batches, stopping early if the client disconnects."""
    gen = _events(upload, encoding, settings)
    try:
        while True:
            batch = await run_in_threadpool(lambda: list(islice(gen, _BATCH)))
            for ev in batch:
                handle(ev)
            if len(batch) < _BATCH or await request.is_disconnected():
                break
    except BodyError as e:
        raise HTTPException(400, str(e)) from e
    except EDIDetectionError as e:
        raise HTTPException(422, f"Not usable EDI: {e}") from e
    finally:
        upload.close()
        with contextlib.suppress(ValueError):
            gen.close()


def _parse_types(include: str) -> set[str]:
    types = {t.strip() for t in include.split(",") if t.strip()}
    if bad := types - set(EVENT_TYPES):
        raise HTTPException(422, f"Unknown event types {sorted(bad)}; choose from {list(EVENT_TYPES)}")
    return types


ENCODING_HELP = "auto = UTF-8 with invalid bytes read as Latin-1; or an ASCII-compatible codec (latin-1, cp1252)"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="Universal EDI Parser",
        version=__version__,
        description=(
            "Parse any X12, EDIFACT, TRADACOMS or HL7 v2 file with no mappings. POST the file as the raw "
            "request body; `/v1/parse` streams back one JSON object per line (NDJSON), one per business "
            "document, followed by interchange and summary records."
        ),
    )

    @app.get("/healthz", tags=["ops"])
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/v1/info", tags=["ops"])
    def info() -> dict:
        return {"name": "ediparse", "version": __version__,
                "standards": ["X12", "EDIFACT", "TRADACOMS", "HL7"],
                "event_types": list(EVENT_TYPES),
                "limits": {f.name: getattr(settings, f.name) for f in fields(settings)}}

    @app.post(
        "/v1/parse", tags=["parse"],
        summary="Stream parsed documents",
        response_class=StreamingResponse,
        responses={200: {"description": "NDJSON stream (or JSON array with format=json)",
                         "content": {"application/x-ndjson": {}, "application/json": {}}},
                   400: {"description": "Empty body or unreadable gzip"}, 413: {"description": "Upload too large"},
                   415: {"description": "Unsupported body"}, 422: {"description": "Not EDI, or bad parameter"},
                   507: {"description": "Not enough temporary storage"}},
        openapi_extra={"requestBody": {"required": True, "content": {
            "application/octet-stream": {"schema": {"type": "string", "format": "binary"}},
            "text/plain": {"schema": {"type": "string"}}}}},
    )
    async def parse(
        request: Request,
        format: Literal["ndjson", "json"] = Query("ndjson", description="ndjson streams one object per line; "
                                                  "json wraps the same objects in an array"),
        include: str = Query("message,interchange,issue,summary",
                             description="Comma-separated event types to return"),
        segments: bool = Query(True, description="false returns document metadata only (no segment bodies)"),
        encoding: str = Query("auto", description=ENCODING_HELP),
    ):
        types = _parse_types(include)
        encoding = _check_encoding(encoding)
        upload = await _spool(request, settings, encoding)

        def records() -> Iterator[dict]:
            summary = Summary()
            try:
                for ev in _events(upload, encoding, settings):
                    summary.add(ev)
                    if ev.kind in types:
                        yield event_to_dict(ev, segments)
                if "summary" in types:
                    yield summary.to_dict()
            except Exception as e:  # headers are already sent; report in-band
                yield {"type": "error", "message": str(e), "error": type(e).__name__}

        def body() -> Iterator[bytes]:
            # Batch lines into ~64 KiB writes: one thread hop per write, not per document.
            buf, size, first = [b"["] if format == "json" else [], 0, True
            for rec in records():
                chunk = _frame(dumps(rec), format, first)
                first = False
                buf.append(chunk)
                size += len(chunk)
                if size >= _FLUSH_BYTES:
                    yield b"".join(buf)
                    buf, size = [], 0
            if format == "json":
                buf.append(b"]\n")
            if buf:
                yield b"".join(buf)

        media = "application/x-ndjson" if format == "ndjson" else "application/json"
        gen = body()  # a sync generator: Starlette runs each step in a worker thread
        return _StreamingResponse(gen, cleanup=lambda: _release(gen, upload), media_type=media)

    @app.post("/v1/validate", tags=["parse"], summary="Check envelopes and control totals")
    async def validate(request: Request, encoding: str = Query("auto", description=ENCODING_HELP)) -> JSONResponse:
        encoding = _check_encoding(encoding)
        upload = await _spool(request, settings, encoding)
        summary, issues, total = Summary(), [], 0

        def handle(ev: Event) -> None:
            nonlocal total
            summary.add(ev)
            if isinstance(ev, MessageEvent):
                found = [{**i.to_dict(), "message_type": ev.message.type, "message_control": ev.message.control}
                         for i in ev.message.issues]
            elif isinstance(ev, InterchangeEvent):
                found = [{**i.to_dict(), "interchange_control": ev.interchange.control}
                         for i in ev.interchange.issues]
            else:
                found = [ev.issue.to_dict()]
            total += len(found)
            issues.extend(found[:max(0, settings.max_issues - len(issues))])

        await _consume(request, upload, encoding, settings, handle)
        result = summary.to_dict()
        result.pop("type")
        return JSONResponse({**result, "issues": issues, "issues_truncated": total > len(issues)})

    @app.post("/v1/detect", tags=["parse"], summary="Identify standard, version and delimiters")
    async def detect(request: Request, encoding: str = Query("auto", description=ENCODING_HELP)) -> JSONResponse:
        encoding = _check_encoding(encoding)
        upload = await _spool(request, settings, encoding)
        out: list[dict] = []

        def handle(ev: Event) -> None:
            if isinstance(ev, InterchangeEvent):
                ic = ev.interchange
                out.append({**ic.dialect.describe(), "control": ic.control, "sender": ic.sender,
                            "receiver": ic.receiver, "messages": ic.message_count})

        await _consume(request, upload, encoding, settings, handle)
        return JSONResponse({"interchanges": out})

    return app


class _StreamingResponse(StreamingResponse):
    """StreamingResponse that always runs ``cleanup``: on completion, on error, and on client disconnect.

    Without this, a client hanging up mid-stream left the generator suspended, so the spooled upload
    was never closed and its /tmp space was never released (found by disconnect testing).
    """

    def __init__(self, content, cleanup, **kwargs):
        super().__init__(content, **kwargs)
        self._cleanup = cleanup

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self._cleanup()


def _release(gen, upload: _Upload) -> None:
    upload.close()  # frees the spool even if the generator is still running in a worker thread
    # ValueError = "generator already executing": it stops on its next read of the closed spool.
    with contextlib.suppress(ValueError):
        gen.close()


def _frame(line: str, format: str, first: bool) -> bytes:
    if format == "json":
        return (("" if first else ",") + "\n" + line).encode("utf-8")
    return (line + "\n").encode("utf-8")


def __getattr__(name: str):
    # `uvicorn ediparse.service:app` (used for multi-worker runs) builds the app lazily from env settings.
    if name == "app":
        return create_app()
    raise AttributeError(name)
