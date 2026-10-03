"""Streaming must give identical results for any chunking, and memory must not grow with file size."""
import asyncio
import tracemalloc

import pytest

from ediparse import StreamParser, astream, event_to_dict, stream

from conftest import SAMPLE_FILES, SAMPLES, x12

ids = [str(p.relative_to(SAMPLES)) for p in SAMPLE_FILES]


def dicts(events):
    return [event_to_dict(e) for e in events]


@pytest.mark.parametrize("path", SAMPLE_FILES, ids=ids)
def test_chunk_size_does_not_change_results(path):
    data = path.read_bytes()
    expected = dicts(stream(data, chunk_size=len(data)))
    for size in (1, 2, 3, 7, 64, 1000):
        assert dicts(stream(data, chunk_size=size)) == expected, f"chunk_size={size}"


@pytest.mark.parametrize("path", SAMPLE_FILES, ids=ids)
def test_byte_at_a_time_round_trip(path):
    data = path.read_bytes()
    p = StreamParser(retain=True)
    for i in range(len(data)):
        p.feed(data[i:i + 1])
    p.close()
    assert p.document().to_bytes() == data


def test_event_order_and_context():
    events = list(stream(SAMPLES / "x12/4010/850_purchase_order_pipe_delims.edi"))
    assert [e.kind for e in events] == ["message", "message", "interchange"]
    assert [e.sequence for e in events[:2]] == [1, 2]
    m = events[0].message
    assert (m.type, m.group.type, m.interchange.control) == ("850", "PO", "000000001")
    assert events[2].interchange.message_count == 2 and events[2].interchange.issues == []


def test_messages_are_emitted_before_input_ends():
    """A consumer gets each document as soon as it completes, not at end of file."""
    text = x12("ST*850*0001~SE*2*0001~ST*850*0002~SE*2*0002")
    cut = text.index("ST*850*0002")
    p = StreamParser()
    early = p.feed(text[:cut + 3])
    assert [e.message.control for e in early] == ["0001"]
    rest = p.feed(text[cut + 3:]) + p.close()
    assert [e.kind for e in rest] == ["message", "interchange"]


def test_utf8_multibyte_split_across_chunks():
    data = x12("ST*850*0001~N1*ST*CAFÉ 東京~SE*3*0001").encode("utf-8")
    events = list(stream(data, chunk_size=1))
    assert events[0].message.segments[0].value(2) == "CAFÉ 東京"


def test_latin1_fallback_mid_stream():
    data = x12("ST*850*0001~N1*ST*CAF\xc9~SE*3*0001").encode("latin-1")
    events = list(stream(data, chunk_size=16))
    msg = events[0].message
    assert msg.segments[0].value(2) == "CAFÉ"
    assert any(i.code == "encoding_fallback" for i in msg.issues + events[-1].interchange.issues)


def test_file_object_and_iterable_sources():
    path = SAMPLES / "x12/5010/837P_professional_claim.edi"
    with open(path, "rb") as f:
        a = dicts(stream(f, chunk_size=10))
    b = dicts(stream(iter([path.read_bytes()])))
    c = dicts(stream([path.read_text()]))
    assert a == b == c


def test_astream():
    data = (SAMPLES / "edifact/INVOIC_D01B_no_una_two_messages.edi").read_bytes()

    async def chunks():
        for i in range(0, len(data), 5):
            yield data[i:i + 5]

    async def collect():
        return [e async for e in astream(chunks())]

    events = asyncio.run(collect())
    assert [e.kind for e in events] == ["message", "message", "interchange"]


def test_memory_is_flat_for_large_input():
    """~5 MB of 837 claims streamed from a generator: peak memory must stay small."""
    one = ("ST*837*{c}*005010X222A1~BHT*0019*00*B1*20261003*1200*CH~NM1*41*2*SUBMITTER*****46*S1~"
           "HL*1**20*1~NM1*85*2*CLINIC*****XX*1234567893~HL*2*1*22*0~SBR*P*18*G1******CI~"
           "NM1*IL*1*DOE*JOHN****MI*M1~CLM*C{c}*150***11:B:1*Y*A*Y*Y~HI*ABK:J069~LX*1~"
           "SV1*HC:99213*100*UN*1***1~DTP*472*D8*20260928~SE*14*{c}~")
    n = 12_000
    head = x12("ST*837*0000~SE*2*0000").split("ST*")[0]  # ISA + GS

    def source():
        yield head.encode()
        for i in range(1, n + 1):
            yield one.format(c=f"{i:05d}").encode()
        yield f"GE*{n}*1~IEA*1*000000001~".encode()

    tracemalloc.start()
    count = errors = 0
    for ev in stream(source()):
        if ev.kind == "message":
            count += 1
            errors += len(ev.message.issues)
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    assert count == n and errors == 0
    assert peak < 1_500_000, f"peak {peak / 1e6:.1f} MB"
