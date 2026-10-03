"""Data model: segments, the envelope tree, and parse issues."""
from __future__ import annotations

from dataclasses import dataclass, field

from .dialect import EDIFACT, HL7, TRADACOMS, X12, Dialect
from .textutil import display, has_escaped_bytes  # noqa: F401 (re-exported)


def split_escaped(s: str, sep: str, release: str | None) -> list[str]:
    """Split on ``sep``, ignoring separators preceded by the release character."""
    if not release or release not in s:
        return s.split(sep)
    parts, cur, i = [], [], 0
    while i < len(s):
        c = s[i]
        if c == release and i + 1 < len(s):
            cur += (c, s[i + 1])
            i += 2
            continue
        if c == sep:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    parts.append("".join(cur))
    return parts


@dataclass(slots=True)
class Issue:
    severity: str  # "error" | "warning" | "info"
    code: str
    message: str
    segment_index: int | None = None

    def to_dict(self) -> dict:
        d = {"severity": self.severity, "code": self.code, "message": self.message}
        if self.segment_index is not None:
            d["segment_index"] = self.segment_index
        return d


@dataclass(eq=False, slots=True)
class Segment:
    """One segment. ``elements`` holds raw element text (escapes intact), 1-based via accessors.

    ``raw`` is the exact source text including terminator and trailing line
    break, so joining every segment's ``raw`` reproduces the input byte for byte.
    """
    tag: str
    elements: list[str]
    raw: str
    offset: int
    index: int
    dialect: Dialect

    def _atomic(self, n: int) -> bool:
        # Service segments whose elements *are* delimiter characters must not be split.
        if self.tag in ("ISA", "UNA"):
            return True
        return self.dialect.standard == HL7 and self.tag in ("MSH", "FHS", "BHS") and n in (1, 2)

    def element(self, n: int) -> str:
        """Raw text of element ``n`` (1-based); empty string if absent."""
        return self.elements[n - 1] if 1 <= n <= len(self.elements) else ""

    def repeats(self, n: int) -> list[str]:
        raw = self.element(n)
        if self._atomic(n) or not self.dialect.repetition:
            return [raw]
        return split_escaped(raw, self.dialect.repetition, self.dialect.release)

    def components(self, n: int, repeat: int = 0) -> list[str]:
        reps = self.repeats(n)
        raw = reps[repeat] if repeat < len(reps) else ""
        if self._atomic(n):
            return [display(raw)]
        return [display(self.dialect.unescape(c))
                for c in split_escaped(raw, self.dialect.component, self.dialect.release)]

    def value(self, n: int, component: int | None = None, repeat: int = 0) -> str:
        """Element value with escapes resolved; ``component`` is 1-based."""
        if component is None:
            return display(self.dialect.unescape(self.element(n)))
        comps = self.components(n, repeat)
        return comps[component - 1] if 1 <= component <= len(comps) else ""

    def __getitem__(self, n: int) -> str:
        return self.value(n)

    def element_id(self, n: int) -> str:
        return self.dialect.element_id(self.tag, n)

    def __repr__(self) -> str:
        return f"Segment({self.index}: {self.raw.rstrip()!r})"


@dataclass(eq=False, slots=True)
class Message:
    """A single business document: X12 ST/SE, EDIFACT UNH/UNT, TRADACOMS MHD/MTR, HL7 MSH."""
    header: Segment
    segments: list[Segment] = field(default_factory=list)  # body, excluding header/trailer
    trailer: Segment | None = None
    group: Group | None = None
    interchange: Interchange | None = None
    issues: list[Issue] = field(default_factory=list)  # issues raised while this message was open
    body_count: int = 0  # body segments seen (can exceed len(segments) when truncated by a limit)
    truncated: bool = False  # True if a max_message_segments limit stopped storing body segments

    @property
    def standard(self) -> str:
        return self.header.dialect.standard

    @property
    def type(self) -> str:
        h = self.header
        if self.standard == X12:
            return h.value(1)
        if self.standard == EDIFACT:
            return h.value(2, 1)
        if self.standard == TRADACOMS:
            return h.value(2, 1)
        return "^".join(c for c in h.components(9) if c)  # HL7 MSH-9, e.g. ADT^A01

    @property
    def control(self) -> str:
        h = self.header
        if self.standard == HL7:
            return h.value(10)
        return h.value(2) if self.standard == X12 else h.value(1)

    @property
    def version(self) -> str | None:
        h = self.header
        if self.standard == X12:
            return h.value(3) or (self.group.version if self.group else None)
        if self.standard == EDIFACT:
            return ".".join(c for c in h.components(2)[1:3] if c) or None  # D.96A
        if self.standard == TRADACOMS:
            return h.value(2, 2) or None
        return h.value(12, 1) or None  # MSH-12.1 (e.g. "2.5" from "2.5^USA")

    def all_segments(self) -> list[Segment]:
        return [self.header, *self.segments, *([self.trailer] if self.trailer else [])]


@dataclass(eq=False, slots=True)
class Group:
    """X12 GS/GE, EDIFACT UNG/UNE, HL7 BHS/BTS. ``header`` is None for an implicit group."""
    header: Segment | None = None
    messages: list[Message] = field(default_factory=list)  # empty when streaming
    trailer: Segment | None = None
    message_count: int = 0

    @property
    def type(self) -> str | None:
        # HL7 BHS has no document-type field (BHS-1 is the field separator).
        if not self.header or self.header.dialect.standard == HL7:
            return None
        return self.header.value(1)

    @property
    def control(self) -> str | None:
        if not self.header:
            return None
        n = {X12: 6, EDIFACT: 5, HL7: 11}.get(self.header.dialect.standard)
        return (self.header.value(n) or None) if n else None

    @property
    def version(self) -> str | None:
        if self.header and self.header.dialect.standard == X12:
            return self.header.value(8) or None
        return None


@dataclass(eq=False, slots=True)
class Interchange:
    """X12 ISA/IEA, EDIFACT [UNA] UNB/UNZ, TRADACOMS STX/END, HL7 FHS/FTS (or implicit)."""
    dialect: Dialect
    header: Segment | None = None
    service_string: Segment | None = None  # EDIFACT UNA
    groups: list[Group] = field(default_factory=list)  # empty when streaming
    loose: list[Segment] = field(default_factory=list)  # inside the interchange but outside groups (e.g. X12 TA1)
    trailer: Segment | None = None
    group_count: int = 0  # explicit groups (with a header)
    message_count: int = 0
    issues: list[Issue] = field(default_factory=list)  # envelope issues outside any message

    @property
    def messages(self) -> list[Message]:
        return [m for g in self.groups for m in g.messages]

    def _get(self, x12: tuple, edifact: tuple, tradacoms: tuple) -> str | None:
        if not self.header:
            return None
        spec = {X12: x12, EDIFACT: edifact, TRADACOMS: tradacoms}.get(self.dialect.standard)
        return (self.header.value(*spec).strip() or None) if spec else None

    @property
    def sender(self) -> str | None:
        return self._get((6,), (2, 1), (2, 1))

    @property
    def receiver(self) -> str | None:
        return self._get((8,), (3, 1), (3, 1))

    @property
    def control(self) -> str | None:
        return self._get((13,), (5,), (5, 1))

    @property
    def date(self) -> str | None:
        return self._get((9,), (4, 1), (4, 1))


@dataclass(eq=False)
class Document:
    prefix: str  # anything before the first segment (BOM, banners); kept for round-tripping
    segments: list[Segment]  # every segment in file order
    encoding: str
    interchanges: list[Interchange] = field(default_factory=list)
    stray: list[Segment] = field(default_factory=list)  # segments outside any interchange
    issues: list[Issue] = field(default_factory=list)

    @property
    def messages(self) -> list[Message]:
        return [m for ic in self.interchanges for m in ic.messages]

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    def to_text(self) -> str:
        return self.prefix + "".join(s.raw for s in self.segments)

    def to_bytes(self) -> bytes:
        return self.to_text().encode(self.encoding, "surrogateescape")


# -- streaming events ----------------------------------------------------------------

@dataclass(eq=False, slots=True)
class MessageEvent:
    """A complete business document, emitted as soon as its trailer (or the next header) is read."""
    message: Message
    sequence: int  # 1-based position of the message in the stream
    kind = "message"


@dataclass(eq=False, slots=True)
class InterchangeEvent:
    """Emitted when an interchange closes, with its control-total checks done."""
    interchange: Interchange
    kind = "interchange"


@dataclass(eq=False)
class IssueEvent:
    """A problem outside any interchange (stray segments, leading junk)."""
    issue: Issue
    kind = "issue"


Event = MessageEvent | InterchangeEvent | IssueEvent
