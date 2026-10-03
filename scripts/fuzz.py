"""Long-running mutation fuzz: python scripts/fuzz.py [cases-per-seed] [seeds]"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))
from fuzzing import run  # noqa: E402

cases = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
seeds = int(sys.argv[2]) if len(sys.argv) > 2 else 4
t = time.time()
total = {"parsed": 0, "rejected": 0}
for seed in range(100, 100 + seeds):
    for k, v in run(seed, cases).items():
        total[k] += v
    print(f"seed {seed}: ok  {total}", flush=True)
print(f"{sum(total.values())} cases, {time.time() - t:.0f}s, no invariant violations")
