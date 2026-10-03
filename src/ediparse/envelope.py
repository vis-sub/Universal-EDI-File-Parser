"""Group segments into interchange > group > message, check control totals, and emit events.

``EnvelopeBuilder`` is incremental. With ``retain=False`` (streaming) it keeps
only the message currently being read plus the open interchange/group headers,
so memory stays flat no matter how many documents a file holds. With
``retain=True`` it also builds the full tree for ``parse_text``/``parse_file``.
"""
from __future__ import annotations

from dataclasses import dataclass

from .dialect import EDIFACT, HL7, TRADACOMS, X12
from .model import (Event, Group, Interchange, InterchangeEvent, Issue, IssueEvent, Message, MessageEvent,
                    Segment)


@dataclass(frozen=True)
class EnvelopeSpec:
    interchange: tuple[str | None, str | None]
    group: tuple[str | None, str | None]
    message: tuple[str, str | None]


SPECS = {
    X12: EnvelopeSpec(("ISA", "IEA"), ("GS", "GE"), ("ST", "SE")),
    EDIFACT: EnvelopeSpec(("UNB", "UNZ"), ("UNG", "UNE"), ("UNH", "UNT")),
    TRADACOMS: EnvelopeSpec(("STX", "END"), (None, None), ("MHD", "MTR")),
    HL7: EnvelopeSpec(("FHS", "FTS"), ("BHS", "BTS"), ("MSH", None)),  # HL7 messages have no trailer
}


class EnvelopeBuilder:
    """Limits apply only when streaming (retain=False), where they bound memory per message and interchange:

    ``max_message_segments``: body segments kept per message; later ones are counted (for SE/UNT checks) but
    not stored, and the message gets a ``message_truncated`` error. ``max_issues``: issues kept per message
    or interchange; later ones are replaced by a single ``too_many_issues`` notice. None means unlimited.
    """

    MAX_LOOSE = 1000  # segments kept per interchange outside any message, when streaming

    def __init__(self, retain: bool = False, max_message_segments: int | None = None, max_issues: int | None = 1000):
        self.retain = retain
        self.max_message_segments = None if retain else max_message_segments
        self.max_issues = None if retain else max_issues
        self.ic: Interchange | None = None
        self.grp: Group | None = None
        self.msg: Message | None = None
        self.una: Segment | None = None
        self.sequence = 0
        self._events: list[Event] = []
        # populated only when retain=True
        self.interchanges: list[Interchange] = []
        self.stray: list[Segment] = []
        self.issues: list[Issue] = []

    def take_events(self) -> list[Event]:
        events, self._events = self._events, []
        return events

    # -- issues ------------------------------------------------------------------
    def add_issue(self, issue: Issue) -> None:
        """Attach to the open message, else the open interchange, else emit on its own."""
        if self.retain:
            self.issues.append(issue)
        target = self.msg.issues if self.msg else self.ic.issues if self.ic else None
        if target is None:
            self._events.append(IssueEvent(issue))
        elif self.max_issues is None or len(target) < self.max_issues:
            target.append(issue)
        elif len(target) == self.max_issues:
            target.append(Issue("warning", "too_many_issues",
                                f"More than {self.max_issues} issues here; further issues are omitted"))

    def issue(self, severity: str, code: str, message: str, seg: Segment | None) -> None:
        self.add_issue(Issue(severity, code, message, seg.index if seg else None))

    # -- opening -----------------------------------------------------------------
    def _open_ic(self, dialect, header: Segment | None = None, una: Segment | None = None) -> Interchange:
        self.ic = Interchange(dialect, header=header, service_string=una)
        if self.retain:
            self.interchanges.append(self.ic)
        return self.ic

    def ensure_ic(self, seg: Segment) -> Interchange:
        if not self.ic:
            self._open_ic(seg.dialect)
            if seg.dialect.standard != HL7:  # HL7 files usually have no FHS
                self.issue("warning", "no_interchange", f"{seg.tag} outside an interchange", seg)
        return self.ic

    def _open_grp(self, seg: Segment, header: Segment | None) -> Group:
        ic = self.ensure_ic(seg)
        self.grp = Group(header=header)
        if header:
            ic.group_count += 1
        if self.retain:
            ic.groups.append(self.grp)
        return self.grp

    def ensure_grp(self, seg: Segment) -> Group:
        if not self.grp:
            self._open_grp(seg, None)
            if seg.dialect.standard == X12:
                self.issue("error", "no_group", f"{seg.tag} outside a GS/GE functional group", seg)
        return self.grp

    # -- closing -----------------------------------------------------------------
    def close_msg(self, at: Segment | None) -> None:
        msg = self.msg
        if not msg:
            return
        trailer_tag = SPECS[msg.standard].message[1]
        if trailer_tag and not msg.trailer:
            self.issue("error", "missing_trailer", f"{msg.header.tag} {msg.control} has no {trailer_tag}", at)
        elif msg.trailer:
            _check_message(self, msg)
        self.msg = None
        self.sequence += 1
        self._events.append(MessageEvent(msg, self.sequence))

    def close_grp(self, at: Segment | None) -> None:
        self.close_msg(at)
        grp, self.grp = self.grp, None
        if not grp or not grp.header:
            return
        if not grp.trailer:
            spec = SPECS[grp.header.dialect.standard]
            self.issue("error", "missing_trailer", f"{grp.header.tag} {grp.control} has no {spec.group[1]}", at)
        else:
            _check_group(self, grp)

    def close_ic(self, at: Segment | None) -> None:
        self.close_grp(at)
        ic = self.ic
        if not ic:
            return
        if ic.header and not ic.trailer:
            spec = SPECS[ic.dialect.standard]
            self.issue("error", "missing_trailer", f"{ic.header.tag} {ic.control} has no {spec.interchange[1]}", at)
        elif ic.header:
            _check_interchange(self, ic)
        self.ic = None
        self._events.append(InterchangeEvent(ic))

    def flush_una(self) -> None:
        if self.una:
            una, self.una = self.una, None
            self.stray_segment(una, "UNA not followed by UNB", "warning", "orphan_una")

    def stray_segment(self, seg: Segment, why: str, severity: str = "error", code: str = "unmatched_segment") -> None:
        if self.ic:
            self.ic.loose.append(seg)
        elif self.retain:
            self.stray.append(seg)
        self.issue(severity, code, why, seg)

    # -- main dispatch -----------------------------------------------------------
    def feed(self, seg: Segment) -> None:
        spec, t = SPECS[seg.dialect.standard], seg.tag
        if self.ic and self.ic.dialect.standard != seg.dialect.standard:
            self.close_ic(seg)  # standard changed mid-file

        if t == "UNA":
            self.flush_una()
            self.una = seg
            return
        if t != "UNB":
            self.flush_una()

        if t == spec.interchange[0]:
            self.close_ic(seg)
            self._open_ic(seg.dialect, header=seg, una=self.una)
            self.una = None
        elif t == spec.interchange[1]:
            self.close_grp(seg)
            if self.ic and self.ic.header:
                self.ic.trailer = seg
                self.close_ic(seg)
            else:
                self.stray_segment(seg, f"{t} without matching {spec.interchange[0]}")
        elif t == spec.group[0]:
            self.close_grp(seg)
            self._open_grp(seg, seg)
        elif t == spec.group[1]:
            self.close_msg(seg)
            if self.grp and self.grp.header:
                self.grp.trailer = seg
                self.close_grp(seg)
            else:
                self.stray_segment(seg, f"{t} without matching {spec.group[0]}")
        elif t == spec.message[0]:
            self.close_msg(seg)
            grp = self.ensure_grp(seg)
            self.msg = Message(header=seg, group=grp, interchange=self.ic)
            grp.message_count += 1
            self.ic.message_count += 1
            if self.retain:
                grp.messages.append(self.msg)
        elif spec.message[1] and t == spec.message[1]:
            if self.msg:
                self.msg.trailer = seg
                self.close_msg(seg)
            else:
                self.stray_segment(seg, f"{t} without matching {spec.message[0]}")
        elif self.msg:
            msg = self.msg
            msg.body_count += 1
            if self.max_message_segments is None or len(msg.segments) < self.max_message_segments:
                msg.segments.append(seg)
            elif not msg.truncated:
                msg.truncated = True
                self.issue("error", "message_truncated",
                           f"{msg.header.tag} {msg.control} has more than {self.max_message_segments} segments; "
                           "later segments are counted but not returned", seg)
        elif self.ic:
            if self.retain or len(self.ic.loose) < self.MAX_LOOSE:
                self.ic.loose.append(seg)
            if not (seg.dialect.standard == X12 and t == "TA1"):
                self.issue("warning", "outside_message", f"{t} is outside any message", seg)
        else:
            self.stray_segment(seg, f"{t} is outside any interchange")

    def finish(self) -> None:
        self.flush_una()
        self.close_ic(None)


# -- control-total checks ----------------------------------------------------------

def _expect(b: EnvelopeBuilder, seg: Segment, n: int, expected: object, what: str) -> None:
    actual = seg.value(n).strip()
    if isinstance(expected, int):
        # isascii(): str.isdigit() accepts characters like '²' that int() rejects (found by fuzzing)
        ok = actual.isascii() and actual.isdigit() and int(actual) == expected
    else:
        ok = actual == str(expected).strip()
    if not ok:
        b.issue("error", "control_mismatch",
                f"{seg.element_id(n)} {what} is {actual!r}, expected {expected!r}", seg)


def _check_message(b: EnvelopeBuilder, msg: Message) -> None:
    t, count = msg.trailer, msg.body_count + 2
    if msg.standard in (X12, EDIFACT):
        _expect(b, t, 1, count, "segment count")
        _expect(b, t, 2, msg.control, "control number")
    elif msg.standard == TRADACOMS:
        _expect(b, t, 1, count, "segment count")


def _check_group(b: EnvelopeBuilder, grp: Group) -> None:
    std = grp.header.dialect.standard
    if std in (X12, EDIFACT):
        _expect(b, grp.trailer, 1, grp.message_count, "message count")
        _expect(b, grp.trailer, 2, grp.control, "group control number")


def _check_interchange(b: EnvelopeBuilder, ic: Interchange) -> None:
    t, std = ic.trailer, ic.dialect.standard
    if std == X12:
        _expect(b, t, 1, ic.group_count, "group count")
        _expect(b, t, 2, ic.control, "interchange control number")
    elif std == EDIFACT:
        # UNZ01 counts groups when UNG is used, otherwise messages.
        _expect(b, t, 1, ic.group_count or ic.message_count, "group/message count")
        _expect(b, t, 2, ic.control, "interchange control reference")
    elif std == TRADACOMS:
        _expect(b, t, 1, ic.message_count, "message count")
