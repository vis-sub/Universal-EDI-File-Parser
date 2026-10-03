"""Split EDI text into segments without any schema knowledge.

``Tokenizer`` is incremental: feed it text in chunks of any size and it returns
each segment as soon as the segment (and the line break after it) is complete,
keeping only the unfinished tail in memory. Delimiters are re-detected at every
interchange header, so a file holding several interchanges with different
delimiters (or different standards) tokenizes correctly.
"""
from __future__ import annotations

import re

from .dialect import HEADER_RE, HL7, TRADACOMS, Dialect, EDIDetectionError, TruncatedHeader, header_dialect
from .model import Issue, Segment, split_escaped

_TAG_RE = re.compile(r"^[A-Z0-9]{2,3}$")
_WHITESPACE = " \t\r\n"
_MIN_LOOKAHEAD = 9  # longest fixed header prefix we must see before deciding (UNA + 6 service chars)


def _find_terminator(text: str, start: int, dialect: Dialect) -> tuple[int, int]:
    """Return (end, terminator_length); end == len(text) and length 0 if no terminator yet."""
    if dialect.standard == HL7:
        ends = [i for i in (text.find("\r", start), text.find("\n", start)) if i != -1]
        return (min(ends), 1) if ends else (len(text), 0)
    term, rel = dialect.segment, dialect.release
    i = text.find(term, start)
    while i != -1 and rel:
        # A terminator preceded by an odd run of release characters is literal.
        j = i
        while j > start and text[j - 1] == rel:
            j -= 1
        if (i - j) % 2 == 0:
            break
        i = text.find(term, i + 1)
    return (i, len(term)) if i != -1 else (len(text), 0)


def _split(body: str, dialect: Dialect) -> tuple[str, list[str]]:
    if dialect.standard == TRADACOMS:
        tag, _, rest = body.partition(dialect.tag_separator)
        return tag, split_escaped(rest, dialect.element, dialect.release)
    parts = split_escaped(body, dialect.element, dialect.release)
    tag, elements = parts[0], parts[1:]
    if dialect.standard == HL7 and tag in ("MSH", "FHS", "BHS"):
        elements = [dialect.element, *elements]  # MSH-1 is the field separator itself
    return tag, elements


class Tokenizer:
    """Incremental tokenizer. ``feed()`` and ``close()`` return a list of Segment and Issue items in order."""

    def __init__(self, keep_prefix: bool = True):
        self.prefix = ""  # text before the first header (kept only if keep_prefix, for round-tripping)
        self._keep_prefix = keep_prefix
        self._buf = ""
        self._base = 0  # stream offset of _buf[0]
        self._started = False
        self._junk = 0  # non-blank characters seen before the first header
        self._dialect: Dialect | None = None
        self._pending_una: Dialect | None = None
        self._index = 0
        self._last: Segment | None = None
        self._warned_wrap = False

    def feed(self, text: str) -> list[Segment | Issue]:
        self._buf += text
        return self._drain(final=False)

    def close(self) -> list[Segment | Issue]:
        return self._drain(final=True)

    # ------------------------------------------------------------------------
    def _skip_prefix(self, text: str) -> None:
        if self._keep_prefix:
            self.prefix += text
        self._junk += len(text.strip("﻿" + _WHITESPACE))

    def _drain(self, final: bool) -> list[Segment | Issue]:
        out: list[Segment | Issue] = []
        buf, pos = self._buf, 0
        if not self._started:
            m = HEADER_RE.search(buf)
            if not m:
                if final:
                    raise EDIDetectionError("No EDI interchange header found (expected ISA, UNA/UNB, STX or MSH)")
                cut = max(0, len(buf) - 8)  # a header may straddle the next chunk
                self._skip_prefix(buf[:cut])
                self._buf, self._base = buf[cut:], self._base + cut
                return out
            self._skip_prefix(buf[:m.start()])
            if self._junk:
                out.append(Issue("warning", "leading_data",
                                 f"Ignored {self._junk} non-blank characters before the first header"))
            pos, self._started = m.start(), True

        while pos < len(buf):
            step = self._next(buf, pos, final)
            if step is None:
                break
            items, pos = step
            out += items
        self._buf, self._base = buf[pos:], self._base + pos
        return out

    def _next(self, buf: str, pos: int, final: bool) -> tuple[list[Segment | Issue], int] | None:
        """Tokenize one segment at ``pos``; None means "wait for more input". No state changes before commit."""
        n = len(buf)
        if not final and n - pos < _MIN_LOOKAHEAD:
            return None
        try:
            found, warnings = header_dialect(buf, pos, self._pending_una, final)
        except TruncatedHeader:
            if not final:
                return None
            raise
        dialect = found or self._dialect
        if dialect is None:  # unreachable: tokenizing always starts on a header
            raise EDIDetectionError(f"Cannot determine delimiters at offset {self._base + pos}")
        issues = [Issue("warning", "isa_format", w, self._index) for w in warnings]

        is_una = buf.startswith("UNA", pos)
        if is_una:
            end, term_len = pos + 9, 0
            tag, elements = "UNA", [buf[pos + 3:pos + 9]]
        else:
            end, term_len = _find_terminator(buf, pos, dialect)
            if term_len == 0 and not final:
                return None
            body = buf[pos:end]
            if term_len == 0 and body.strip(_WHITESPACE):
                issues.append(Issue("warning", "unterminated_segment", "Last segment has no terminator", self._index))
            if dialect.standard != HL7 and ("\n" in body or "\r" in body) and not {"\r", "\n"} & dialect.delimiters:
                # Hard-wrapped files (e.g. 80-column) break lines mid-segment.
                body = re.sub(r"[\r\n]", "", body)
                if not self._warned_wrap:
                    issues.append(Issue("info", "wrapped_lines", "Line breaks inside segments were ignored",
                                        self._index))
                    self._warned_wrap = True
            tag, elements = _split(body.rstrip(_WHITESPACE) if term_len == 0 else body, dialect)

        after = end + term_len
        # Absorb line breaks between segments; HL7 blank lines are never data.
        while after < n and buf[after] in _WHITESPACE and (
                buf[after] not in dialect.delimiters or dialect.standard == HL7):
            after += 1
        if after == n and not final:
            return None  # more line-break characters may follow in the next chunk

        # -- commit ------------------------------------------------------------
        self._dialect = dialect
        if is_una:
            self._pending_una = dialect
        elif buf.startswith("UNB", pos):
            self._pending_una = None
        raw = buf[pos:after]

        if not tag.strip(_WHITESPACE) and not elements:
            # Empty segment (e.g. doubled terminator): keep the text for round-tripping only.
            issues.append(Issue("warning", "empty_segment", "Empty segment skipped", self._index))
            if self._last:
                self._last.raw += raw
            elif self._keep_prefix:
                self.prefix += raw
            return issues, after

        if not _TAG_RE.match(tag):
            issues.append(Issue("warning", "unusual_tag", f"Unusual segment tag {tag!r}", self._index))
        seg = Segment(tag, elements, raw, self._base + pos, self._index, dialect)
        self._index += 1
        self._last = seg
        return [*issues, seg], after


def tokenize(text: str) -> tuple[str, list[Segment], list[Issue]]:
    """Tokenize a complete text. Returns (prefix, segments, issues)."""
    t = Tokenizer()
    items = t.feed(text) + t.close()
    return (t.prefix, [i for i in items if isinstance(i, Segment)], [i for i in items if isinstance(i, Issue)])
