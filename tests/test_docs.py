"""Docs must not rot: links and anchors resolve, referenced make targets/CLI flags exist, snippets run."""
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
MARKDOWN = sorted(p for p in ROOT.rglob("*.md") if not {".venv", ".git", ".pytest_cache"} & set(p.parts))
LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)\)")


def slug(heading: str) -> str:
    """GitHub-style heading anchor."""
    s = re.sub(r"[`*_]", "", heading.strip().lower())
    s = re.sub(r"[^\w\- ]", "", s)
    return s.replace(" ", "-")


def anchors(path: Path) -> set[str]:
    text = re.sub(r"```.*?```", "", path.read_text(), flags=re.S)
    return {slug(m.group(1)) for m in re.finditer(r"^#+\s+(.+)$", text, re.M)}


def code_free(text: str) -> str:
    return re.sub(r"```.*?```", "", text, flags=re.S)


@pytest.mark.parametrize("doc", MARKDOWN, ids=[str(p.relative_to(ROOT)) for p in MARKDOWN])
def test_links_resolve(doc):
    broken = []
    for target in LINK.findall(code_free(doc.read_text())):
        if re.match(r"[a-z]+:", target):          # http:, https:, mailto:
            continue
        path_part, _, anchor = target.partition("#")
        dest = (doc.parent / path_part).resolve() if path_part else doc
        if not dest.exists():
            broken.append(f"{target} (missing file)")
        elif anchor and dest.suffix == ".md" and anchor not in anchors(dest):
            broken.append(f"{target} (missing anchor)")
    assert not broken, broken


def code_lines(text: str) -> list[str]:
    """Lines of fenced code blocks plus inline code spans: where commands appear."""
    lines = [ln.strip() for block in re.findall(r"```[a-z]*\n(.*?)```", text, re.S) for ln in block.splitlines()]
    return lines + [span.strip() for span in re.findall(r"`([^`\n]+)`", code_free(text))]


ALL_CODE = [ln for doc in MARKDOWN for ln in code_lines(doc.read_text())]


def test_make_targets_mentioned_in_docs_exist():
    targets = set(re.findall(r"^([a-z0-9-]+):", (ROOT / "Makefile").read_text(), re.M))
    mentioned = set()
    for line in ALL_CODE:
        for m in re.finditer(r"(?:^|&& |; )make ((?:[a-z0-9-]+ ?)+)", line):
            mentioned |= set(m.group(1).split())
    missing = mentioned - targets
    assert not missing, missing


def test_cli_flags_mentioned_in_docs_exist():
    help_text = ""
    for cmd in ("parse", "serve"):
        help_text += subprocess.run([sys.executable, "-m", "ediparse", cmd, "--help"],
                                    capture_output=True, text=True, check=True).stdout
    flags = {f for line in ALL_CODE if line.startswith("ediparse") for f in re.findall(r"\s(--[a-z-]+)", line)}
    assert flags, "expected some documented CLI flags"
    for flag in flags:
        assert flag in help_text, flag


def test_getting_started_python_snippet_runs():
    text = (ROOT / "docs/getting-started.md").read_text()
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)
    assert blocks
    for code in blocks:
        out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=False)
        assert out.returncode == 0, out.stderr
        assert "850" in out.stdout


def test_library_reference_examples_match_behaviour():
    from ediparse import parse_file
    msg = parse_file(ROOT / "samples/x12/5010/837P_professional_claim.edi").messages[0]
    clm = next(s for s in msg.segments if s.tag == "CLM")
    assert (clm[1], clm.components(5), clm.value(5, component=1)) == ("CLM-0001", ["11", "B", "1"], "11")
