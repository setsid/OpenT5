"""Release version numbers: MAJOR.MINOR.PATCH (semver core), optionally tagged 'v'."""

from __future__ import annotations

import re

_SEMVER = re.compile(
    r"^v?(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})\.(0|[1-9]\d{0,8})"
    r"(?:-([0-9A-Za-z.-]+))?(?:\+([0-9A-Za-z.-]+))?$"
)


def parse(text: str) -> tuple[int, int, int] | None:
    """(major, minor, patch) for a final release version; None for anything else.

    Pre-releases ('1.2.0-rc.1') return None: they are never offered. Build metadata
    ('+...') is ignored, as semver says it carries no precedence.
    """
    m = _SEMVER.match(text.strip())
    if not m or m.group(4):
        return None
    return int(m.group(1)), int(m.group(2)), int(m.group(3))


def is_prerelease(text: str) -> bool:
    m = _SEMVER.match(text.strip())
    return bool(m and m.group(4))


def text(v: tuple[int, int, int]) -> str:
    return ".".join(str(x) for x in v)
