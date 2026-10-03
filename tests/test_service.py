import gzip
import json

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from ediparse.service import Settings, create_app  # noqa: E402

from conftest import SAMPLES, x12  # noqa: E402

PO = (SAMPLES / "x12/4010/850_purchase_order_pipe_delims.edi").read_bytes()
CLAIM = (SAMPLES / "x12/5010/837P_professional_claim.edi").read_bytes()


@pytest.fixture
def client():
    return TestClient(create_app(Settings(max_upload_bytes=200_000, spool_memory_bytes=1024, chunk_bytes=512)))


def ndjson(resp):
    return [json.loads(line) for line in resp.text.splitlines()]


def test_health_and_info(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    info = client.get("/v1/info").json()
    assert "X12" in info["standards"] and info["limits"]["max_upload_bytes"] == 200_000


def test_parse_streams_one_record_per_document(client):
    r = client.post("/v1/parse", content=PO)
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/x-ndjson")
    recs = ndjson(r)
    assert [x["type"] for x in recs] == ["message", "message", "interchange", "summary"]
    assert [x["message"]["control"] for x in recs[:2]] == ["0001", "0002"]
    beg = recs[0]["message"]["segments"][0]
    assert beg["tag"] == "BEG" and beg["elements"]["BEG03"] == "PO-10045"
    assert recs[-1] == {"type": "summary", "interchanges": 1, "messages": 2, "errors": 0, "warnings": 0,
                        "valid": True}


def test_parse_options(client):
    r = client.post("/v1/parse?format=json&include=message&segments=false", content=CLAIM)
    body = r.json()
    assert r.headers["content-type"].startswith("application/json")
    assert len(body) == 1 and "segments" not in body[0]["message"]
    assert body[0]["message"]["version"] == "005010X222A1"


def test_parse_gzip_upload(client):
    r = client.post("/v1/parse?include=summary", content=gzip.compress(CLAIM),
                    headers={"Content-Encoding": "gzip"})
    assert ndjson(r) == [{"type": "summary", "interchanges": 1, "messages": 1, "errors": 0, "warnings": 0,
                          "valid": True}]


def test_parse_reports_document_level_errors(client):
    bad = x12("ST*850*0001~BEG*00*SA*PO1**20261001~SE*9*0001~ST*850*0002~SE*2*0002")
    recs = ndjson(client.post("/v1/parse", content=bad))
    assert [m["valid"] for m in recs[:2]] == [False, True]
    assert recs[0]["issues"][0]["code"] == "control_mismatch"
    assert recs[-1]["errors"] == 1


def test_validate_and_detect(client):
    bad = x12("ST*850*0001~SE*9*0001")
    v = client.post("/v1/validate", content=bad).json()
    assert v["valid"] is False and v["issues"][0]["message_control"] == "0001"
    assert client.post("/v1/validate", content=PO).json()["valid"] is True
    d = client.post("/v1/detect", content=PO).json()
    assert d["interchanges"][0]["element"] == "|" and d["interchanges"][0]["messages"] == 2


@pytest.mark.parametrize("kwargs,status", [
    ({"content": b"hello, not edi"}, 422),
    ({"content": b""}, 400),
    ({"content": b"ISA" + b"x" * 300_000}, 413),
    ({"files": {"file": ("a.edi", PO)}}, 415),
    ({"content": b"not gzip", "headers": {"Content-Encoding": "gzip"}}, 400),
    ({"content": PO, "headers": {"Content-Encoding": "br"}}, 415),
])
def test_rejections(client, kwargs, status):
    assert client.post("/v1/parse", **kwargs).status_code == status


def test_bad_include(client):
    assert client.post("/v1/parse?include=message,bogus", content=PO).status_code == 422


def test_spool_spills_to_disk_for_large_uploads(client):
    # spool_memory_bytes=1024 forces a temp file; results must be unaffected.
    body = x12("~".join(f"ST*850*{i:04d}~BEG*00*SA*PO{i}**20261001~SE*3*{i:04d}" for i in range(1, 400)))
    recs = ndjson(client.post("/v1/parse?include=summary", content=body.encode()))
    assert recs == [{"type": "summary", "interchanges": 1, "messages": 399, "errors": 0, "warnings": 0,
                     "valid": True}]
