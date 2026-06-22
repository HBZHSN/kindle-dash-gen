from __future__ import annotations

import re
import unicodedata
from pathlib import Path


_SPACE_RE = re.compile(r"\s+")
_SYMBOL_SPEC_RE = re.compile(r"^(?P<primary>[^()]+)\((?P<fallback>[^()]+)\)$")


def parse_symbol_spec(spec: object) -> tuple[str, str | None]:
    """Split a market symbol spec into (primary, fallback).

    ``"^NDX(NQ=F)"`` means: show ``^NDX`` while its market is open, otherwise
    show ``NQ=F``. A plain symbol like ``"AAPL"`` has no fallback.
    """
    text = str(spec).strip() if spec is not None else ""
    match = _SYMBOL_SPEC_RE.match(text)
    if not match:
        return text, None
    primary = match.group("primary").strip()
    fallback = match.group("fallback").strip()
    if not primary or not fallback:
        return text, None
    return primary, fallback


def ascii_text(value: object, fallback: str = "N/A") -> str:
    text = str(value) if value is not None else ""
    normalized = unicodedata.normalize("NFKD", text)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    ascii_only = _SPACE_RE.sub(" ", ascii_only).strip()
    return ascii_only or fallback


def display_name(path: Path, fallback: str) -> str:
    name = path.stem if path.is_file() else path.name
    name = re.sub(r"^[0-9]{6,8}[-_ ]*", "", name)
    name = re.sub(r"^[0-9]+[-_ ]*", "", name)
    return ascii_text(name, fallback)
