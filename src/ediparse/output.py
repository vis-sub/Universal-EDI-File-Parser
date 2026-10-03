"""Render parse results: per-event JSON objects (streaming), flat CSV rows, a full-document dict, or a text tree."""
from __future__ import annotations

from collections.abc import Iterable, Iterator

from .model import (Document, Event, Group, Interchange, InterchangeEvent, Issue, IssueEvent, Message,
                    MessageEvent, Segment)


def element_value(seg: Segment, n: int):
    """A plain string, a list of components, or {"repeats": [...]} for repeated elements."""
    reps = seg.repeats(n)
    vals = []
    for r in range(len(reps)):
        comps = seg.components(n, r)
        vals.append(comps[0] if len(comps) == 1 else comps)
    return vals[0] if len(vals) == 1 else {"repeats": vals}


def segment_to_dict(seg: Segment) -> dict:
    elements = {seg.element_id(n): element_value(seg, n)
                for n in range(1, len(seg.elements) + 1) if seg.element(n) != ""}
    return {"tag": seg.tag, "index": seg.index, "elements": elements}


def _seg(seg: Segment | None) -> dict | None:
    return segment_to_dict(seg) if seg else None


def _issues(issues: list[Issue]) -> list[dict]:
    return [i.to_dict() for i in issues]


# -- streaming events ------------------------------------------------------------------

def interchange_summary(ic: Interchange) -> dict:
    return {"standard": ic.dialect.standard, "version": ic.dialect.version, "control": ic.control,
            "sender": ic.sender, "receiver": ic.receiver, "date": ic.date}


def group_summary(grp: Group | None) -> dict | None:
    if not grp or not grp.header:
        return None
    return {"type": grp.type, "control": grp.control, "version": grp.version}


def event_to_dict(event: Event, segments: bool = True) -> dict:
    """JSON-ready dict for one stream event. ``segments=False`` drops segment bodies (metadata only)."""
    if isinstance(event, MessageEvent):
        msg = event.message
        body = {"type": msg.type, "control": msg.control, "version": msg.version,
                "segment_count": len(msg.segments) + 1 + (1 if msg.trailer else 0)}
        if segments:
            body |= {"header": _seg(msg.header), "trailer": _seg(msg.trailer),
                     "segments": [segment_to_dict(s) for s in msg.segments]}
        return {"type": "message", "sequence": event.sequence,
                "interchange": interchange_summary(msg.interchange) if msg.interchange else None,
                "group": group_summary(msg.group), "message": body,
                "valid": not any(i.severity == "error" for i in msg.issues), "issues": _issues(msg.issues)}
    if isinstance(event, InterchangeEvent):
        ic = event.interchange
        d = {"type": "interchange", **interchange_summary(ic), "delimiters": ic.dialect.describe(),
             "groups": ic.group_count, "messages": ic.message_count,
             "valid": not any(i.severity == "error" for i in ic.issues), "issues": _issues(ic.issues)}
        if segments:
            d |= {"service_string": _seg(ic.service_string), "header": _seg(ic.header),
                  "trailer": _seg(ic.trailer), "loose_segments": [segment_to_dict(s) for s in ic.loose]}
        return d
    if isinstance(event, IssueEvent):
        return {"type": "issue", **event.issue.to_dict()}
    raise TypeError(f"Unknown event {event!r}")


class Summary:
    """Running totals over an event stream; ``to_dict()`` gives the final summary record."""

    def __init__(self):
        self.messages = self.interchanges = self.errors = self.warnings = 0

    def add(self, event: Event) -> None:
        if isinstance(event, MessageEvent):
            self.messages += 1
            issues = event.message.issues
        elif isinstance(event, InterchangeEvent):
            self.interchanges += 1
            issues = event.interchange.issues
        else:
            issues = [event.issue]
        self.errors += sum(i.severity == "error" for i in issues)
        self.warnings += sum(i.severity == "warning" for i in issues)

    def to_dict(self) -> dict:
        return {"type": "summary", "interchanges": self.interchanges, "messages": self.messages,
                "errors": self.errors, "warnings": self.warnings, "valid": self.errors == 0}


CSV_COLUMNS = ["interchange", "group", "message_type", "message_control", "segment_index",
               "segment_tag", "element", "repeat", "component", "value"]


def _segment_rows(seg: Segment, ctx: list) -> Iterator[list]:
    for n in range(1, len(seg.elements) + 1):
        for r in range(len(seg.repeats(n))):
            comps = seg.components(n, r)
            for c, v in enumerate(comps, 1):
                if v != "":
                    yield [*ctx, seg.index, seg.tag, seg.element_id(n), r + 1, c if len(comps) > 1 else "", v]


def csv_rows(events: Iterable[Event]) -> Iterator[list]:
    """One row per non-empty value, labelled with its envelope context. Streams: works on ``stream()`` output.

    Message rows come out as each message completes; interchange envelope rows (ISA/IEA,
    UNB/UNZ, TA1, ...) follow when the interchange closes. Group headers are not
    emitted as rows; the group control number is in every row's ``group`` column.
    """
    for ev in events:
        if isinstance(ev, MessageEvent):
            msg = ev.message
            ctx = [msg.interchange.control if msg.interchange else "", msg.group.control if msg.group else "",
                   msg.type, msg.control]
            for seg in msg.all_segments():
                yield from _segment_rows(seg, ctx)
        elif isinstance(ev, InterchangeEvent):
            ic = ev.interchange
            for seg in filter(None, [ic.service_string, ic.header, *ic.loose, ic.trailer]):
                yield from _segment_rows(seg, [ic.control or "", "", "", ""])


# -- whole document ---------------------------------------------------------------------

def message_to_dict(msg: Message) -> dict:
    return {"type": msg.type, "control": msg.control, "version": msg.version,
            "header": _seg(msg.header), "trailer": _seg(msg.trailer),
            "segments": [segment_to_dict(s) for s in msg.segments]}


def group_to_dict(grp: Group) -> dict:
    return {"type": grp.type, "control": grp.control, "version": grp.version,
            "header": _seg(grp.header), "trailer": _seg(grp.trailer),
            "messages": [message_to_dict(m) for m in grp.messages]}


def interchange_to_dict(ic: Interchange) -> dict:
    return {**interchange_summary(ic), "delimiters": ic.dialect.describe(),
            "service_string": _seg(ic.service_string), "header": _seg(ic.header), "trailer": _seg(ic.trailer),
            "loose_segments": [segment_to_dict(s) for s in ic.loose],
            "groups": [group_to_dict(g) for g in ic.groups]}


def document_to_dict(doc: Document) -> dict:
    return {"encoding": doc.encoding, "segment_count": len(doc.segments),
            "interchanges": [interchange_to_dict(ic) for ic in doc.interchanges],
            "stray_segments": [segment_to_dict(s) for s in doc.stray],
            "issues": _issues(doc.issues)}


def tree_lines(doc: Document) -> Iterator[str]:
    for ic in doc.interchanges:
        d = ic.dialect
        delims = " ".join(f"{k}={v!r}" for k, v in d.describe().items() if k not in ("standard", "version"))
        yield (f"Interchange {d.standard}{' ' + d.version if d.version else ''}"
               f" control={ic.control} {ic.sender or '?'} -> {ic.receiver or '?'}  [{delims}]")
        for s in ic.loose:
            yield f"  ({s.tag} outside groups)"
        for grp in ic.groups:
            if grp.header:
                yield f"  Group {grp.type} control={grp.control}{' version=' + grp.version if grp.version else ''}"
            indent = "    " if grp.header else "  "
            for msg in grp.messages:
                yield (f"{indent}Message {msg.type} control={msg.control}"
                       f"{' version=' + msg.version if msg.version else ''} ({len(msg.all_segments())} segments)")
    for s in doc.stray:
        yield f"(stray {s.tag} at segment {s.index})"
    for i in doc.issues:
        yield f"{i.severity.upper()} [{i.code}] {i.message}" + (f" (segment {i.segment_index})" if i.segment_index is not None else "")
