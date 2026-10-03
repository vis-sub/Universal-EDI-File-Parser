"""Every record the parser emits must match the published JSON Schema (docs/schemas/records.schema.json)."""
import json
import random
from pathlib import Path

import pytest

from ediparse import EDIDetectionError, event_to_dict, stream
from ediparse.output import Summary

from conftest import SAMPLE_FILES, SAMPLES

jsonschema = pytest.importorskip("jsonschema")
SCHEMA = json.loads((Path(__file__).resolve().parent.parent / "docs/schemas/records.schema.json").read_text())
VALIDATOR = jsonschema.Draft202012Validator(SCHEMA)


def records(data: bytes, segments: bool = True) -> list[dict]:
    summary, out = Summary(), []
    for ev in stream(data):
        summary.add(ev)
        out.append(event_to_dict(ev, segments))
    return [*out, summary.to_dict()]


def test_schema_is_valid():
    jsonschema.Draft202012Validator.check_schema(SCHEMA)


@pytest.mark.parametrize("path", SAMPLE_FILES, ids=[str(p.relative_to(SAMPLES)) for p in SAMPLE_FILES])
@pytest.mark.parametrize("segments", [True, False])
def test_sample_records_match_schema(path, segments):
    for rec in records(path.read_bytes(), segments):
        VALIDATOR.validate(rec)


def test_fuzzed_records_match_schema():
    from fuzzing import SEEDS, mutate
    rng = random.Random(7)
    checked = 0
    for _ in range(300):
        try:
            recs = records(mutate(rng, rng.choice(SEEDS)))
        except EDIDetectionError:
            continue
        for rec in recs:
            VALIDATOR.validate(rec)
        checked += len(recs)
    assert checked > 300


def test_error_record_matches_schema():
    VALIDATOR.validate({"type": "error", "message": "boom", "error": "RuntimeError"})


def test_cli_ndjson_matches_schema(capsys):
    from ediparse.cli import main
    files = [str(p) for p in SAMPLE_FILES]
    assert main(["parse", *files]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) > len(files)
    for line in lines:
        VALIDATOR.validate(json.loads(line))
