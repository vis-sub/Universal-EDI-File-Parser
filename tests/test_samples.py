"""Every sample file must round-trip byte for byte and parse without errors."""
import pytest

from ediparse import parse_bytes, parse_file

from conftest import SAMPLE_FILES, SAMPLES

ids = [str(p.relative_to(SAMPLES)) for p in SAMPLE_FILES]


@pytest.mark.parametrize("path", SAMPLE_FILES, ids=ids)
def test_round_trip(path):
    data = path.read_bytes()
    assert parse_bytes(data).to_bytes() == data


@pytest.mark.parametrize("path", SAMPLE_FILES, ids=ids)
def test_no_errors(path):
    doc = parse_file(path)
    assert doc.errors == [], [i.message for i in doc.errors]
    assert doc.messages, "expected at least one message"


@pytest.mark.parametrize("path", SAMPLE_FILES, ids=ids)
def test_every_segment_placed_once(path):
    doc = parse_file(path)
    placed = list(doc.stray)
    for ic in doc.interchanges:
        placed += [s for s in (ic.service_string, ic.header, ic.trailer) if s] + ic.loose
        for g in ic.groups:
            placed += [s for s in (g.header, g.trailer) if s]
            for m in g.messages:
                placed += m.all_segments()
    assert sorted(s.index for s in placed) == [s.index for s in doc.segments]


@pytest.mark.parametrize("path", [p for p in SAMPLE_FILES if p.parts[-3] == "x12"],
                         ids=[i for i, p in zip(ids, SAMPLE_FILES, strict=True) if p.parts[-3] == "x12"])
def test_x12_message_type_matches_filename(path):
    doc = parse_file(path)
    expected = path.name.split("_")[0].rstrip("PID")  # 837P_... -> 837
    assert {m.type for m in doc.messages} == {expected}
    version = "00501" if path.parent.name == "5010" else "00401"
    assert doc.interchanges[0].dialect.version == version


def test_non_x12_samples():
    by_name = {p.name: parse_file(p) for p in SAMPLE_FILES if p.parts[-3] != "x12"}
    orders = by_name["ORDERS_D96A_una.edi"]
    assert [(m.type, m.version) for m in orders.messages] == [("ORDERS", "D.96A")]
    assert orders.interchanges[0].service_string is not None

    invoic = by_name["INVOIC_D01B_no_una_two_messages.edi"]
    assert [m.control for m in invoic.messages] == ["M0001", "M0002"]

    trad = by_name["ORDERS_order_file.edi"]
    assert [m.type for m in trad.messages] == ["ORDHDR", "ORDERS", "ORDTLR"]

    hl7 = by_name["ADT_A01_A08.hl7"]
    assert [m.type for m in hl7.messages] == ["ADT^A01^ADT_A01", "ADT^A08^ADT_A01"]
    assert hl7.messages[0].version == "2.5.1"
