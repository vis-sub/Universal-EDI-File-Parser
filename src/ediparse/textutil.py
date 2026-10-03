"""Helpers for text decoded with ``surrogateescape`` (bytes that were not valid UTF-8)."""
from __future__ import annotations

import re

_ESCAPED_BYTE = re.compile("[\udc80-\udcff]")
_ESCAPED_TO_LATIN1 = {0xDC00 + b: b for b in range(0x80, 0x100)}


def has_escaped_bytes(s: str) -> bool:
    """True if ``s`` holds bytes that were not valid UTF-8 (kept as surrogates by the decoder)."""
    return _ESCAPED_BYTE.search(s) is not None


def display(s: str) -> str:
    """Show bytes that were not valid UTF-8 as their Latin-1 characters (e.g. 0xC9 -> 'É')."""
    return s.translate(_ESCAPED_TO_LATIN1) if has_escaped_bytes(s) else s
