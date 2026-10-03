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

| Query parameter | Default | Meaning |
|---|---|---|
| `format` | `ndjson` | `ndjson` gives one object per line. `json` gives the same objects in a JSON array, still streamed |
| `include` | `message,interchange,issue,summary` | Which record types to return |
| `segments` | `true` | `false` returns document metadata only, which is much smaller. Use it for routing or indexing |
| `encoding` | `auto` | `auto` means UTF-8 with Latin-1 fallback. Any Python codec name is also accepted |

### Response records

**`message`**: one per business document. This is the object most consumers act on.

```json
{
  "type": "message",
  "sequence": 1,
  "interchange": {"standard": "X12", "version": "00501", "control": "000000001",
                  "sender": "SENDERID", "receiver": "RECEIVERID", "date": "261003"},
  "group": {"type": "PO", "control": "1", "version": "005010"},
  "message": {
    "type": "850", "control": "0001", "version": "005010", "segment_count": 21,
    "header":  {"tag": "ST", "index": 2, "elements": {"ST01": "850", "ST02": "0001"}},
    "trailer": {"tag": "SE", "index": 22, "elements": {"SE01": "21", "SE02": "0001"}},
    "segments": [
      {"tag": "BEG", "index": 3, "elements": {"BEG01": "00", "BEG02": "SA", "BEG03": "PO-10045", "BEG05": "20261001"}},
      {"tag": "PO1", "index": 15, "elements": {"PO101": "1", "PO102": "24", "PO103": "EA", "PO104": "12.50", "...": "..."}},
      {"tag": "SV1", "index": 27, "elements": {"SV101": ["HC", "99213"], "SV102": "100"}},
      {"tag": "EB",  "index": 12, "elements": {"EB03": {"repeats": ["30", "1", "98"]}}}
    ]
  },
  "valid": true,
  "issues": []
}
```

How element values are encoded:
- A plain value is a string.
- A composite (sub-element) value is a list of components.
- A repeated element is `{"repeats": [...]}`.
- Empty elements are left out, and keys keep their positional IDs (`NM103`, `PID-5`).
- Escapes are resolved: EDIFACT `?+` becomes `+`, and HL7 `\T\` becomes `&`.

**`interchange`**: emitted when an interchange closes. It contains the header and trailer, delimiters, group and message counts, and envelope issues (for example a wrong `IEA01`).

**`issue`**: a problem outside any interchange, such as stray segments or junk before the first header.

**`summary`**: always last: `{"type":"summary","interchanges":1,"messages":2,"errors":0,"warnings":0,"valid":true}`.

**`error`**: only if parsing fails after streaming has started (see below).

### Errors

| When | What you get |
|---|---|
| Body is empty, not EDI, too large, multipart, or bad gzip | HTTP `400` / `413` / `415` / `422` with `{"detail": "..."}`, before any records |
| A document has envelope problems (wrong SE count, missing trailer…) | Still HTTP 200. That document has `"valid": false` and `issues` entries, and the stream continues |
| An unexpected failure mid-stream | A final `{"type":"error", ...}` record. The HTTP status is already 200, so **check for a `summary` record** before treating a response as complete |

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

## How memory stays flat

1. **Upload:** the request body is copied to a spool. It stays in memory up to `EDIPARSE_SPOOL_MEMORY_MB` and spills to a temp file beyond that.
2. **Parse:** the spool is read in 64 KiB chunks by the incremental parser. Only the document currently being parsed is held in memory.
3. **Respond:** each document is serialized as soon as it completes and written out in ~64 KiB batches. It is then dropped.

**Results start once the upload finishes.** Answering while the upload is still in progress (full duplex) is not
used, because most HTTP clients don't read the response until they finish uploading. The JSON output is larger than
the EDI input, so both sides would block waiting on each other. The temp file is deleted when the response ends.

Measured on a laptop (single worker): a 47 MB file of 150,000 837 claims returns 150,000 records.

| Output | Time | Server memory |
|---|---|---|
| `segments=false` | ≈ 9 s | ≈ 61 MB (steady) |
| Full output (293 MB of JSON) | ≈ 29 s | ≈ 61 MB (steady) |

The one exception to flat memory: a single huge document (one ST/SE holding thousands of claims) is held in memory
in full, because it is returned as one object.

## Configuration

| Environment variable | Default | Purpose |
|---|---|---|
| `WEB_CONCURRENCY` | `1` (compose/k8s: `2`) | Worker processes per instance. Parsing is CPU-bound, so use about one per core |
| `EDIPARSE_MAX_UPLOAD_MB` | `1024` | Larger uploads are rejected with `413` |
| `EDIPARSE_SPOOL_MEMORY_MB` | `16` | Per-request upload memory before spilling to `/tmp` |
| `EDIPARSE_CHUNK_KB` | `64` | Parser read size |
| `EDIPARSE_MAX_ISSUES` | `1000` | Cap on issues listed by `/v1/validate` |
| `EDIPARSE_HOST` / `EDIPARSE_PORT` | `127.0.0.1` / `8080` (`0.0.0.0` in Docker) | Bind address |

Size `/tmp` to roughly *concurrent requests × max upload*. The compose file mounts a 2 GB tmpfs.

## Deploying

See **[deployment.md](deployment.md)**. It covers how scaling works, Docker Compose with a load balancer, Kubernetes
manifests, ECS, Cloud Run, Container Apps, VMs, running the library in workers or functions with no service, parallel
clients, and a security checklist.

## Roadmap

- Loop resolution and field labels (e.g. `NM1*85` → "Billing Provider"), as described in the [document catalog](document-catalog.md)
- Async job API (`POST /v1/jobs` → poll or webhook) for very large batches
- Splitting very large single messages (e.g. per 837 claim) into separate records
