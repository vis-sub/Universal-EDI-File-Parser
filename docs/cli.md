# CLI reference

`ediparse` is installed with the package (`pip install -e .`). It's also available as `python -m ediparse`.

```
ediparse [parse] FILE... [-f ndjson|csv|json|tree] [--no-segments] [-o OUT]
ediparse detect FILE...
ediparse validate FILE...
ediparse serve [--host HOST] [--port PORT] [--workers N]
```

`ediparse FILE...` is shorthand for `ediparse parse FILE...`. Use `-` as a file name to read from stdin.

## `parse`

| Format | Streams? | Output |
|---|---|---|
| `ndjson` (default) | Yes, flat memory | One [record](output-format.md) per line, each with `"file"`, and a `summary` per file |
| `csv` | Yes, flat memory | One row per non-empty value: `file, interchange, group, message_type, message_control, segment_index, segment_tag, element, repeat, component, value` |
| `json` | No (loads the file) | One object per file: the full interchange → group → message tree. A JSON array for several files |
| `tree` | No (loads the file) | Human-readable outline with delimiters and issues |

```bash
ediparse batch.edi > batch.ndjson
ediparse --no-segments batch.edi | jq -c '{type: .message.type, control: .message.control, valid}'
ediparse -f csv inbound/*.edi -o values.csv
ediparse -f tree samples/x12/5010/837P_professional_claim.edi
cat file.edi | ediparse -
```

Example `tree` output:

```
== samples/x12/4010/850_purchase_order_pipe_delims.edi
Interchange X12 00401 control=000000001 SENDERID -> RECEIVERID  [element='|' component='>' segment='~']
  Group PO control=1 version=004010
    Message 850 control=0001 version=004010 (21 segments)
    Message 850 control=0002 version=004010 (21 segments)
```

CSV notes:
- Interchange envelope rows (ISA/IEA, UNB/UNZ, TA1…) come after that interchange's messages.
- Group header rows aren't emitted; every row carries the group control number.

## `validate`

Checks envelopes and control totals, printing one line per issue and `OK (n messages)` for clean files.

```bash
ediparse validate inbound/*.edi
```

## `detect`

Prints one JSON line per interchange with the standard, version, delimiters and control number.

## `serve`

Runs the HTTP service. It needs `pip install -e ".[service]"`.

| Option | Default |
|---|---|
| `--host` | `$EDIPARSE_HTTP_HOST` or `127.0.0.1` |
| `--port` | `$EDIPARSE_HTTP_PORT` or `8080` |
| `--workers` | `$WEB_CONCURRENCY` or `1` |

The other service settings (`EDIPARSE_MAX_UPLOAD_MB` etc.) are environment variables. See
[Service API → configuration](service.md#configuration).

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success (for `validate`: no errors) |
| `1` | `validate` found at least one error-severity issue |
| `2` | A file couldn't be read or contains no usable EDI, or bad arguments |
