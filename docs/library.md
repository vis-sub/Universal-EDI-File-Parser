# Library reference

```bash
pip install -e .              # from a checkout; the core has no dependencies
```

```python
from ediparse import stream, StreamParser, astream, parse_file, parse_bytes, parse_text, event_to_dict
```

## Choosing an entry point

| You have | Use | Memory |
|---|---|---|
| A file path, file object, bytes, or iterable of chunks | `stream(source)` | Flat |
| Chunks arriving from somewhere else (socket, queue, HTTP body) | `StreamParser().feed(chunk)` / `.close()` | Flat |
| An async byte source | `astream(async_iterable)` | Flat |
| A small file, and you want the whole tree or byte-exact round-trips | `parse_file` / `parse_bytes` / `parse_text` | Whole file |

## Streaming

### `stream(source, *, chunk_size=65536, encoding="auto") -> Iterator[Event]`

```python
for event in stream("batch.edi"):
    if event.kind == "message":
        handle(event.message)
    elif event.kind == "interchange":
        audit(event.interchange)
    else:                                   # "issue": problem outside any interchange
        log(event.issue)
```

`source` can be:
- a path (`str` or `os.PathLike`). A `str` is always treated as a path. To parse EDI text, pass `[text]`
- `bytes` or `bytearray`
- a binary file object: anything with `.read(n)`, including `boto3` S3 bodies and `gzip.open(...)`
- an iterable of `bytes` or `str` chunks

`encoding="auto"` decodes UTF-8 and reads any invalid bytes as Latin-1 (see
[Architecture → text decoding](architecture.md#text-decoding)). You can also pass an ASCII-compatible codec name
(e.g. `"cp1252"`). Bytes it can't decode are kept losslessly and shown as Latin-1, so decoding never fails. UTF-16/32
aren't supported (EDI delimiters must be single ASCII bytes).

Optional limits (keyword arguments, also accepted by `StreamParser` and `astream`) bound memory per document:

| Argument | Default | When exceeded |
|---|---|---|
| `max_segment` | 16 MiB (characters) | `oversized_segment` error; skip to the next interchange header |
| `max_message_segments` | `None` (unlimited) | Later body segments are counted but not stored; `message.truncated = True`, `message_truncated` error |
| `max_issues` | 1000 | Per message/interchange; then one `too_many_issues` notice |

They apply only when streaming. Retained parsing (`parse_file`, `retain=True`) keeps everything.

Raises `EDIDetectionError` only if the input contains no usable EDI header at all.

### `StreamParser(encoding="auto", retain=False, *, max_segment=..., max_message_segments=None, max_issues=1000)`

The push parser that everything else is built on.

```python
p = StreamParser()
for chunk in chunks_from_somewhere():
    for event in p.feed(chunk):             # events completed by this chunk (often none)
        handle(event)
for event in p.close():                     # flush the end of input
    handle(event)
```

| Member | Description |
|---|---|
| `feed(data: bytes \| str) -> list[Event]` | Push a chunk of any size. Returns the events it completed |
| `close() -> list[Event]` | Signal end of input. Returns the remaining events, including the final `InterchangeEvent`s |
| `encoding` | The codec in use |
| `document() -> Document` | With `retain=True`, after `close()`: the full tree |

Results are identical however the input is chunked, down to one byte at a time. The tests check this.

### `astream(source, *, encoding="auto") -> AsyncIterator[Event]`

```python
async for event in astream(response.aiter_bytes()):
    ...
```

Parsing is CPU-bound and runs on the event loop thread. For large inputs in an async server, run `stream()` in a
worker thread instead, as the service does.

## Events

| Class | `kind` | Attributes |
|---|---|---|
| `MessageEvent` | `"message"` | `message: Message`, `sequence: int` (1-based) |
| `InterchangeEvent` | `"interchange"` | `interchange: Interchange` |
| `IssueEvent` | `"issue"` | `issue: Issue` |

`event_to_dict(event, segments=True) -> dict` gives the JSON record documented in [Output format](output-format.md).
`segments=False` drops segment bodies.

## Data model

### `Message`

| Attribute | Description |
|---|---|
| `type` | ST01 / UNH02 type / MHD02 type / MSH-9 (`"ADT^A01^ADT_A01"`) |
| `control` | ST02 / UNH01 / MHD01 / MSH-10 |
| `version` | ST03 or GS08 / `"D.96A"` / MHD02 version / MSH-12 |
| `standard` | `"X12"`, `"EDIFACT"`, `"TRADACOMS"` or `"HL7"` |
| `header`, `trailer` | `Segment`s (`trailer` is `None` if missing, and always for HL7) |
| `segments` | Body segments in order, excluding header and trailer |
| `all_segments()` | Header + body + trailer |
| `group`, `interchange` | Enclosing `Group` / `Interchange` (headers available while streaming) |
| `issues` | `Issue`s raised while this message was open |
| `body_count` | Body segments seen (equals `len(segments)` unless truncated) |
| `truncated` | `True` if `max_message_segments` stopped storing body segments |

### `Segment`

| Member | Description |
|---|---|
| `tag` | `"BEG"`, `"NM1"`, `"PID"` … |
| `value(n, component=None, repeat=0) -> str` | Element `n` (**1-based**) with escapes resolved, or `""` if absent. With `component` (1-based), one component of a composite |
| `seg[n]` | Same as `seg.value(n)` |
| `components(n, repeat=0) -> list[str]` | The components of element `n` |
| `repeats(n) -> list[str]` | The raw repetitions of element `n` (a one-item list if not repeated) |
| `element(n) -> str` | Raw element text, escapes intact |
| `element_id(n) -> str` | `"BEG03"` / `"PID-5"` |
| `elements` | Raw element strings (0-based list) |
| `raw` | Exact source text, including terminator and trailing line break |
| `index`, `offset` | Position in the input: segment number and character offset |
| `dialect` | The `Dialect` in effect |

```python
clm = next(s for s in msg.segments if s.tag == "CLM")
clm[1]                      # 'CLM-0001'      CLM01
clm.components(5)           # ['11', 'B', '1'] CLM05 (place of service : qualifier : frequency)
clm.value(5, component=1)   # '11'

eb = next(s for s in msg.segments if s.tag == "EB")
eb.repeats(3)               # ['30', '1', '98'] (5010 repetition separator)
```

### `Group`, `Interchange`

| | Attributes |
|---|---|
| `Group` | `type` (GS01), `control` (GS06/UNG05), `version` (GS08), `header`, `trailer`, `message_count`, `messages` (retained mode only) |
| `Interchange` | `sender`, `receiver`, `control`, `date`, `dialect`, `header`, `trailer`, `service_string` (UNA), `loose` (e.g. TA1), `group_count`, `message_count`, `issues`, `groups` and `messages` (retained mode only) |

### `Dialect`

Frozen dataclass: `standard`, `element`, `component`, `segment`, `repetition`, `release`, `tag_separator`,
`subcomponent`, `escape`, `version`. `describe()` returns the non-empty fields as a dict.

### `Issue`

`severity` (`"error" | "warning" | "info"`), `code`, `message`, `segment_index` (or `None`). `to_dict()` gives the
JSON form. The codes are listed in [Output format](output-format.md#issue-codes).

## Whole-document parsing

```python
doc = parse_file("small.edi")        # also parse_bytes(data), parse_text(text)
doc.interchanges[0].groups[0].messages[0].segments
doc.messages                         # all messages, flattened
doc.issues, doc.errors               # all issues / only severity "error"
doc.stray                            # segments outside any interchange
doc.to_bytes() == open("small.edi", "rb").read()   # True: lossless round trip
```

`Document` holds every segment, so memory grows with file size. Use `stream()` for anything large.

## Recipes

**Route documents by type to handlers**

```python
HANDLERS = {"850": on_order, "810": on_invoice, "856": on_asn}
for ev in stream(path):
    if ev.kind == "message" and ev.message.type in HANDLERS and not any(i.severity == "error" for i in ev.message.issues):
        HANDLERS[ev.message.type](ev.message)
```

**Stream from S3 without downloading the file**

```python
body = boto3.client("s3").get_object(Bucket=b, Key=k)["Body"]
for ev in stream(body):
    ...
```

**Gzip input**

```python
import gzip
with gzip.open("batch.edi.gz", "rb") as f:
    for ev in stream(f):
        ...
```

**Write NDJSON for a warehouse load**

```python
import json
from ediparse import event_to_dict, stream

with open("out.ndjson", "w") as out:
    for ev in stream("batch.edi"):
        out.write(json.dumps(event_to_dict(ev)) + "\n")
```
