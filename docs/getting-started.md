# Getting started

Run the service locally, parse your first files, and try the library and CLI. This takes about five minutes.

## Prerequisites

| For | You need |
|---|---|
| The service | Docker Desktop (or Docker Engine + Compose v2), `make`, `curl` |
| The library / CLI from source | Python 3.10+ |
| Optional | `jq` for reading JSON in the terminal; `kubectl` for the Kubernetes path |

## 1. Start the service

```bash
git clone https://github.com/vis-sub/Universal-EDI-File-Parser.git
cd Universal-EDI-File-Parser
make up        # builds the image; starts nginx + 2 parser instances on http://localhost:8080
make smoke     # 11 end-to-end checks against the running service
```

`make up` waits until the service is healthy. Open **http://localhost:8080/docs** for interactive API docs, where you
can try every endpoint in the browser.

Without `make`: `docker compose up --build -d`, then `scripts/smoke-test.sh`.

## 2. Parse a file

```bash
curl --data-binary @samples/x12/5010/850_purchase_order.edi http://localhost:8080/v1/parse
```

You get one JSON line per business document, then an interchange record and a summary:

```json
{"type": "message", "sequence": 1, "interchange": {"standard": "X12", "version": "00501", ...},
 "group": {"type": "PO", "control": "1", "version": "005010"},
 "message": {"type": "850", "control": "0001", "segments": [{"tag": "BEG", "elements": {"BEG03": "PO-10045", ...}}, ...]},
 "valid": true, "issues": []}
{"type": "interchange", ...}
{"type": "summary", "interchanges": 1, "messages": 1, "errors": 0, "warnings": 0, "valid": true}
```

More things to try:

```bash
# Just document metadata, nicely formatted
curl -s --data-binary @samples/x12/4010/850_purchase_order_pipe_delims.edi \
  "localhost:8080/v1/parse?segments=false&include=message" | jq .

# Other standards: same endpoint, no configuration
curl -s --data-binary @samples/edifact/ORDERS_D96A_una.edi "localhost:8080/v1/parse?include=summary"
curl -s --data-binary @samples/hl7/ADT_A01_A08.hl7         "localhost:8080/v1/parse?include=summary"

# Validate envelopes and control totals only
curl -s --data-binary @samples/x12/5010/834_benefit_enrollment.edi localhost:8080/v1/validate | jq .

# What standard and delimiters does this file use?
curl -s --data-binary @samples/x12/5010/837P_professional_claim_alt_delims.edi localhost:8080/v1/detect | jq .

# Compressed upload
gzip -c samples/x12/5010/835_claim_payment.edi | \
  curl -s --data-binary @- -H "Content-Encoding: gzip" "localhost:8080/v1/parse?include=summary"
```

## 3. Process many files

```bash
python3 examples/parallel_client.py samples/x12/*/*.edi --workers 8 --out /tmp/edi-results
ls /tmp/edi-results        # one .ndjson file per input
```

Scale the service and watch requests spread across instances:

```bash
make scale N=4
make logs                  # Ctrl-C to stop following
```

## 4. Use the library or CLI directly

```bash
make dev                   # creates .venv with the package and test tools
source .venv/bin/activate
```

```bash
ediparse -f tree samples/x12/5010/837P_professional_claim.edi    # outline of the file
ediparse samples/x12/5010/850_purchase_order.edi | head -1 | jq .message.type
ediparse validate samples/x12/*/*.edi
ediparse -f csv samples/edifact/ORDERS_D96A_una.edi | head
```

```python
from ediparse import stream

for event in stream("samples/x12/4010/850_purchase_order_pipe_delims.edi"):
    if event.kind == "message":
        msg = event.message
        print(msg.type, msg.control, "PO number:", msg.segments[0].value(3))
```

## 5. Stop

```bash
make down
```

## Troubleshooting

| Symptom | Fix |
|---|---|
| `make up` says the port is in use | Another process has 8080. Run `make up PORT=8090` (and `make smoke PORT=8090`) |
| `Cannot connect to the Docker daemon` | Start Docker Desktop and wait until it's running |
| `422 Not usable EDI` | The body has no readable ISA/UNA/UNB/STX/MSH header. Check you're sending the file itself, not a path or a form upload |
| `415` | You sent multipart form data. Use `--data-binary @file` |
| Records arrive all at once, not streamed | Normal for small files: the service writes in ~64 KiB batches, so a small file's output arrives in one write. For large files, a proxy is buffering responses. See [Deployment → how scaling works](deployment.md#how-scaling-works) |
| `make scale` set the wrong port | Always pass the same `PORT` you used for `make up` |

## Next steps

- [Service API](service.md) and [Output format](output-format.md) for building a consumer
- [Deployment](deployment.md) to run it for your team
- [Architecture](architecture.md) to understand how it works
