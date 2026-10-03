# Running ediparse as a service

The service takes an EDI file over HTTP and streams back **one JSON object per business document** (X12
ST/SE, EDIFACT UNH/UNT, TRADACOMS MHD/MTR, HL7 MSH), followed by interchange and summary records. Your
application reads the response line by line and processes each document as it arrives.

```
your app ──POST raw EDI──▶ ediparse ──NDJSON──▶ {"type":"message", ...}   ← 850 #1
                                                 {"type":"message", ...}   ← 850 #2
                                                 {"type":"interchange", ...}
                                                 {"type":"summary", ...}
```

## Start it

```bash
docker compose up --build                 # nginx + 2 instances on http://localhost:8080, docs at /docs
# or, without Docker:
pip install -e ".[service]" && ediparse serve --host 0.0.0.0 --port 8080
```

## Endpoints

| Method & path | Returns |
|---|---|
| `POST /v1/parse` | NDJSON stream of documents (main endpoint) |
| `POST /v1/validate` | One JSON object: totals plus every envelope or control-number issue |
| `POST /v1/detect` | Standard, version, delimiters, sender/receiver and message count per interchange |
| `GET /healthz` | `{"status": "ok"}` for load balancers |
| `GET /v1/info` | Version, supported standards, configured limits |
| `GET /docs`, `/openapi.json` | Interactive OpenAPI docs / spec (use it to generate clients) |

### Request

Send the file as the **raw request body**, not as multipart form data:

```bash
curl --data-binary @orders.edi http://localhost:8080/v1/parse
gzip -c big_837.edi | curl --data-binary @- -H "Content-Encoding: gzip" http://localhost:8080/v1/parse
```

`Content-Encoding: gzip` is supported, including multi-member gzip (`cat a.gz b.gz`). Truncated or corrupt gzip gets `400`. Other encodings get `415`.

| Query parameter | Default | Meaning |
|---|---|---|
| `format` | `ndjson` | `ndjson` gives one object per line. `json` gives the same objects in a JSON array, still streamed |
| `include` | `message,interchange,issue,summary` | Which record types to return |
| `segments` | `true` | `false` returns document metadata only, which is much smaller. Use it for routing or indexing |
| `encoding` | `auto` | `auto` means UTF-8, with invalid bytes read as Latin-1. Or an ASCII-compatible codec (`latin-1`, `cp1252`, …). UTF-16/32 and unknown codecs get `422` |

### Response records

| Record | When |
|---|---|
| `message` | One per business document, as each completes. This is what most consumers act on |
| `interchange` | When each interchange closes: delimiters, counts, envelope issues |
| `issue` | A problem outside any interchange (stray segments, junk before the first header, damaged header between interchanges) |
| `summary` | Always last. **A response without one is incomplete** |
| `error` | Only if parsing fails unexpectedly after the 200 has started |

```json
{"type": "message", "sequence": 1,
 "interchange": {"standard": "X12", "version": "00501", "control": "000000001", "sender": "SENDERID", "receiver": "RECEIVERID", "date": "261003"},
 "group": {"type": "PO", "control": "1", "version": "005010"},
 "message": {"type": "850", "control": "0001", "version": "005010", "segment_count": 21,
             "header": {"tag": "ST", "index": 2, "elements": {"ST01": "850", "ST02": "0001"}},
             "trailer": {"tag": "SE", "index": 22, "elements": {"SE01": "21", "SE02": "0001"}},
             "segments": [{"tag": "BEG", "index": 3, "elements": {"BEG01": "00", "BEG02": "SA", "BEG03": "PO-10045", "BEG05": "20261001"}}, "..."]},
 "valid": true, "issues": []}
```

**[Output format](output-format.md)** documents every record and field, value encoding (composites, repeats,
escapes) and all issue codes. A machine-readable [JSON Schema](schemas/records.schema.json) is available for client
generation and validation.

### Errors

| When | What you get |
|---|---|
| Body is empty, or truncated/corrupt gzip, or decompresses past the limit (`400`); too large (`413`); multipart or unsupported `Content-Encoding` (`415`); bad `encoding`/`include` parameter (`422`); `/tmp` full (`507`) | HTTP error with `{"detail": "..."}`, before any records |
| No usable EDI: no header, or the only header is unreadable | HTTP `422` with `{"detail": "Not usable EDI: ..."}`. The service tokenizes the first 1 MiB before answering |
| A document has envelope problems (wrong SE count, missing trailer…) | Still HTTP 200. That document has `"valid": false` and `issues` entries, and the stream continues |
| A later interchange header is damaged | Still HTTP 200. A `bad_header` issue says what was skipped, and parsing resumes at the next header |
| A failure after streaming has started (e.g. gzip corruption or the decompressed-size limit beyond the first 1 MiB) | A final `{"type":"error", ...}` record. The HTTP status is already 200, so **check for a `summary` record** before treating a response as complete |

## Consuming the stream

Any HTTP client that can read a response incrementally works.

```bash
# curl + jq: list every document
curl -s --data-binary @batch.edi "localhost:8080/v1/parse?segments=false" \
  | jq -c 'select(.type=="message") | {type: .message.type, control: .message.control, valid}'
```

- **Python, standard library only:** [`examples/stream_client.py`](../examples/stream_client.py). It streams the upload from disk and yields each record.
- **In-process, no HTTP:** if your service is in Python, call the library directly. It's the same parser and gives the same JSON:

  ```python
  from ediparse import stream, event_to_dict
  for event in stream("batch.edi"):
      if event.kind == "message":
          publish(event_to_dict(event))
  ```

## How memory stays bounded

1. **Upload:** the request body is copied to a spool. It stays in memory up to `EDIPARSE_SPOOL_MEMORY_MB` and spills to a temp file beyond that.
2. **Check:** the first 1 MiB is decompressed and tokenized in a worker thread. A body with no usable EDI header gets `422` here, before any records.
3. **Parse:** the spool is read in 64 KiB chunks (gzip output too) by the incremental parser. Only the document currently being parsed is held in memory.
4. **Respond:** each document is serialized as soon as it completes and written out in ~64 KiB batches. It is then dropped.

**Results start once the upload finishes.** Answering while the upload is still in progress (full duplex) is not
used, because most HTTP clients don't read the response until they finish uploading. The JSON output is larger than
the EDI input, so both sides would block waiting on each other. The temp file is deleted when the response ends,
including when the client disconnects.

Measured on a laptop: a 47 MB file of 150,000 837 claims returns 150,000 records.

| Output | Time (one request) | Server memory |
|---|---|---|
| `segments=false` | ≈ 10 s | ≈ 61 MB per process, steady |
| Full output (293 MB of JSON) | ≈ 29 s | ≈ 61 MB per process, steady |

[Operations](operations.md#performance-measured) has more measurements, including concurrent load.

**A single large document is held in memory until it completes** (one ST/SE with thousands of claims), at roughly
0.5–1 KB per segment. `EDIPARSE_MAX_MESSAGE_SEGMENTS` caps it (see below). Segments, decompressed size and issues
are capped too, so no single request can grow without limit.

## Configuration

| Environment variable | Default | Purpose |
|---|---|---|
| `WEB_CONCURRENCY` | `1` (compose/k8s: `2`) | Worker processes per instance. Parsing is CPU-bound, so use about one per core |
| `EDIPARSE_MAX_UPLOAD_MB` | `1024` | Larger uploads are rejected with `413` |
| `EDIPARSE_SPOOL_MEMORY_MB` | `16` | Per-request upload memory before spilling to `/tmp` |
| `EDIPARSE_MAX_DECOMPRESSED_MB` | `4096` | Gzip bodies that decompress to more than this are rejected (400, or an in-band `error` record once streaming has started) |
| `EDIPARSE_MAX_SEGMENT_MB` | `8` | A segment longer than this (or with no terminator) is reported as `oversized_segment` and skipped up to the next interchange header |
| `EDIPARSE_MAX_MESSAGE_SEGMENTS` | `250000` | Body segments kept per document. Later ones are counted (control totals stay correct) but not returned, and the document gets `message_truncated` |
| `EDIPARSE_MAX_ISSUES` | `1000` | Issues kept per document/interchange (then one `too_many_issues`), and the cap on issues listed by `/v1/validate` |
| `EDIPARSE_CHUNK_KB` | `64` | Read size for the parser and for gzip output steps |
| `EDIPARSE_HTTP_HOST` / `EDIPARSE_HTTP_PORT` | `127.0.0.1` / `8080` (`0.0.0.0` in Docker) | Bind address |

Size `/tmp` to roughly *concurrent requests × largest expected upload*; when it fills, uploads get `507`. The compose file mounts a 2 GB tmpfs. Bytes on a tmpfs
count as container memory; see [Operations → sizing](operations.md#sizing).

The bind variables are deliberately not `EDIPARSE_PORT`/`EDIPARSE_HOST`: Kubernetes injects `EDIPARSE_PORT=tcp://…`
into pods when a Service is named `ediparse`.

## Deploying

See **[deployment.md](deployment.md)**. It covers how scaling works, Docker Compose with a load balancer, Kubernetes
manifests, ECS, Cloud Run, Container Apps, VMs, running the library in workers or functions with no service, parallel
clients, and a security checklist.

## Roadmap

See [roadmap.md](roadmap.md).
