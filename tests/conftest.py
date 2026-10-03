from pathlib import Path

import pytest

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
SAMPLE_FILES = sorted(p for p in SAMPLES.rglob("*") if p.is_file())


def x12(body: str, version: str = "00501", elem: str = "*", rep: str = "^", comp: str = ":",
        seg: str = "~", ctrl: int = 1, nl: str = "\n") -> str:
    """Wrap ``body`` segments (written with * : ~) in a valid ISA/GS/GE/IEA envelope."""
    isa11 = rep if version >= "00402" else "U"
    isa = elem.join(["ISA", "00", " " * 10, "00", " " * 10, "ZZ", "SENDER".ljust(15), "ZZ",
                     "RECEIVER".ljust(15), "261003", "1200", isa11, version, f"{ctrl:09d}", "0", "T", comp])
    lines = [s for s in body.strip().split("~") if s.strip()]
    n_sets = sum(1 for s in lines if s.startswith("ST*"))
    lines = [f"GS*PO*SENDER*RECEIVER*20261003*1200*1*X*{version}0", *lines,
             f"GE*{n_sets}*1", f"IEA*1*{ctrl:09d}"]
    rest = "".join(s.strip().replace("*", elem).replace(":", comp) + seg + nl for s in lines)
    return isa + seg + nl + rest


@pytest.fixture
def make_x12():
    return x12
