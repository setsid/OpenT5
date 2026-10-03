"""Where the game data lives, read from .env or the environment.

Only paths: nothing here reads a zone. Every location is optional, so a
checkout without the game still runs its unit tests.
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KEYS = ("OPENT5_ZONES", "OPENT5_PATCH_ZONES", "OPENT5_DLC_ZONES", "OPENT5_ELF", "OPENT5_WADS")


def load(path: Path | None = None) -> dict[str, str]:
    values: dict[str, str] = {}
    source = path or ROOT / ".env"
    if source.is_file():
        for line in source.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    for key in KEYS:
        if os.environ.get(key):
            values[key] = os.environ[key]
    return values


def path_of(key: str) -> Path | None:
    value = load().get(key)
    return Path(value) if value else None


def zone_dirs() -> list[Path]:
    """Every configured zone folder that exists, base first."""
    out = []
    for key in ("OPENT5_ZONES", "OPENT5_PATCH_ZONES", "OPENT5_DLC_ZONES"):
        found = path_of(key)
        if found and found.is_dir():
            out.append(found)
    return out


#: Folders searched recursively rather than at the top level only: the DLC
#: arrives as dlc1/english, dlc1/french, dlc2/... under one root.
RECURSIVE = ("OPENT5_DLC_ZONES",)


def all_zones() -> list[Path]:
    """Every zone in every configured folder, base first, sorted within each."""
    out: list[Path] = []
    for key in ("OPENT5_ZONES", "OPENT5_PATCH_ZONES", "OPENT5_DLC_ZONES"):
        found = path_of(key)
        if not found or not found.is_dir():
            continue
        pattern = "**/*.ff" if key in RECURSIVE else "*.ff"
        out.extend(sorted(p for p in found.glob(pattern) if p.is_file()))
    return out
