# Changelog

## 0.3.0 — first public release

### Added
- Mutation fuzzer (`tests/fuzzing.py`, `scripts/fuzz.py`, `scripts/fuzz_service.py`) and JSON Schema for output
  records (`docs/schemas/records.schema.json`), both enforced in tests.
- Recovery from damaged interchange headers: `bad_header` issue, resync at the next header.
- Kubernetes `base` + `overlays/local` + `overlays/production`; Makefile; smoke-test script.
- Pinned service dependencies (`constraints-service.txt`), OS patching in the image, CI Trivy gate, ruff.
- Full documentation set under `docs/`, including a comparison with open-source EDI tools (`docs/comparison.md`)
  and a phased roadmap: meaning (loops, labels), acknowledgments and EDI writing, validation, speed and reach.

### Changed
- **Breaking:** service bind settings renamed `EDIPARSE_HOST`/`EDIPARSE_PORT` → `EDIPARSE_HTTP_HOST`/
  `EDIPARSE_HTTP_PORT` (Kubernetes injects `EDIPARSE_PORT` for a Service named `ediparse`).
- Non-UTF-8 input is decoded identically regardless of chunking; invalid bytes are shown as Latin-1
  (`invalid_utf8` notice replaces `encoding_fallback`).
- Interchange headers are recognized only at the start of a token.
- The service answers 422 for input whose only header is unreadable.
- Container runs as numeric UID 10001.

### Fixed (from an independent pre-publication code review and docs audit)
- Unbounded memory and quadratic time on segments with no terminator, runs of empty segments, long whitespace,
  HL7 terminator search and damaged regions. New limits: `max_segment` / `EDIPARSE_MAX_SEGMENT_MB`,
  `max_message_segments` / `EDIPARSE_MAX_MESSAGE_SEGMENTS`, `max_issues`, `EDIPARSE_MAX_DECOMPRESSED_MB`;
  new issue codes `oversized_segment`, `message_truncated`, `too_many_issues`.
- Gzip: bounded output per step, multi-member bodies, truncation detection. `deflate` is no longer accepted.
- Service: pre-check runs off the event loop; bad `encoding` is 422 everywhere; full `/tmp` is 507; large non-EDI
  bodies are 422; validate/detect stop when the client disconnects; settings validated at startup.
- Header false positives in leading junk (`UNAVAILABLE`, `ISA,`); HL7 encoding-character defaults, BHS group type,
  MSH-12 version; UNA repetition before syntax 4; MLLP framing tolerated; CLI shorthand and broken pipe.
- Crash on non-ASCII digits in control counts and Content-Length.
- Spooled uploads leaked when clients disconnected mid-stream.
- JSON encoding crash for non-UTF-8 bytes in tags/delimiters.
- Several chunking-dependent tokenizer behaviours (see docs/testing.md#bugs-found-by-testing).

## 0.2.0
- Streaming parser and events, HTTP service (FastAPI) with NDJSON, Docker, Compose + nginx load balancer,
  Kubernetes manifests, release workflow, parallel client, deployment guide.

## 0.1.0
- Dialect detection, tokenizer, envelope tree, control checks, CLI, X12 document catalog and registry, samples.
