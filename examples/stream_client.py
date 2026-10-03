"""Stream an EDI file to the ediparse service and handle each parsed document as it arrives.

Standard library only. Usage:
    python examples/stream_client.py samples/x12/5010/837P_professional_claim.edi [http://localhost:8080]
"""
from __future__ import annotations

import http.client
import json
import os
import sys
from collections.abc import Iterator
from urllib.parse import urlencode, urlsplit


def parse_documents(path: str, base_url: str = "http://localhost:8080", **params) -> Iterator[dict]:
    """Upload ``path`` and yield each NDJSON record (message, interchange, issue, summary)."""
    u = urlsplit(base_url)
    conn_cls = http.client.HTTPSConnection if u.scheme == "https" else http.client.HTTPConnection
    conn = conn_cls(u.hostname, u.port, timeout=600)
    query = f"?{urlencode(params)}" if params else ""
    try:
        with open(path, "rb") as f:  # the file is streamed from disk, not loaded into memory
            conn.request("POST", f"{u.path.rstrip('/')}/v1/parse{query}", body=f, headers={
                "Content-Type": "application/octet-stream",
                "Content-Length": str(os.path.getsize(path)),
            })
        resp = conn.getresponse()
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}: {resp.read().decode(errors='replace')}")
        for line in resp:  # one JSON object per line, delivered incrementally
            yield json.loads(line)
    finally:
        conn.close()


def main() -> int:
    path, base = sys.argv[1], (sys.argv[2] if len(sys.argv) > 2 else "http://localhost:8080")
    for rec in parse_documents(path, base):
        if rec["type"] == "message":
            m = rec["message"]
            print(f"{rec['sequence']:>6}  {m['type']:<8} control={m['control']:<10} valid={rec['valid']}")
            # Hand the document to your own processing here, e.g.:
            #   route(m["type"], rec)  /  queue.publish(rec)  /  db.insert(rec)
        elif rec["type"] in ("summary", "error"):
            print(json.dumps(rec))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
