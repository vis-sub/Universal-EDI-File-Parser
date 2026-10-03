import json
from pathlib import Path

from ediparse.cli import main

from conftest import SAMPLES

PO = str(SAMPLES / "x12/5010/850_purchase_order.edi")


def test_ndjson_default_streams_one_object_per_document(capsys):
    assert main([PO]) == 0
    records = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["type"] for r in records] == ["message", "interchange", "summary"]
    assert records[0]["message"]["type"] == "850" and records[0]["group"]["type"] == "PO"
    assert records[-1]["valid"] is True


def test_ndjson_no_segments(capsys):
    assert main(["parse", "--no-segments", PO]) == 0
    first = json.loads(capsys.readouterr().out.splitlines()[0])
    assert "segments" not in first["message"] and first["message"]["segment_count"] == 21


def test_json(capsys):
    assert main(["-f", "json", PO]) == 0
    out = json.loads(capsys.readouterr().out)
    msg = out["interchanges"][0]["groups"][0]["messages"][0]
    assert msg["type"] == "850"
    po1 = next(s for s in msg["segments"] if s["tag"] == "PO1")
    assert po1["elements"]["PO107"] == "012345678905"


def test_csv(capsys):
    assert main(["parse", "-f", "csv", PO]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("file,interchange,group,message_type")
    assert any(",BEG,BEG03,1,,PO-10045" in line for line in lines)


def test_tree_and_detect(capsys):
    assert main(["-f", "tree", PO]) == 0
    assert "Message 850 control=0001" in capsys.readouterr().out
    assert main(["detect", PO]) == 0
    assert json.loads(capsys.readouterr().out)["repetition"] == "^"


def test_validate_exit_codes(capsys, tmp_path):
    assert main(["validate", PO]) == 0
    bad = tmp_path / "bad.edi"
    bad.write_text(Path(PO).read_text().replace("SE*21*0001", "SE*99*0001"))
    assert main(["validate", str(bad)]) == 1
    junk = tmp_path / "junk.txt"
    junk.write_text("not edi")
    assert main(["validate", str(junk)]) == 2


def test_serve_rejects_bad_port_cleanly(monkeypatch, capsys):
    """Regression (found on a real cluster): Kubernetes injects EDIPARSE_PORT=tcp://... for a Service
    named "ediparse"; the service now reads EDIPARSE_HTTP_PORT and reports bad values without a traceback."""
    import pytest
    monkeypatch.setenv("EDIPARSE_PORT", "tcp://10.43.0.1:80")      # ignored now
    monkeypatch.setenv("EDIPARSE_HTTP_PORT", "tcp://10.43.0.1:80")
    with pytest.raises(SystemExit) as exc:
        main(["serve"])
    assert exc.value.code == 2 and "invalid port" in capsys.readouterr().err


def test_shorthand_only_checks_first_argument(tmp_path):
    """Was: `ediparse a.edi -o validate` failed because 'validate' appeared somewhere in argv."""
    out = tmp_path / "validate"
    assert main([PO, "-o", str(out)]) == 0
    assert out.read_text().count('"type": "message"') == 1


def test_broken_pipe_exits_quietly():
    import shlex
    import subprocess
    import sys
    files = [PO] * 200
    cmd = f"{shlex.quote(sys.executable)} -m ediparse {' '.join(map(shlex.quote, files))} | head -1"
    p = subprocess.run(cmd, shell=True, capture_output=True, text=True, check=False)
    assert p.stdout.count("\n") == 1 and "Broken pipe" not in p.stderr and "Exception" not in p.stderr
