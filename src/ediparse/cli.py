"""Command line: ediparse [parse|detect|validate|serve] ..."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections.abc import Iterator

from .dialect import EDIDetectionError
from .model import Event, InterchangeEvent, MessageEvent
from .output import CSV_COLUMNS, Summary, csv_rows, document_to_dict, event_to_dict, tree_lines
from .parser import parse_bytes
from .stream import stream

COMMANDS = ("parse", "detect", "validate", "serve")


def _events(path: str) -> Iterator[Event]:
    return stream(sys.stdin.buffer if path == "-" else path)


def _read(path: str) -> bytes:
    return sys.stdin.buffer.read() if path == "-" else open(path, "rb").read()


def _fmt_issue(f: str, i, ctx: str = "") -> str:
    where = f" (segment {i.segment_index})" if i.segment_index is not None else ""
    return f"{f}: {i.severity}: [{i.code}] {i.message}{where}{ctx}"


def _serve(args) -> int:
    try:
        import uvicorn
    except ImportError:
        print('The service needs extra dependencies: pip install "ediparse[service]"', file=sys.stderr)
        return 2
    workers = args.workers or int(os.environ.get("WEB_CONCURRENCY", "1"))
    if workers > 1:
        uvicorn.run("ediparse.service:app", host=args.host, port=args.port, workers=workers)
    else:
        from .service import create_app
        uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and not set(argv) & {*COMMANDS, "-h", "--help"}:
        argv.insert(0, "parse")  # `ediparse file.edi` is shorthand for `ediparse parse file.edi`

    ap = argparse.ArgumentParser(prog="ediparse", description="Read any X12, EDIFACT, TRADACOMS or HL7 v2 file.")
    sub = ap.add_subparsers(dest="command", required=True)
    p = sub.add_parser("parse", help="parse files (streams by default)")
    p.add_argument("files", nargs="+", help="input files, or - for stdin")
    p.add_argument("-f", "--format", choices=("ndjson", "csv", "json", "tree"), default="ndjson",
                   help="ndjson/csv stream with flat memory; json/tree load the whole file to build a full tree")
    p.add_argument("--no-segments", action="store_true", help="ndjson: document metadata only, no segment bodies")
    p.add_argument("-o", "--output", help="write to this file instead of stdout")
    d = sub.add_parser("detect", help="print the standard, version and delimiters of each interchange")
    d.add_argument("files", nargs="+")
    v = sub.add_parser("validate", help="report envelope/control-number issues; exit 1 on errors")
    v.add_argument("files", nargs="+")
    s = sub.add_parser("serve", help='run the HTTP service (needs "ediparse[service]")')
    s.add_argument("--host", default=os.environ.get("EDIPARSE_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("EDIPARSE_PORT", "8080")))
    s.add_argument("--workers", type=int, help="worker processes (default: $WEB_CONCURRENCY or 1)")
    args = ap.parse_args(argv)

    if args.command == "serve":
        return _serve(args)

    out = open(args.output, "w", newline="", encoding="utf-8") if getattr(args, "output", None) else sys.stdout
    status = 0
    try:
        fmt = getattr(args, "format", None)
        writer = csv.writer(out) if fmt == "csv" else None
        if writer:
            writer.writerow(["file", *CSV_COLUMNS])
        json_docs = []
        for f in args.files:
            try:
                if args.command == "detect":
                    for ev in _events(f):
                        if isinstance(ev, InterchangeEvent):
                            ic = ev.interchange
                            print(json.dumps({"file": f, **ic.dialect.describe(), "control": ic.control}), file=out)
                elif args.command == "validate":
                    summary = Summary()
                    for ev in _events(f):
                        summary.add(ev)
                        if isinstance(ev, MessageEvent):
                            m = ev.message
                            lines = [_fmt_issue(f, i, f" in {m.type} {m.control}") for i in m.issues]
                        elif isinstance(ev, InterchangeEvent):
                            lines = [_fmt_issue(f, i) for i in ev.interchange.issues]
                        else:
                            lines = [_fmt_issue(f, ev.issue)]
                        for line in lines:
                            print(line, file=out)
                    if summary.errors:
                        status = max(status, 1)
                    else:
                        print(f"{f}: OK ({summary.messages} messages)", file=out)
                elif fmt == "ndjson":
                    summary = Summary()
                    for ev in _events(f):
                        summary.add(ev)
                        print(json.dumps({"file": f, **event_to_dict(ev, not args.no_segments)},
                                         ensure_ascii=False), file=out)
                    print(json.dumps({"file": f, **summary.to_dict()}), file=out)
                elif fmt == "csv":
                    writer.writerows([f, *row] for row in csv_rows(_events(f)))
                elif fmt == "json":
                    json_docs.append({"file": f, **document_to_dict(parse_bytes(_read(f)))})
                else:
                    print(f"== {f}", file=out)
                    for line in tree_lines(parse_bytes(_read(f))):
                        print(line, file=out)
            except (EDIDetectionError, OSError) as e:
                print(f"{f}: {e}", file=sys.stderr)
                status = 2
        if json_docs:
            json.dump(json_docs[0] if len(json_docs) == 1 else json_docs, out, indent=2, ensure_ascii=False)
            out.write("\n")
    finally:
        if out is not sys.stdout:
            out.close()
    return status
