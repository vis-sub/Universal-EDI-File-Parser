"""Regressions from the pre-publication code review: resource limits, linear time, and parser edge cases."""
import time
import tracemalloc

import pytest

from ediparse import StreamParser, event_to_dict, parse_bytes, parse_text, stream

from conftest import x12

CH = 65536


def codes(events):
    out = []
    for e in events:
        iss = (e.message.issues if e.kind == "message" else e.interchange.issues if e.kind == "interchange"
               else [e.issue])
        out += [i.code for i in iss]
    return out


def isa_only():
    return x12("ST*850*0001~SE*2*0001").split("GS*")[0]


# -- linear time and bounded memory ------------------------------------------------------------

def test_no_terminator_is_bounded_and_recovers():
    """Was quadratic with unbounded memory (64 MB: 10 s, 3 GB)."""
    good = x12("ST*850*0007~SE*2*0007", ctrl=7)

    def source():
        yield (isa_only() + "GS*").encode()
        for _ in range(32 * 1024 * 1024 // CH):
            yield b"A" * CH
        yield ("\n" + good).encode()

    tracemalloc.start()
    t = time.time()
    events = list(stream(source(), max_segment=1024 * 1024))
    elapsed, (_, peak) = time.time() - t, tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert elapsed < 10 and peak < 8_000_000, (elapsed, peak)
    assert "oversized_segment" in codes(events)
    assert [e.message.control for e in events if e.kind == "message"] == ["0007"]  # recovered at the next ISA


def test_oversized_segment_is_chunking_independent():
    text = isa_only() + "GS*" + "B" * 5000 + "~" + x12("ST*850*0001~SE*2*0001", ctrl=2)
    whole = [event_to_dict(e) for e in stream([text], max_segment=1000)]
    for size in (1, 7, 100, 999, 1001):
        chunks = [text[i:i + size] for i in range(0, len(text), size)]
        assert [event_to_dict(e) for e in stream(chunks, max_segment=1000)] == whole, size


def test_runs_of_empty_segments_are_fast_and_reported_once():
    """Was quadratic (640k empties: 5 s) with one issue object each."""
    text = x12("ST*850*0001~BEG*00*SA*PO1**20261001~SE*3*0001").replace("BEG", "~" * 2_000_000 + "BEG", 1)
    t = time.time()
    events = list(stream([text[i:i + CH] for i in range(0, len(text), CH)]))
    assert time.time() - t < 5
    assert codes(events).count("empty_segment") == 1
    data = text.encode()
    assert parse_bytes(data).to_bytes() == data


def test_hl7_whole_document_parse_is_linear():
    """Was quadratic for parse_text/parse_file (4 MB HL7: 15.6 s)."""
    hl7 = "".join(f"MSH|^~\\&|A|B|C|D|2026||ADT^A01|{i}|P|2.5\rPID|1||MRN{i}\r" for i in range(60_000))
    t = time.time()
    doc = parse_text(hl7)
    assert time.time() - t < 5 and len(doc.messages) == 60_000


def test_damaged_region_is_not_retained_when_streaming():
    good, bad = x12("ST*850*0001~SE*2*0001", ctrl=1), x12("ST*850*0002~SE*2*0002", ctrl=2)

    def source():
        yield (good + bad[:105] + "Z").encode()
        for _ in range(32 * 1024 * 1024 // CH):
            yield b"B" * CH

    tracemalloc.start()
    events = list(stream(source()))
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert peak < 4_000_000 and "bad_header" in codes(events)


def test_overlong_isa_decides_the_same_whole_or_chunked():
    text = "ISA" + "*" + "x" * 5000 + "*" * 15 + ":~" + x12("ST*850*0001~SE*2*0001")
    whole = [event_to_dict(e) for e in stream([text])]
    for size in (64, 4096, 5000):
        assert [event_to_dict(e) for e in stream([text[i:i + size] for i in range(0, len(text), size)])] == whole


# -- limits on what a streamed message/interchange keeps ----------------------------------------

def test_max_message_segments_truncates_but_keeps_counts_right():
    body = "~".join(f"REF*ZZ*{i}" for i in range(500))
    events = list(stream([x12(f"ST*850*0001~{body}~SE*502*0001")], max_message_segments=100))
    msg = events[0].message
    assert len(msg.segments) == 100 and msg.body_count == 500 and msg.truncated
    assert [i.code for i in msg.issues] == ["message_truncated"]  # SE01 still checked against all 500
    rec = event_to_dict(events[0])
    assert rec["message"]["truncated"] is True and rec["message"]["segment_count"] == 502 and not rec["valid"]


def test_issue_cap_per_scope():
    loose = "~".join(f"N1*ST*{i}" for i in range(2000))
    text = x12("ST*850*0001~SE*2*0001").replace("GE*", loose + "~GE*", 1)
    ic = list(stream([text], max_issues=10))[-1].interchange
    assert len(ic.issues) == 11 and ic.issues[-1].code == "too_many_issues"
    assert len(ic.loose) <= 1000


def test_limits_do_not_apply_to_retained_documents():
    body = "~".join(f"REF*ZZ*{i}" for i in range(500))
    p = StreamParser(retain=True, max_message_segments=10)
    p.feed(x12(f"ST*850*0001~{body}~SE*502*0001"))
    p.close()
    assert len(p.document().messages[0].segments) == 500


# -- parser edge cases ---------------------------------------------------------------------------

@pytest.mark.parametrize("banner", ["Status: UNAVAILABLE retry\n", "Subject: ISA, GS envelope\n", "UNA \n"])
def test_header_words_in_leading_junk(banner):
    doc = parse_text(banner + x12("ST*850*0001~SE*2*0001"))
    assert len(doc.messages) == 1 and doc.errors == []


def test_hl7_short_encoding_field_uses_correct_defaults():
    doc = parse_text("MSH|^~|A|B|C|D|2026||ADT^A01|1|P|2.5^USA\rPID|1||DOE^E^JOHN\r")
    d = doc.interchanges[0].dialect
    assert (d.component, d.repetition, d.escape, d.subcomponent) == ("^", "~", "\\", "&")
    assert doc.messages[0].segments[0].components(3) == ["DOE", "E", "JOHN"]
    assert doc.messages[0].version == "2.5"


def test_hl7_batch_group_has_no_type():
    doc = parse_text("BHS|^~\\&|A|B|C|D|2026||||BATCH1\rMSH|^~\\&|A|B|C|D|2026||ADT^A01|1|P|2.5\rBTS|1\r")
    grp = doc.interchanges[0].groups[0]
    assert grp.type is None and grp.control == "BATCH1"


def test_hl7_mllp_framing():
    m1 = "MSH|^~\\&|A|B|C|D|2026||ADT^A01|1|P|2.5\rPID|1||X\r"
    m2 = "MSH|^~\\&|A|B|C|D|2026||ADT^A08|2|P|2.5\rPID|1||Y\r"
    doc = parse_text(f"\x0b{m1}\x1c\r\x0b{m2}\x1c\r")
    assert [m.control for m in doc.messages] == ["1", "2"] and doc.errors == []


def test_una_repetition_reserved_before_syntax_4():
    text = "UNA:+.?*'UNB+UNOA:3+S+R+261003:1200+1'UNH+1+ORDERS:D:96A:UN'FTX+AAI+++a*b'UNT+3+1'UNZ+1+1'"
    doc = parse_text(text)
    assert doc.messages[0].segments[0].repeats(4) == ["a*b"]


def test_explicit_codec_never_fails_and_round_trips():
    data = x12("ST*850*0001~N1*ST*€ X~SE*3*0001").encode("cp1252") + b"\x81"  # 0x81 is undefined in cp1252
    events = list(stream(data, encoding="cp1252"))
    assert events[0].message.segments[0].value(2) == "€ X"
    p = StreamParser("cp1252", retain=True)
    p.feed(data)
    p.close()
    assert p.document().to_bytes() == data
