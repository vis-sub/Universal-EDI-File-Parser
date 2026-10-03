"""Detect the EDI standard and its delimiters from the segment that opens an interchange.

Every supported standard announces its own syntax at a known position, so no
partner configuration is needed:

    X12        ISA  element sep = char 4; component sep and segment terminator
                    follow the 16th element separator; ISA11 is the repetition
                    separator from version 00402 on.
    EDIFACT    UNA  six service characters; without UNA, the syntax defaults.
    TRADACOMS  STX= fixed delimiters.
    HL7 v2     MSH  field sep = char 4, encoding characters follow.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, replace

from .textutil import display

X12, EDIFACT, TRADACOMS, HL7 = "X12", "EDIFACT", "TRADACOMS", "HL7"

_HL7_ESCAPES = {"F": "element", "S": "component", "T": "subcomponent", "R": "repetition", "E": "escape"}


class EDIDetectionError(ValueError):
    """The input does not start an interchange in any supported standard."""


class TruncatedHeader(EDIDetectionError):
    """A header is cut off; when streaming, more input may complete it."""


@dataclass(frozen=True)
class Dialect:
    standard: str
    element: str
    component: str
    segment: str  # terminator; HL7 also accepts \n and \r\n
    repetition: str | None = None
    release: str | None = None  # makes the next character literal (EDIFACT/TRADACOMS '?')
    tag_separator: str | None = None  # TRADACOMS '=' between tag and first element
    subcomponent: str | None = None  # HL7 '&'
    escape: str | None = None  # HL7 '\' escape-sequence character
    version: str | None = None  # X12 ISA12, EDIFACT syntax version

    @property
    def delimiters(self) -> frozenset[str]:
        chars = (self.element, self.component, self.segment, self.repetition, self.release,
                 self.tag_separator, self.subcomponent, self.escape)
        return frozenset(c for c in chars if c)

    def element_id(self, tag: str, n: int) -> str:
        return f"{tag}-{n}" if self.standard == HL7 else f"{tag}{n:02d}"

    def unescape(self, value: str) -> str:
        if self.release and self.release in value:
            out, i = [], 0
            while i < len(value):
                if value[i] == self.release and i + 1 < len(value):
                    i += 1
                out.append(value[i])
                i += 1
            return "".join(out)
        if self.escape and self.escape in value:
            def sub(m: re.Match) -> str:
                attr = _HL7_ESCAPES.get(m.group(1))
                return (getattr(self, attr) or m.group(0)) if attr else m.group(0)
            esc = re.escape(self.escape)
            return re.sub(f"{esc}([FSTRE]){esc}", sub, value)
        return value

    def describe(self) -> dict:
        return {k: display(v) if isinstance(v, str) else v for k, v in {
            "standard": self.standard, "version": self.version, "element": self.element,
            "component": self.component, "segment": self.segment, "repetition": self.repetition,
            "release": self.release, "tag_separator": self.tag_separator,
            "subcomponent": self.subcomponent, "escape": self.escape,
        }.items() if v is not None}


TRADACOMS_DIALECT = Dialect(TRADACOMS, "+", ":", "'", release="?", tag_separator="=")

# Where an interchange can start. Used to skip junk (mail headers, banners) before the first one.
# Note: str.isspace()/\s treat \x1c-\x1f as whitespace, but X12 senders use them as delimiters.
_BLANK = " \t\r\n"
# The lookbehind stops text such as "VISA*" inside data being taken for an ISA header.
HEADER_RE = re.compile(r"(?<![A-Za-z0-9])(?:ISA[^\w \t\r\n]|UNA|UNB\+|STX=|(?:MSH|FHS|BHS)[^\w \t\r\n])")


def _is_delimiter_char(c: str) -> bool:
    return bool(c) and not c.isalnum() and c not in _BLANK


def x12_from_isa(text: str, pos: int) -> tuple[Dialect, list[str]]:
    """Read delimiters from an ISA segment starting at ``pos``. Returns (dialect, warnings)."""
    elem = text[pos + 3]
    # ISA is nominally fixed width (16th separator at offset 103), but tolerate
    # senders that trim padding by counting separators instead of trusting offsets.
    i = pos
    for _ in range(16):
        i = text.find(elem, i + 1)
        if i == -1:
            raise TruncatedHeader("Truncated ISA segment")
    if i + 2 >= len(text):
        raise TruncatedHeader("Truncated ISA segment")
    comp, term = text[i + 1], text[i + 2]
    if term.isalnum():
        raise EDIDetectionError(f"Invalid X12 segment terminator {term!r} in ISA")
    fields = text[pos:i].split(elem)  # ['ISA', ISA01 .. ISA15]
    version, isa11 = display(fields[12]), fields[11]
    warnings = []
    if i - pos != 103:
        warnings.append(f"ISA is {i - pos + 3} characters, not the standard 106 (padding trimmed or extended)")
    rep = None
    if version >= "00402":
        if len(isa11) == 1 and _is_delimiter_char(isa11) and isa11 not in (elem, comp, term):
            rep = isa11
        else:
            warnings.append(f"ISA11 {isa11!r} is not a usable repetition separator; repetitions disabled")
    return Dialect(X12, elem, comp, term, repetition=rep, version=version), warnings


def edifact_from_una(text: str, pos: int) -> Dialect:
    s = text[pos + 3:pos + 9]
    if len(s) < 6:
        raise TruncatedHeader("Truncated UNA segment")
    comp, elem, _decimal, rel, rep, term = s
    return Dialect(EDIFACT, elem, comp, term,
                   repetition=None if rep == " " else rep,
                   release=None if rel == " " else rel)


def _unb_syntax_version(text: str, pos: int, elem: str = "+", comp: str = ":") -> str | None:
    m = re.match(f"UNB{re.escape(elem)}[A-Z]{{4}}{re.escape(comp)}(\\d)", text[pos:pos + 12])
    return m.group(1) if m else None


def edifact_default(text: str, pos: int) -> Dialect:
    # Syntax version 4 adds '*' as the default repetition separator.
    version = _unb_syntax_version(text, pos)
    return Dialect(EDIFACT, "+", ":", "'", repetition="*" if version == "4" else None,
                   release="?", version=version)


def hl7_from_header(text: str, pos: int, final: bool = True) -> Dialect:
    field = text[pos + 3]
    end = text.find(field, pos + 4)
    if end == -1 and not final:
        raise TruncatedHeader(f"Truncated {text[pos:pos + 3]} segment")
    enc = text[pos + 4:end if end != -1 else pos + 8]
    enc = (enc + "^~\\&")[:4] if len(enc) < 4 else enc
    return Dialect(HL7, field, enc[0], "\r", repetition=enc[1], escape=enc[2], subcomponent=enc[3])


def header_dialect(text: str, pos: int, pending_una: Dialect | None,
                   final: bool = True) -> tuple[Dialect | None, list[str]]:
    """If an interchange/message header starts at ``pos``, return its dialect.

    Raises TruncatedHeader if the header is cut off (``final`` False means more text may follow).
    """
    head, nxt = text[pos:pos + 3], text[pos + 3:pos + 4]
    if head == "ISA" and _is_delimiter_char(nxt):
        return x12_from_isa(text, pos)
    if head == "UNA":
        return edifact_from_una(text, pos), []
    if head == "UNB":
        if pending_una and nxt == pending_una.element:
            version = _unb_syntax_version(text, pos, pending_una.element, pending_una.component)
            return replace(pending_una, version=version), []
        if nxt == "+":
            return edifact_default(text, pos), []
    if head == "STX" and nxt == "=":
        return TRADACOMS_DIALECT, []
    if head in ("MSH", "FHS", "BHS") and _is_delimiter_char(nxt):
        return hl7_from_header(text, pos, final), []
    return None, []
