"""Mutation fuzzer shared by tests/test_fuzz.py (quick, fixed seed) and scripts/fuzz.py (long runs)."""
from __future__ import annotations

import json
import random

from ediparse import EDIDetectionError, StreamParser, event_to_dict, parse_bytes, stream

from conftest import SAMPLE_FILES

SEEDS = [p.read_bytes() for p in SAMPLE_FILES]
NASTY = [b"~", b"*", b":", b"^", b"'", b"+", b"?", b"|", b"\r", b"\n", b"\r\n", b"\x00", b"\xff", b"\xc3",
         b"ISA", b"UNA:+.? '", b"UNB+", b"STX=", b"MSH|^~\\&|", b"IEA*1*000000001~", b"SE*", b"GS*", b"ST*850*1~"]


def mutate(rng: random.Random, data: bytes) -> bytes:
    data = bytearray(data)
    for _ in range(rng.randint(1, 6)):
        op = rng.randrange(8)
        if not data:
            data += rng.choice(NASTY)
            continue
        i = rng.randrange(len(data))
        if op == 0:                                    # flip a byte
            data[i] = rng.randrange(256)
        elif op == 1:                                  # insert a delimiter / header fragment
            data[i:i] = rng.choice(NASTY)
        elif op == 2:                                  # delete a span
            del data[i:i + rng.randint(1, 40)]
        elif op == 3:                                  # truncate
            del data[i:]
        elif op == 4:                                  # duplicate a span
            data[i:i] = data[i:i + rng.randint(1, 200)]
        elif op == 5:                                  # splice in part of another sample
            other = rng.choice(SEEDS)
            j = rng.randrange(len(other))
            data[i:i] = other[j:j + rng.randint(1, 300)]
        elif op == 6:                                  # shuffle segments (assumes '~' or "'" terminators)
            term = b"~" if b"~" in data else b"'"
            segs = bytes(data).split(term)
            a, b = rng.randrange(len(segs)), rng.randrange(len(segs))
            segs[a], segs[b] = segs[b], segs[a]
            data = bytearray(term.join(segs))
        else:                                          # random junk
            data[i:i] = rng.randbytes(rng.randint(1, 20))
    return bytes(data)


def check_case(rng: random.Random, data: bytes) -> str:
    """Assert the parser's invariants on one input. Returns 'parsed' or 'rejected'."""
    try:
        doc = parse_bytes(data)
    except EDIDetectionError:
        # Rejection must be consistent between whole-file and streaming parsing.
        try:
            list(stream(data, chunk_size=rng.randint(1, 64)))
        except EDIDetectionError:
            return "rejected"
        raise AssertionError("streaming accepted input that parse_bytes rejected") from None

    assert doc.to_bytes() == data, "round trip changed the bytes"

    placed = list(doc.stray)
    for ic in doc.interchanges:
        placed += [s for s in (ic.service_string, ic.header, ic.trailer) if s] + ic.loose
        for g in ic.groups:
            placed += [s for s in (g.header, g.trailer) if s]
            for m in g.messages:
                placed += m.all_segments()
    assert sorted(s.index for s in placed) == list(range(len(doc.segments))), "segment lost or duplicated"

    # Streaming with random chunking must equal streaming the whole input at once. (Encoding can differ
    # from parse_bytes when invalid UTF-8 appears mid-stream, so compare stream against stream.)
    whole = [event_to_dict(e) for e in stream(data, chunk_size=max(1, len(data)))]
    chunked = [event_to_dict(e) for e in stream(data, chunk_size=rng.randint(1, 97))]
    assert chunked == whole, "chunking changed the events"
    for record in whole:  # the service writes records with ensure_ascii=False as UTF-8
        json.dumps(record, ensure_ascii=False).encode("utf-8")

    p = StreamParser(retain=True)          # byte-at-a-time retained parse must still round-trip
    for k in range(0, len(data), 3):
        p.feed(data[k:k + 3])
    p.close()
    assert p.document().to_bytes() == data, "chunked retained parse changed the bytes"
    return "parsed"


def run(seed: int, cases: int) -> dict:
    rng = random.Random(seed)
    stats = {"parsed": 0, "rejected": 0}
    for n in range(cases):
        data = mutate(rng, rng.choice(SEEDS))
        try:
            stats[check_case(rng, data)] += 1
        except Exception as e:
            raise AssertionError(f"case {n} (seed {seed}) failed: {e!r}\ninput: {data[:400]!r}") from e
    return stats
