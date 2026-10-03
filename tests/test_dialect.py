"""Delimiter detection for each standard."""
import pytest

from ediparse import EDIFACT, HL7, TRADACOMS, X12, EDIDetectionError, parse_text


def dialect_of(text):
    return parse_text(text).interchanges[0].dialect


def test_x12_4010_has_no_repetition_separator(make_x12):
    d = dialect_of(make_x12("ST*850*0001~BEG*00*SA*PO1**20261001~SE*3*0001", version="00401"))
    assert (d.standard, d.version, d.element, d.component, d.segment, d.repetition) == \
        (X12, "00401", "*", ":", "~", None)


def test_x12_4010_does_not_split_on_U(make_x12):
    doc = parse_text(make_x12("ST*850*0001~N1*ST*ACME UNIVERSAL U~SE*3*0001", version="00401"))
    assert doc.messages[0].segments[0].repeats(2) == ["ACME UNIVERSAL U"]


def test_x12_5010_repetition_separator(make_x12):
    doc = parse_text(make_x12("ST*271*0001~EB*1*IND*30^1^98~SE*3*0001"))
    eb = doc.messages[0].segments[0]
    assert doc.interchanges[0].dialect.repetition == "^"
    assert eb.repeats(3) == ["30", "1", "98"]


def test_x12_5010_invalid_isa11_is_ignored(make_x12):
    doc = parse_text(make_x12("ST*850*0001~N1*ST*ACME UNIVERSAL~SE*3*0001", rep="U"))
    assert doc.interchanges[0].dialect.repetition is None
    assert any(i.code == "isa_format" for i in doc.issues)


@pytest.mark.parametrize("elem,comp,seg", [("|", ">", "~"), ("*", "\\", "\n"), ("^", ":", "'"), ("\x1d", "\x1f", "\x1c")])
def test_x12_any_delimiters(make_x12, elem, comp, seg):
    text = make_x12("ST*837*0001~SV1*HC:99213*100~SE*3*0001", elem=elem, comp=comp, seg=seg,
                    rep="!", nl="" if seg == "\n" else "\n")
    doc = parse_text(text)
    assert doc.errors == []
    assert doc.messages[0].segments[0].components(1) == ["HC", "99213"]
    assert doc.to_text() == text


def test_x12_isa_not_fixed_width():
    text = ("ISA*00**00**ZZ*SENDER*ZZ*RECEIVER*261003*1200*^*00501*000000001*0*T*:~"
            "GS*PO*S*R*20261003*1200*1*X*005010~ST*850*0001~SE*2*0001~GE*1*1~IEA*1*000000001~")
    doc = parse_text(text)
    assert doc.interchanges[0].dialect.segment == "~"
    assert doc.errors == []
    assert any(i.code == "isa_format" for i in doc.issues)


def test_edifact_una_custom_delimiters():
    text = "UNA|*.# !UNB*UNOC|3*S*R*261003|1200*1!UNH*1*ORDERS|D|96A|UN!FTX*AAI***A#*B!UNT*3*1!UNZ*1*1!"
    doc = parse_text(text)
    d = doc.interchanges[0].dialect
    assert (d.standard, d.element, d.component, d.release, d.segment) == (EDIFACT, "*", "|", "#", "!")
    assert doc.messages[0].segments[0].value(4) == "A*B"
    assert doc.errors == []


def test_edifact_syntax4_default_repetition():
    text = "UNB+UNOC:4+S+R+261003:1200+1'UNH+1+ORDERS:D:96A:UN'UNT+2+1'UNZ+1+1'"
    assert dialect_of(text).repetition == "*"


def test_tradacoms():
    d = dialect_of("STX=ANA:1+S+R+261003+REF'MHD=1+ORDERS:9'MTR=2'END=1'")
    assert (d.standard, d.tag_separator) == (TRADACOMS, "=")


def test_hl7_newline_terminated():
    doc = parse_text("MSH|^~\\&|A|B|C|D|20261003||ADT^A01|1|P|2.5\nPID|1||MRN\n")
    assert doc.interchanges[0].dialect.standard == HL7
    assert [s.tag for s in doc.messages[0].all_segments()] == ["MSH", "PID"]


def test_unknown_input_raises():
    with pytest.raises(EDIDetectionError):
        parse_text("hello, this is not EDI")
