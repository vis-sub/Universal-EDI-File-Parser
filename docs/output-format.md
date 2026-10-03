# Output format

The service (`POST /v1/parse`), the CLI (`ediparse parse`) and the library (`event_to_dict`) all produce the same
records. A machine-readable definition is in [`schemas/records.schema.json`](schemas/records.schema.json) (JSON
Schema 2020-12). The test suite validates every record from every sample, and from fuzzed input, against it.

## Stream layout

```
{"type":"message", ...}        one per business document, in file order, as each completes
{"type":"message", ...}
{"type":"interchange", ...}    when each interchange closes (after its messages)
{"type":"issue", ...}          only for problems outside any interchange
{"type":"summary", ...}        always last
```

- In NDJSON (the default) each record is one line.
- `format=json` wraps the same records in one JSON array.
- CLI records also carry `"file": "<path>"`.
- Filter record types with `include=` in the service. In the CLI, use `--no-segments` to drop segment bodies.

## `message`

```json
{
  "type": "message",
  "sequence": 1,
  "interchange": {"standard": "X12", "version": "00501", "control": "000000001",
                  "sender": "SENDERID", "receiver": "RECEIVERID", "date": "261003"},
  "group": {"type": "HC", "control": "1", "version": "005010X222A1"},
  "message": {
    "type": "837", "control": "0001", "version": "005010X222A1", "segment_count": 29,
    "header":  {"tag": "ST", "index": 2, "elements": {"ST01": "837", "ST02": "0001", "ST03": "005010X222A1"}},
    "trailer": {"tag": "SE", "index": 30, "elements": {"SE01": "29", "SE02": "0001"}},
    "segments": [
      {"tag": "BHT", "index": 3, "elements": {"BHT01": "0019", "BHT02": "00", "BHT03": "BATCH0001", "BHT04": "20261003", "BHT05": "1200", "BHT06": "CH"}},
      {"tag": "CLM", "index": 20, "elements": {"CLM01": "CLM-0001", "CLM02": "150", "CLM05": ["11", "B", "1"], "CLM06": "Y", "CLM07": "A", "CLM08": "Y", "CLM09": "Y"}},
      {"tag": "SV1", "index": 25, "elements": {"SV101": ["HC", "99213"], "SV102": "100", "SV103": "UN", "SV104": "1", "SV107": "1"}}
    ]
  },
  "valid": true,
  "issues": []
}
```

| Field | Meaning |
|---|---|
| `sequence` | 1-based position of this document in the stream |
| `interchange` | Context of the enclosing interchange: `control` is ISA13/UNB05/STX05, `version` is ISA12 or the EDIFACT syntax version. `null` only if the document is outside any interchange |
| `group` | Enclosing functional group: X12 GS01/GS06/GS08, EDIFACT UNG. `null` when there's no explicit group (EDIFACT without UNG, TRADACOMS, HL7) |
| `message.type` | ST01 / UNH02 type / MHD02 type / MSH-9 |
| `message.control` | ST02 / UNH01 / MHD01 / MSH-10 |
| `message.version` | ST03 or GS08 / UNH02 directory (`D.96A`) / MHD02 version / MSH-12 |
| `message.segment_count` | Segments including header and trailer |
| `header`, `trailer`, `segments` | Present unless `segments=false`. `segments` is the body in order, without header and trailer. `trailer` is `null` if missing, or always for HL7 |
| `valid` | `false` if any issue on this document has severity `error` |
| `issues` | Problems detected while this document was open |

## Segments and element values

```json
{"tag": "NM1", "index": 9, "elements": {"NM101": "85", "NM102": "2", "NM103": "EXAMPLE FAMILY CLINIC", "NM108": "XX", "NM109": "1234567893"}}
```

- **`index`**: 0-based position of the segment in the whole input. Use it to locate the segment in the source and to match issues to segments (`segment_index`).
- **Keys** are positional element IDs: tag plus a 2-digit position for X12/EDIFACT/TRADACOMS (`NM103`), and `TAG-n` for HL7 (`PID-5`). **Empty elements are omitted.** `NM104` missing means it was empty.
- **Values** take one of three forms:

| Form | JSON | Example source → output |
|---|---|---|
| Simple | string | `BEG*00*SA*PO-10045` → `"BEG03": "PO-10045"` |
| Composite | array of strings (components, empty ones kept to preserve position) | `SV1*HC:99213` → `"SV101": ["HC", "99213"]` |
| Repeated | `{"repeats": [...]}`, each item a string or component array | `EB*1*IND*30^1^98` → `"EB03": {"repeats": ["30", "1", "98"]}` |

- **Escapes are resolved:** EDIFACT `?+` becomes `+`; HL7 `\F\ \S\ \T\ \R\ \E\` become the corresponding delimiters.
- **Invalid UTF-8 bytes** are shown as their Latin-1 characters (`0xC9` → `É`).
- **HL7 `MSH-1` and `MSH-2`** are the field separator and encoding characters, given literally (`"|"`, `"^~\\&"`).

## `interchange`

```json
{
  "type": "interchange", "standard": "X12", "version": "00401", "control": "000000001",
  "sender": "SENDERID", "receiver": "RECEIVERID", "date": "261003",
  "delimiters": {"standard": "X12", "version": "00401", "element": "|", "component": ">", "segment": "~"},
  "groups": 1, "messages": 2, "valid": true, "issues": [],
  "service_string": null,
  "header":  {"tag": "ISA", "index": 0, "elements": {"ISA01": "00", "...": "..."}},
  "trailer": {"tag": "IEA", "index": 45, "elements": {"IEA01": "1", "IEA02": "000000001"}},
  "loose_segments": []
}
```

| Field | Meaning |
|---|---|
| `delimiters` | What was detected: `element`, `component`, `segment`, plus `repetition`, `release`, `tag_separator`, `subcomponent` and `escape` when the standard uses them |
| `groups`, `messages` | Counts in this interchange |
| `issues` | Envelope problems not tied to one document (e.g. a wrong `IEA01`, a missing `GE`) |
| `service_string` | The EDIFACT `UNA` segment, if present |
| `loose_segments` | Segments inside the interchange but outside any group or message (e.g. X12 `TA1`) |
| `header`/`trailer`/`service_string`/`loose_segments` | Omitted when `segments=false` |

## `issue`

A problem outside any interchange. The fields are the same as entries in `issues` arrays:

```json
{"type": "issue", "severity": "error", "code": "bad_header",
 "message": "Invalid X12 segment terminator 'Z' in ISA (header at offset 214); skipped 214 characters to the next interchange header",
 "segment_index": 6}
```

## `summary`

```json
{"type": "summary", "interchanges": 1, "messages": 2, "errors": 0, "warnings": 0, "valid": true}
```

It's always the last record. **If a response ends without one, treat it as incomplete** (connection lost, or an
`error` record came instead).

## `error` (service only)

```json
{"type": "error", "message": "...", "error": "RuntimeError"}
```

Sent if parsing fails unexpectedly after the 200 response has started. It hasn't occurred in fuzz testing, but
clients should handle it.

## Issue codes

| Code | Severity | Meaning |
|---|---|---|
| `control_mismatch` | error | A trailer count or control number doesn't match (SE01, SE02, GE01, GE02, IEA01, IEA02, UNT/UNE/UNZ, MTR/END) |
| `missing_trailer` | error | A message, group or interchange ended without its trailer |
| `unmatched_segment` | error | A trailer without its header, or a segment outside any interchange |
| `no_group` | error | An X12 `ST` outside a `GS`/`GE` group |
| `bad_header` | error | An interchange header couldn't be read. The parser skipped to the next header (the message says how far) |
| `outside_message` | warning | A segment inside an interchange but outside any message (other than X12 `TA1`) |
| `no_interchange` | warning | A group or message with no interchange header |
| `orphan_una` | warning | An EDIFACT `UNA` not followed by `UNB` |
| `isa_format` | warning | The ISA isn't the standard 106 characters, or ISA11 isn't a usable repetition separator |
| `leading_data` | warning | Non-blank characters before the first header were ignored |
| `unterminated_segment` | warning | The last segment has no terminator |
| `empty_segment` | warning | An empty segment (e.g. doubled terminator) was skipped |
| `unusual_tag` | warning | A segment tag isn't 2–3 uppercase letters or digits. Often a sign of corruption |
| `wrapped_lines` | info | Line breaks inside segments were removed (hard-wrapped file) |
| `invalid_utf8` | info | Bytes that aren't valid UTF-8 were read as Latin-1 |

`segment_index` (when present) is the `index` of the segment the issue refers to.

## Consuming records: patterns

```python
import json
for line in response_lines:                     # any HTTP client that reads incrementally
    rec = json.loads(line)
    match rec["type"]:
        case "message" if rec["valid"]:
            route(rec["message"]["type"], rec)  # e.g. 850 → orders, 837 → claims
        case "message":
            quarantine(rec)                     # rec["issues"] says why
        case "summary":
            mark_file_complete(rec)
        case "error":
            retry_or_alert(rec)
```

Getting a field out of a document:

```python
def first(rec, tag):
    return next((s["elements"] for s in rec["message"]["segments"] if s["tag"] == tag), {})

po_number = first(rec, "BEG").get("BEG03")
ship_to   = next(s["elements"].get("N102") for s in rec["message"]["segments"]
                 if s["tag"] == "N1" and s["elements"].get("N101") == "ST")
```
