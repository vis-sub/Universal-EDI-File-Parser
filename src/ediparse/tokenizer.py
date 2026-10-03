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
from .textutil import display, has_escaped_bytes

_TAG_RE = re.compile(r"^[A-Z0-9]{2,3}$")
_WHITESPACE = " \t\r\n"
_MIN_LOOKAHEAD = 9  # longest fixed header prefix we must see before deciding (UNA + 6 service chars)
_KEEP_TAIL = 9  # when searching for a header: 8 chars that may start one, plus 1 of lookbehind context


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
        self._search_from = 0  # where the next first-header search starts in _buf
        self._junk = 0  # non-blank characters seen before the first header
        self._dialect: Dialect | None = None
        self._pending_una: Dialect | None = None
        self._index = 0
        self._last: Segment | None = None
        self._warned_wrap = False
        self._warned_bytes = False
        # Recovery from an unreadable interchange header: skip to the next header.
        self._skip_error: str | None = None
        self._skip_first = False  # still positioned on the bad header itself
        self._skipped = 0

    def feed(self, text: str) -> list[Segment | Issue]:
        self._buf += text
        return self._drain(final=False)

    def close(self) -> list[Segment | Issue]:
        return self._drain(final=True)

    # ------------------------------------------------------------------------
    def _skip_prefix(self, text: str) -> None:
        if self._keep_prefix:
            self.prefix += text
        self._junk += sum(1 for c in text if c not in _WHITESPACE and c != "\ufeff")

    def _drain(self, final: bool) -> list[Segment | Issue]:
        out: list[Segment | Issue] = []
        buf, pos = self._buf, 0
        if not self._started:
            m = HEADER_RE.search(buf, self._search_from)
            if not m:
                if final:
                    raise EDIDetectionError("No EDI interchange header found (expected ISA, UNA/UNB, STX or MSH)")
                # Keep a tail in case a header straddles the next chunk, plus one character before it
                # so HEADER_RE's lookbehind still sees what precedes a candidate header.
                cut = max(0, len(buf) - _KEEP_TAIL)
                if cut:
                    self._skip_prefix(buf[:cut])
                    self._buf, self._base, self._search_from = buf[cut:], self._base + cut, 1
                return out
            self._skip_prefix(buf[:m.start()])
            if self._junk:
                out.append(Issue("warning", "leading_data",
                                 f"Ignored {self._junk} non-blank characters before the first header"))
            pos, self._started = m.start(), True

        while pos < len(buf):
            step = self._skip(buf, pos, final) if self._skip_error else self._next(buf, pos, final)
            if step is None:
                break
            items, pos = step
            out += items
        self._buf, self._base = buf[pos:], self._base + pos
        return out

    def _skip(self, buf: str, pos: int, final: bool) -> tuple[list[Segment | Issue], int] | None:
        """Recovery mode: drop text up to the next interchange header. None means "wait for more input"."""
        m = HEADER_RE.search(buf, pos + 1 if self._skip_first else pos)
        if m:
            end = m.start()
        elif final:
            end = len(buf)
        else:
            end = max(pos, len(buf) - _KEEP_TAIL)  # see _drain: tail + one char of lookbehind context
            if end == pos:
                return None
        text = buf[pos:end]
        self._skipped += len(text)
        # After a cut, buf[end] is lookbehind context that was already searched, so the next search starts after it.
        self._skip_first = not (m or final)
        if self._last:
            self._last.raw += text  # kept for round-tripping
        elif self._keep_prefix:
            self.prefix += text
        if not (m or final):
            return [], end
        error, self._skip_error = self._skip_error, None
        if not m and self._index == 0:
            raise EDIDetectionError(error)  # nothing usable anywhere in the input
        where = "the next interchange header" if m else "the end of the input"
        return [Issue("error", "bad_header", f"{error}; skipped {self._skipped} characters to {where}",
                      self._index)], end

    def _next(self, buf: str, pos: int, final: bool) -> tuple[list[Segment | Issue], int] | None:
        """Tokenize one segment at ``pos``; None means "wait for more input". No state changes before commit."""
        n = len(buf)
        if not final and n - pos < _MIN_LOOKAHEAD:
            return None
        try:
            found, warnings = header_dialect(buf, pos, self._pending_una, final)
        except EDIDetectionError as e:
            if isinstance(e, TruncatedHeader) and not final:
                return None
            self._skip_error = f"{e} (header at offset {self._base + pos})"
            self._skip_first, self._skipped = True, 0
            return [], pos  # _drain continues in recovery mode
        dialect = found or self._dialect
        if dialect is None:  # unreachable: tokenizing always starts on a header
            raise EDIDetectionError(f"Cannot determine delimiters at offset {self._base + pos}")
        issues = [Issue("warning", "isa_format", w, self._index) for w in warnings]

        wrapped = False
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
                wrapped = True
            tag, elements = _split(body.rstrip(_WHITESPACE) if term_len == 0 else body, dialect)

        after = end + term_len
        # Absorb line breaks between segments; HL7 blank lines are never data.
        while after < n and buf[after] in _WHITESPACE and (
                buf[after] not in dialect.delimiters or dialect.standard == HL7):
            after += 1
        if after == n and not final:
            return None  # more line-break characters may follow in the next chunk

        # -- commit ------------------------------------------------------------
        if wrapped and not self._warned_wrap:
            issues.append(Issue("info", "wrapped_lines", "Line breaks inside segments were ignored", self._index))
            self._warned_wrap = True
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

        tag = display(tag)
        if not _TAG_RE.match(tag):
            issues.append(Issue("warning", "unusual_tag", f"Unusual segment tag {tag!r}", self._index))
        if not self._warned_bytes and has_escaped_bytes(raw):
            # Checked as the segment is created: text appended to raw later (skipped regions) must not count,
            # or the notice's position would depend on chunking (found by fuzzing).
            self._warned_bytes = True
            issues.append(Issue("info", "invalid_utf8",
                                "Input is not valid UTF-8; invalid bytes are read as Latin-1", self._index))
        seg = Segment(tag, elements, raw, self._base + pos, self._index, dialect)
        self._index += 1
        self._last = seg
        return [*issues, seg], after


def tokenize(text: str) -> tuple[str, list[Segment], list[Issue]]:
    """Tokenize a complete text. Returns (prefix, segments, issues)."""
    t = Tokenizer()
    items = t.feed(text) + t.close()
    return (t.prefix, [i for i in items if isinstance(i, Segment)], [i for i in items if isinstance(i, Issue)])
