"""Messy real-world input: wrapping, missing terminators, multiple interchanges, bad totals."""
from ediparse import EDIFACT, X12, parse_bytes, parse_text


def codes(doc):
    return {i.code for i in doc.issues}


def test_wrapped_80_columns(make_x12):
    text = make_x12("ST*850*0001~BEG*00*SA*PO-10045**20261001~N1*ST*EXAMPLE DC~SE*4*0001", nl="")
    wrapped = "\n".join(text[i:i + 80] for i in range(0, len(text), 80))
    doc = parse_text(wrapped)
    assert doc.errors == []
    assert doc.messages[0].segments[0].value(3) == "PO-10045"
    assert "wrapped_lines" in codes(doc)
    assert doc.to_text() == wrapped


def test_missing_final_terminator(make_x12):
    text = make_x12("ST*850*0001~SE*2*0001").rstrip("~\n")
    doc = parse_text(text)
    assert doc.errors == []
    assert doc.segments[-1].tag == "IEA"
    assert "unterminated_segment" in codes(doc)
    assert doc.to_text() == text


def test_multiple_interchanges_with_different_delimiters(make_x12):
    a = make_x12("ST*850*0001~SE*2*0001", ctrl=1)
    b = make_x12("ST*850*0001~SE*2*0001", ctrl=2, elem="|", comp=">", seg="\x85", nl="")
    c = "UNB+UNOA:3+S+R+261003:1200+9'UNH+1+ORDERS:D:96A:UN'UNT+2+1'UNZ+1+9'"
    doc = parse_text(a + b + c)
    assert [(ic.dialect.standard, ic.dialect.element) for ic in doc.interchanges] == \
        [(X12, "*"), (X12, "|"), (EDIFACT, "+")]
    assert doc.errors == []


def test_leading_junk_and_bom(make_x12):
    text = "\ufeffFrom: partner@example.com\nSubject: orders\n\n" + make_x12("ST*850*0001~SE*2*0001")
    data = text.encode("utf-8")
    doc = parse_bytes(data)
    assert doc.errors == [] and "leading_data" in codes(doc)
    assert doc.to_bytes() == data


def test_latin1_bytes_round_trip(make_x12):
    data = make_x12("ST*850*0001~N1*ST*CAF\xc9 M\xdcLLER~SE*3*0001").encode("latin-1")
    doc = parse_bytes(data)
    assert doc.messages[0].segments[0].value(2) == "CAFÉ MÜLLER"
    assert "invalid_utf8" in codes(doc)
    assert doc.to_bytes() == data


def test_ta1_inside_interchange():
    text = ("ISA*00*          *00*          *ZZ*SENDER         *ZZ*RECEIVER       "
            "*261003*1200*^*00501*000000002*0*T*:~TA1*000000001*261003*1200*A*000~IEA*0*000000002~")
    doc = parse_text(text)
    assert doc.errors == [] and doc.issues == []
    assert [s.tag for s in doc.interchanges[0].loose] == ["TA1"]


def test_control_count_mismatches(make_x12):
    text = make_x12("ST*850*0001~BEG*00*SA*PO1**20261001~SE*9*0002").replace("GE*1*1", "GE*5*7")
    doc = parse_text(text)
    msgs = sorted(i.message for i in doc.errors)
    assert len(msgs) == 4, msgs
    assert any("SE01" in m for m in msgs) and any("SE02" in m for m in msgs)
    assert any("GE01" in m for m in msgs) and any("GE02" in m for m in msgs)


def test_non_ascii_digits_in_counts(make_x12):
    """Regression (found by fuzzing): '18\u00b2' passed str.isdigit() and then crashed int()."""
    doc = parse_text(make_x12("ST*850*0001~SE*2\u00b2*0001"))
    assert [i.code for i in doc.errors] == ["control_mismatch"]


def test_missing_trailers(make_x12):
    text = make_x12("ST*850*0001~BEG*00*SA*PO1**20261001~ST*850*0002~SE*2*0002")
    doc = parse_text(text.replace("GE*2*1", "GE*2*1"))
    assert [m.control for m in doc.messages] == ["0001", "0002"]
    assert any(i.code == "missing_trailer" and "0001" in i.message for i in doc.errors)


def test_segment_outside_envelope(make_x12):
    text = make_x12("ST*850*0001~SE*2*0001") + "N1*ST*STRAY~"
    doc = parse_text(text)
    assert [s.tag for s in doc.stray] == ["N1"]
    assert "unmatched_segment" in codes(doc)


def test_edifact_escaped_terminator_and_release():
    text = "UNB+UNOA:3+S+R+261003:1200+1'UNH+1+ORDERS:D:96A:UN'FTX+AAI+++IT?'S 50?? OFF?+MORE'UNT+3+1'UNZ+1+1'"
    doc = parse_text(text)
    assert doc.errors == []
    assert doc.messages[0].segments[0].value(4) == "IT'S 50? OFF+MORE"


def test_empty_segments_are_tolerated(make_x12):
    text = make_x12("ST*850*0001~BEG*00*SA*PO1**20261001~SE*3*0001", nl="").replace("~BEG", "~~BEG")
    doc = parse_text(text)
    assert doc.errors == [] and "empty_segment" in codes(doc)
    assert doc.to_text() == text


# -- regressions found by fuzzing (tests/fuzzing.py, scripts/fuzz.py, scripts/fuzz_service.py) ---------

def test_damaged_header_mid_file_skips_to_next_interchange(make_x12):
    good1 = make_x12("ST*850*0001~SE*2*0001", ctrl=1)
    damaged = make_x12("ST*850*0002~SE*2*0002", ctrl=2)
    damaged = damaged[:105] + "Z" + damaged[106:]  # ISA16+1 = segment terminator becomes a letter
    good3 = make_x12("ST*850*0003~SE*2*0003", ctrl=3)
    data = (good1 + damaged + good3).encode()
    doc = parse_bytes(data)
    assert [m.control for m in doc.messages] == ["0001", "0003"]
    bad = [i for i in doc.issues if i.code == "bad_header"]
    assert len(bad) == 1 and "header at offset" in bad[0].message
    assert doc.to_bytes() == data


def test_unreadable_only_header_is_rejected(make_x12):
    text = make_x12("ST*850*0001~SE*2*0001")
    import pytest

    from ediparse import EDIDetectionError
    with pytest.raises(EDIDetectionError, match="segment terminator"):
        parse_text(text[:105] + "Z" + text[106:])


def test_header_lookalike_inside_text_is_not_a_header(make_x12):
    text = "Pay by VISA*MC please\n" + make_x12("ST*850*0001~SE*2*0001")
    doc = parse_text(text)
    assert doc.errors == [] and len(doc.messages) == 1


def test_lookalike_split_across_chunks():
    """A cut between 'V' and 'ISA*' must not make 'ISA*' look like a header."""
    from ediparse import stream
    from conftest import x12
    text = "junk " * 3 + "VISA*" + "x" * 3 + "\n" + x12("ST*850*0001~SE*2*0001")
    for size in range(1, 30):
        events = list(stream([text[i:i + size] for i in range(0, len(text), size)]))
        assert [e.kind for e in events] == ["issue", "message", "interchange"], size  # issue = leading junk


def test_invalid_byte_delimiter_produces_utf8_json(make_x12):
    """Byte 0x85 (not valid UTF-8 on its own) as segment terminator must still give encodable JSON."""
    import json

    from ediparse import event_to_dict, stream
    data = make_x12("ST*850*0001~N1*ST*X~SE*3*0001", seg="\x85", nl="").encode("latin-1")
    events = list(stream(data))
    for e in events:
        json.dumps(event_to_dict(e), ensure_ascii=False).encode("utf-8")
    assert events[-1].interchange.dialect.describe()["segment"] == "\x85"
    assert parse_bytes(data).to_bytes() == data
