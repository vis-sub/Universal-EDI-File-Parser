"""Send many EDI files to the service in parallel and collect per-file results.

Each worker thread streams one file at a time; the load balancer in front of the
service instances spreads the requests. Failed requests are retried with backoff.
Standard library only.

Usage:
    python examples/parallel_client.py inbound/*.edi --url http://localhost:8080 --workers 8 --out results/
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from stream_client import parse_documents  # same directory


def process_file(path: str, url: str, out_dir: Path | None, seq: int = 0, retries: int = 3) -> dict:
    """Parse one file; write its records to <out_dir>/<seq>_<name>.ndjson. Returns the summary record."""
    for attempt in range(1, retries + 1):
        try:
            records = []
            summary = None
            for rec in parse_documents(path, url):
                if rec["type"] == "summary":
                    summary = rec
                elif rec["type"] == "error":
                    raise RuntimeError(rec["message"])
                records.append(rec)  # or hand each document to your pipeline here
            if summary is None:
                raise RuntimeError("stream ended without a summary record (incomplete response)")
            if out_dir:
                # seq prefix keeps same-named files from different folders apart
                with open(out_dir / f"{seq:05d}_{Path(path).name}.ndjson", "w", encoding="utf-8") as f:
                    f.writelines(json.dumps(r) + "\n" for r in records)
            return {"file": path, **summary}
        except (OSError, RuntimeError) as e:
            # 4xx means the file itself is bad (not EDI, too large): retrying won't help.
            if attempt == retries or str(e).startswith("HTTP 4"):
                return {"file": path, "type": "failed", "error": str(e)}
            time.sleep(2 ** attempt)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--url", default="http://localhost:8080")
    ap.add_argument("--workers", type=int, default=8, help="parallel uploads; ~total parser processes")
    ap.add_argument("--out", help="directory for per-file NDJSON results")
    args = ap.parse_args()
    out_dir = Path(args.out) if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    start, failed = time.time(), 0
    with ThreadPoolExecutor(args.workers) as pool:
        futures = [pool.submit(process_file, f, args.url, out_dir, i) for i, f in enumerate(args.files, 1)]
        for fut in as_completed(futures):
            res = fut.result()
            failed += res["type"] == "failed" or not res.get("valid", False)
            print(json.dumps(res))
    print(f"{len(args.files)} files in {time.time() - start:.1f}s, {failed} invalid or failed", file=sys.stderr)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
