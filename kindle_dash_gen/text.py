from __future__ import annotations

import re
import unicodedata
from pathlib import Path


_SPACE_RE = re.compile(r"\s+")


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
