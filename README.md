# Universal EDI Parser

Reads any **X12** (4010, 5010, and other versions), **EDIFACT**, **TRADACOMS** or **HL7 v2** file and returns
**one JSON object per business document**. It needs no trading-partner mappings or configuration: the delimiters are
detected from each interchange header.

You can use it in three ways:
- as a **service** that dev teams spin up with Docker and stream files through
- as a **Python library**
- as a **command-line tool**

Everything streams: memory stays flat whatever the file size, limited only by the largest single document.

## Quick start: the service

```bash
docker compose up --build
curl --data-binary @samples/x12/5010/850_purchase_order.edi http://localhost:8080/v1/parse
```

```json
{"type": "message", "sequence": 1, "interchange": {"standard": "X12", "version": "00501", "sender": "SENDERID", ...},
 "group": {"type": "PO", "control": "1", "version": "005010"},
 "message": {"type": "850", "control": "0001", "segments": [{"tag": "BEG", "elements": {"BEG03": "PO-10045", ...}}, ...]},
 "valid": true, "issues": []}
{"type": "interchange", ...}
{"type": "summary", "interchanges": 1, "messages": 1, "errors": 0, "warnings": 0, "valid": true}
```

Interactive API docs are at http://localhost:8080/docs. See **[docs/service.md](docs/service.md)** for the API,
record format, configuration, deployment, and client examples.

## Library

```bash
pip install -e .            # core library: no dependencies
pip install -e ".[service]" # + FastAPI/uvicorn for the HTTP service
```

```python
from ediparse import stream, event_to_dict

for event in stream("big_batch.edi"):        # path, bytes, file object, or iterable of chunks
    if event.kind == "message":
        msg = event.message                   # ST/SE, UNH/UNT, MHD/MTR or MSH document
        print(msg.type, msg.control, msg.version, msg.group.type)
        po_number = msg.segments[0].value(3)  # 1-based elements: BEG03
        record = event_to_dict(event)         # same JSON the service returns
```

- **Push-style parsing.** `StreamParser` lets you push chunks as they arrive (from sockets, queues, or object storage), and `astream` accepts async byte sources.
- **Small files.** `parse_file(path)` returns the full interchange → group → message tree and round-trips byte for byte (`doc.to_bytes()`).
- **Accessors.** `seg.components(n)` and `seg.repeats(n)` handle composite and repeated elements. Escapes are resolved in values, while `seg.raw` keeps the source text.

## Command line

```bash
ediparse big_batch.edi                       # NDJSON: one line per document (streams)
ediparse --no-segments big_batch.edi         # metadata only
ediparse -f csv samples/x12/5010/*.edi -o out.csv   # one row per value (streams)
ediparse -f tree file.edi                    # human-readable outline
ediparse validate file.edi                   # envelope / control-total checks; exit 1 on errors
ediparse detect file.edi                     # standard, version, delimiters
ediparse serve --port 8080                   # run the HTTP service
```

## What it handles

- Delimiters read from ISA / UNA / UNB / STX / MSH. A file can hold several interchanges, each with its own delimiters, or even a different standard.
- X12 4010 vs 5010 repetition-separator rules. Some 4010 senders put a `U` in ISA11, and it is never treated as a delimiter.
- EDIFACT release characters, HL7 escape sequences, and control-character delimiters.
- Control totals and control numbers checked at every level. Problems are reported per document and never stop the stream.
- Messy input: 80-column wrapping, a missing final terminator, ISA segments that aren't fixed width, leading junk or a BOM, Latin-1, TA1 acknowledgments, and doubled terminators.

## How it works

| Layer | Module | Needs a schema? |
|---|---|---|
| Detect standard + delimiters at every interchange header | `dialect.py` | no |
| Incremental tokenizer → segments → elements → repeats → components | `tokenizer.py`, `model.py` | no |
| Envelope builder: interchange → group → message, control totals, events | `envelope.py`, `stream.py` | no |
| JSON / NDJSON / CSV / tree output | `output.py`, `cli.py` | no |
| HTTP service | `service.py` | no |
| Loop resolution and field meanings | *roadmap* | dictionaries + heuristics |

## Reference material

- [`docs/document-catalog.md`](docs/document-catalog.md): what every common X12 document looks like, and the 4010 vs 5010 differences
- [`reference/x12_transaction_sets.json`](reference/x12_transaction_sets.json): all 319 X12 transaction sets in 4010/5010
- [`samples/`](samples/): synthetic test files for X12, EDIFACT, TRADACOMS and HL7. They contain no real data.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/pytest
```

## License

[MIT](LICENSE)
