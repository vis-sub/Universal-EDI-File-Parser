"""HTTP service: POST an EDI file, get back one JSON object per business document as a stream.

Run locally:   ediparse serve            (needs `pip install "ediparse[service]"`)
Run in Docker: docker compose up

Uploads are written to a spool (memory up to EDIPARSE_SPOOL_MEMORY_MB, then a temp
file), and the response streams NDJSON as documents are parsed. Memory per request
stays bounded by the spool limit plus the largest single message, whatever the file
size. Results start after the upload finishes. Answering while the upload is still
running can deadlock clients that don't read until their upload completes, so the
service doesn't do it.
"""
from __future__ import annotations

import contextlib
import os
import tempfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from typing import BinaryIO, Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool

from . import __version__
from .dialect import EDIDetectionError
from .model import InterchangeEvent, MessageEvent
from .output import Summary, dumps, event_to_dict
from .stream import StreamParser
from .tokenizer import Tokenizer

EVENT_TYPES = ("message", "interchange", "issue", "summary")
_FLUSH_BYTES = 1 << 16


@dataclass(frozen=True)
class Settings:
    max_upload_bytes: int = 1024 * 1024 * 1024
    spool_memory_bytes: int = 16 * 1024 * 1024
    chunk_bytes: int = 1 << 16
    max_issues: int = 1000  # cap for /v1/validate responses

    @classmethod
    def from_env(cls) -> Settings:
        def mb(name: str, default: int) -> int:
            return int(float(os.environ.get(name, default / 1024 / 1024)) * 1024 * 1024)
        return cls(max_upload_bytes=mb("EDIPARSE_MAX_UPLOAD_MB", cls.max_upload_bytes),
                   spool_memory_bytes=mb("EDIPARSE_SPOOL_MEMORY_MB", cls.spool_memory_bytes),
                   chunk_bytes=int(os.environ.get("EDIPARSE_CHUNK_KB", cls.chunk_bytes // 1024)) * 1024,
                   max_issues=int(os.environ.get("EDIPARSE_MAX_ISSUES", cls.max_issues)))


class _Upload:
    """A spooled request body plus how to read it back decompressed."""

    def __init__(self, spool: BinaryIO, gzip: bool, chunk: int):
        self.spool, self.gzip, self.chunk = spool, gzip, chunk

    def chunks(self) -> Iterator[bytes]:
        self.spool.seek(0)
        dec = zlib.decompressobj(wbits=47) if self.gzip else None  # 47 = auto-detect gzip/zlib header
        for block in iter(lambda: self.spool.read(self.chunk), b""):
            yield dec.decompress(block) if dec else block
        if dec:
            yield dec.flush()

    def head(self, limit: int = 1 << 20) -> tuple[bytes, bool]:
        """The first ``limit`` decompressed bytes, and whether that is the whole body."""
        out = bytearray()
        for c in self.chunks():
            out += c
            if len(out) > limit:
                return bytes(out[:limit]), False
        return bytes(out), True

    def close(self) -> None:
        self.spool.close()


async def _spool(request: Request, settings: Settings) -> _Upload:
    ctype = request.headers.get("content-type", "")
    if ctype.startswith("multipart/"):
        raise HTTPException(415, "Send the EDI file as the raw request body (e.g. curl --data-binary @file.edi), "
                                 "not as multipart form data")
    encoding = request.headers.get("content-encoding", "identity").lower()
    if encoding not in ("identity", "gzip", "deflate"):
        raise HTTPException(415, f"Unsupported Content-Encoding {encoding!r}; use gzip or none")
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
            spool.write(chunk)
    except BaseException:
        spool.close()
        raise
    if size == 0:
        spool.close()
        raise HTTPException(400, "Empty request body")
    upload = _Upload(spool, encoding != "identity", settings.chunk_bytes)
    try:
        head, complete = upload.head()
    except zlib.error as e:
        upload.close()
        raise HTTPException(400, f"Body is not valid {encoding} data: {e}") from e
    try:
        _probe(head, complete)
    except EDIDetectionError as e:
        upload.close()
        raise HTTPException(422, f"Not usable EDI: {e}") from e
    return upload


def _probe(head: bytes, complete: bool) -> None:
    """Tokenize the start of the body so unusable input is a 422 *before* a 200 stream begins.

    Raises EDIDetectionError if there is no header, or the only headers are unreadable.
    (Problems further into a large body are reported in-band as records.)
    """
    t = Tokenizer(keep_prefix=False)
    t.feed(head.decode("utf-8", "surrogateescape"))
    if complete:
        t.close()


def _events(upload: _Upload, encoding: str) -> Iterator:
    parser = StreamParser(encoding)
    try:
        for chunk in upload.chunks():
            yield from parser.feed(chunk)
        yield from parser.close()
    finally:
        upload.close()


def _parse_types(include: str) -> set[str]:
    types = {t.strip() for t in include.split(",") if t.strip()}
    if bad := types - set(EVENT_TYPES):
        raise HTTPException(422, f"Unknown event types {sorted(bad)}; choose from {list(EVENT_TYPES)}")
    return types


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
                "limits": {"max_upload_bytes": settings.max_upload_bytes,
                           "spool_memory_bytes": settings.spool_memory_bytes}}

    @app.post(
        "/v1/parse", tags=["parse"],
        summary="Stream parsed documents",
        response_class=StreamingResponse,
        responses={200: {"description": "NDJSON stream (or JSON array with format=json)",
                         "content": {"application/x-ndjson": {}, "application/json": {}}},
                   413: {"description": "Upload too large"}, 415: {"description": "Unsupported body"},
                   422: {"description": "Not EDI"}},
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
        encoding: str = Query("auto", description="Text encoding; auto = UTF-8 with Latin-1 fallback"),
    ):
        types = _parse_types(include)
        upload = await _spool(request, settings)

        def records() -> Iterator[dict]:
            summary = Summary()
            try:
                for ev in _events(upload, encoding):
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
    async def validate(request: Request, encoding: str = "auto") -> JSONResponse:
        upload = await _spool(request, settings)

        def run() -> dict:
            summary, issues, total = Summary(), [], 0
            for ev in _events(upload, encoding):
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
                issues += found[:max(0, settings.max_issues - len(issues))]
            result = summary.to_dict()
            result.pop("type")
            return {**result, "issues": issues, "issues_truncated": total > len(issues)}

        try:
            return JSONResponse(await run_in_threadpool(run))
        except EDIDetectionError as e:
            raise HTTPException(422, str(e)) from e

    @app.post("/v1/detect", tags=["parse"], summary="Identify standard, version and delimiters")
    async def detect(request: Request) -> JSONResponse:
        upload = await _spool(request, settings)

        def run() -> list[dict]:
            return [{**ev.interchange.dialect.describe(), "control": ev.interchange.control,
                     "sender": ev.interchange.sender, "receiver": ev.interchange.receiver,
                     "messages": ev.interchange.message_count}
                    for ev in _events(upload, "auto") if isinstance(ev, InterchangeEvent)]

        try:
            return JSONResponse({"interchanges": await run_in_threadpool(run)})
        except EDIDetectionError as e:
            raise HTTPException(422, str(e)) from e

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
