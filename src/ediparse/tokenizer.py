"""Split EDI text into segments without any schema knowledge.

``Tokenizer`` is incremental: feed it text in chunks of any size and it returns
each segment as soon as the segment (and the line break after it) is complete,
keeping only the unfinished tail in memory. Delimiters are re-detected at every
interchange header, so a file holding several interchanges with different
delimiters (or different standards) tokenizes correctly.

Work and memory are linear in the input: terminator searches resume where they
stopped, segments and headers have hard size limits (oversized ones are reported
and skipped), and skipped text is only kept when the caller retains the tree.
"""
from __future__ import annotations

import re

from .dialect import (HEADER_RE, HL7, TRADACOMS, Dialect, EDIDetectionError, TruncatedHeader,
                      header_dialect)
from .model import Issue, Segment, split_escaped
from .textutil import display, has_escaped_bytes

_TAG_RE = re.compile(r"^[A-Z0-9]{2,3}$")
_HL7_TERMINATOR = re.compile(r"[\r\n]")
_WHITESPACE = " \t\r\n"
_HL7_BETWEEN = _WHITESPACE + "\x0b\x1c"  # MLLP framing bytes between HL7 messages are never data
_MIN_LOOKAHEAD = 9  # longest fixed header prefix we must see before deciding (UNA + 6 service chars)
_KEEP_TAIL = 9  # when searching for a header: 8 chars that may start one, plus 1 of lookbehind context
DEFAULT_MAX_SEGMENT = 16 * 1024 * 1024  # characters


def _find_terminator(text: str, start: int, seg_start: int, dialect: Dialect) -> tuple[int, int]:
    """Search for the segment terminator from ``start`` (the segment began at ``seg_start``).

    Returns (end, terminator_length); end == len(text) and length 0 if no terminator yet.
    """
    if dialect.standard == HL7:
        m = _HL7_TERMINATOR.search(text, start)
        return (m.start(), 1) if m else (len(text), 0)
    term, rel = dialect.segment, dialect.release
    i = text.find(term, start)
    while i != -1 and rel:
        # A terminator preceded by an odd run of release characters is literal.
        j = i
        while j > seg_start and text[j - 1] == rel:
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
    """Incremental tokenizer. ``feed()`` and ``close()`` return a list of Segment and Issue items in order.

    ``keep_prefix`` keeps every skipped character (junk, empty segments, damaged regions) attached to the
    prefix or to the previous segment's ``raw``, so a retained Document round-trips byte for byte.
    Streaming callers pass False and those characters are only counted.
    """

    def __init__(self, keep_prefix: bool = True, max_segment: int = DEFAULT_MAX_SEGMENT):
        self._keep_prefix = keep_prefix
        self._max_segment = max_segment
        self._prefix_parts: list[str] = []
        self._extra: list[str] = []  # skipped text waiting to be appended to self._last.raw (retained mode)
        self._buf = ""
        self._base = 0  # stream offset of _buf[0]
        self._started = False
        self._search_from = 0  # where the next first-header search starts in _buf
        self._scan_rel = 0  # terminator search for the pending segment resumes this far past its start
        self._junk = 0  # non-blank characters seen before the first header
        self._dialect: Dialect | None = None
        self._pending_una: Dialect | None = None
        self._index = 0
        self._last: Segment | None = None
        self._warned_wrap = False
        self._warned_bytes = False
        self._in_empty_run = False
        self._empty_re: dict[Dialect, re.Pattern] = {}
        self._between: dict[Dialect, re.Pattern] = {}
        self._between_for: Dialect | None = None
        self._between_rx: re.Pattern | None = None
        # Recovery: skip to the next interchange header after an unreadable header or oversized segment.
        self._skip_error: str | None = None
        self._skip_code = "bad_header"
        self._skip_first = False  # still positioned on the bad text itself
        self._skipped = 0

    @property
    def started(self) -> bool:
        """True once the first interchange header has been found."""
        return self._started

    @property
    def prefix(self) -> str:
        """Text before the first segment (kept only with keep_prefix, for round-tripping)."""
        return "".join(self._prefix_parts)

    def feed(self, text: str) -> list[Segment | Issue]:
        self._buf += text
        return self._drain(final=False)

    def close(self) -> list[Segment | Issue]:
        out = self._drain(final=True)
        self._flush_extra()
        return out

    # ------------------------------------------------------------------------
    def _keep(self, text: str) -> None:
        """Keep skipped text for round-tripping: after the previous segment, or in the prefix."""
        if not self._keep_prefix or not text:
            return
        (self._extra if self._last else self._prefix_parts).append(text)

    def _flush_extra(self) -> None:
        if self._extra and self._last:
            self._last.raw += "".join(self._extra)  # one join per segment: linear overall
            self._extra.clear()

    def _skip_prefix(self, text: str) -> None:
        self._keep(text)
        self._junk += sum(1 for c in text if c not in _WHITESPACE and c != "﻿")

    def _enter_recovery(self, code: str, error: str) -> None:
        self._skip_code, self._skip_error = code, error
        self._skip_first, self._skipped, self._scan_rel = True, 0, 0

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
        self._keep(text)
        if not (m or final):
            return [], end
        error, self._skip_error = self._skip_error, None
        if not m and self._index == 0:
            raise EDIDetectionError(error)  # nothing usable anywhere in the input
        where = "the next interchange header" if m else "the end of the input"
        if self._index == 0 and self._skip_code == "bad_header":
            # Nothing parsed yet: a header-like word in leading junk (e.g. "Subject: ISA, GS ..."), not damage.
            issue = Issue("warning", "leading_data",
                          f"Ignored {self._skipped} characters before the first readable header ({error})")
        else:
            issue = Issue("error", self._skip_code, f"{error}; skipped {self._skipped} characters to {where}",
                          self._index)
        return [issue], end

    def _between_re(self, d: Dialect) -> re.Pattern:
        """Characters that may sit between segments: whitespace that isn't a delimiter (HL7: also MLLP framing)."""
        if d is not self._between_for:  # identity check: hashing the Dialect per segment was a hot spot
            rx = self._between.get(d)
            if rx is None:
                chars = _HL7_BETWEEN if d.standard == HL7 else "".join(c for c in _WHITESPACE if c not in d.delimiters)
                rx = self._between[d] = re.compile(f"[{re.escape(chars)}]*")
            self._between_for, self._between_rx = d, rx
        return self._between_rx

    def _empty_run(self, buf: str, pos: int, final: bool, d: Dialect) -> tuple[list[Segment | Issue], int] | None:
        """Fast path for a run of empty segments (doubled terminators): one regex match instead of one pass each.

        Gives exactly what the per-segment path would: the text is kept for round-tripping, one notice per run.
        """
        rx = self._empty_re.get(d)
        if rx is None:
            between = "".join(c for c in _WHITESPACE if c not in d.delimiters)
            rx = self._empty_re[d] = re.compile(f"(?:{re.escape(d.segment)}[{re.escape(between)}]*)+")
        end = rx.match(buf, pos).end()
        if end == len(buf) and not final:
            # Leave the last empty segment for later: more line-break characters may still follow it.
            end = buf.rfind(d.segment, pos, end)
            if end <= pos:
                return None
        issues: list[Segment | Issue] = []
        if not self._in_empty_run:
            issues.append(Issue("warning", "empty_segment", "Empty segment(s) skipped", self._index))
            self._in_empty_run = True
        self._keep(buf[pos:end])
        self._scan_rel = 0
        return issues, end

    def _next(self, buf: str, pos: int, final: bool) -> tuple[list[Segment | Issue], int] | None:
        """Tokenize one segment at ``pos``; None means "wait for more input". No state changes before commit."""
        n = len(buf)
        d = self._dialect
        if d is not None:
            # Whitespace between segments (e.g. the rest of a long padding run that arrived in a later
            # chunk) belongs to the previous segment's raw text; it never starts a segment.
            end = self._between_re(d).match(buf, pos).end()
            if end > pos:
                self._keep(buf[pos:end])
                return [], end
            if d.standard != HL7 and buf.startswith(d.segment, pos):
                return self._empty_run(buf, pos, final, d)
        if not final and n - pos < _MIN_LOOKAHEAD:
            return None
        try:
            found, warnings = header_dialect(buf, pos, self._pending_una, final)
        except EDIDetectionError as e:
            if isinstance(e, TruncatedHeader) and not final:  # bounded: header detection gives up at MAX_HEADER
                return None
            self._enter_recovery("bad_header", f"{e} (header at offset {self._base + pos})")
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
            end, term_len = _find_terminator(buf, pos + self._scan_rel, pos, dialect)
            if end - pos > self._max_segment:
                self._enter_recovery("oversized_segment",
                                     f"Segment at offset {self._base + pos} is longer than {self._max_segment} "
                                     "characters (or has no terminator)")
                return [], pos
            if term_len == 0 and not final:
                self._scan_rel = n - pos  # resume here when more input arrives: no rescanning
                return None
            body = buf[pos:end]
            if term_len == 0 and body.strip(_WHITESPACE):
                issues.append(Issue("warning", "unterminated_segment", "Last segment has no terminator", self._index))
            if dialect.standard != HL7 and ("\n" in body or "\r" in body) and not {"\r", "\n"} & dialect.delimiters:
                # Hard-wrapped files (e.g. 80-column) break lines mid-segment.
                body = re.sub(r"[\r\n]", "", body)
                wrapped = True
            tag, elements = _split(body.rstrip(_WHITESPACE) if term_len == 0 else body, dialect)

        # Absorb line breaks between segments; HL7 blank lines and MLLP framing are never data. If the run
        # continues into the next chunk, the rest is absorbed at the start of the next _next() call.
        after = self._between_re(dialect).match(buf, end + term_len).end()

        # -- commit ------------------------------------------------------------
        self._scan_rel = 0
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
            # Empty segment (e.g. doubled terminator): text kept for round-tripping only; one notice per run.
            if not self._in_empty_run:
                issues.append(Issue("warning", "empty_segment", "Empty segment(s) skipped", self._index))
                self._in_empty_run = True
            self._keep(raw)
            return issues, after

        self._in_empty_run = False
        tag = display(tag)
        if not _TAG_RE.match(tag):
            issues.append(Issue("warning", "unusual_tag", f"Unusual segment tag {tag!r}", self._index))
        if not self._warned_bytes and has_escaped_bytes(raw):
            # Checked as the segment is created: text appended to raw later (skipped regions) must not count,
            # or the notice's position would depend on chunking (found by fuzzing).
            self._warned_bytes = True
            issues.append(Issue("info", "invalid_utf8",
                                "Input is not valid UTF-8; invalid bytes are read as Latin-1", self._index))
        self._flush_extra()  # skipped text before this segment belongs to the previous one's raw
        seg = Segment(tag, elements, raw, self._base + pos, self._index, dialect)
        self._index += 1
        self._last = seg
        return [*issues, seg], after


def tokenize(text: str) -> tuple[str, list[Segment], list[Issue]]:
    """Tokenize a complete text. Returns (prefix, segments, issues)."""
    t = Tokenizer()
    items = t.feed(text) + t.close()
    return (t.prefix, [i for i in items if isinstance(i, Segment)], [i for i in items if isinstance(i, Issue)])
