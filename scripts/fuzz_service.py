"""Fuzz a running service over HTTP: python scripts/fuzz_service.py [base-url] [cases]

Every response must be a 200 that ends with a summary record (and contains no error record),
or a clean 4xx. Any 5xx, dropped connection or incomplete stream is a failure.
"""
import collections
import gzip
import http.client
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from fuzzing import SEEDS, mutate  # noqa: E402

base = urlsplit(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8080")
cases = int(sys.argv[2]) if len(sys.argv) > 2 else 600


def one(n: int) -> str:
    rng = random.Random(n)
    body = mutate(rng, rng.choice(SEEDS))
    headers = {"Content-Type": "application/octet-stream"}
    if n % 7 == 0:
        body, headers["Content-Encoding"] = gzip.compress(body), "gzip"
    query = rng.choice(["", "?format=json", "?segments=false", "?include=message,summary"])
    conn = http.client.HTTPConnection(base.hostname, base.port, timeout=60)
    try:
        conn.request("POST", f"/v1/parse{query}", body=body, headers=headers)
        r = conn.getresponse()
        data = r.read()
    except Exception as e:
        return f"FAIL connection: {e!r}"
    finally:
        conn.close()
    if 400 <= r.status < 500:
        return f"{r.status}"
    if r.status != 200:
        return f"FAIL status {r.status}: {data[:200]!r}"
    recs = json.loads(data) if "format=json" in query else [json.loads(x) for x in data.splitlines()]
    if any(x.get("type") == "error" for x in recs):
        return f"FAIL error record: {recs[-1]}"
    if not recs or recs[-1].get("type") != "summary":
        return "FAIL stream ended without summary"
    return "200 valid" if recs[-1]["valid"] else "200 with issues"


with ThreadPoolExecutor(16) as pool:
    results = list(pool.map(one, range(cases)))
counts = collections.Counter(r if not r.startswith("FAIL") else "FAIL" for r in results)
print(dict(counts))
for r in results:
    if r.startswith("FAIL"):
        print(r)
sys.exit(1 if counts["FAIL"] else 0)
