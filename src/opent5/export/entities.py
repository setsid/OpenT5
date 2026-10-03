"""The map entity string (MapEnts): ``{ "key" "value" ... }`` blocks, one per entity."""

from __future__ import annotations

import re

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')


class EntityError(ValueError):
    pass


def entity_text(raw: bytes | None) -> str:
    if raw is None:
        return ""
    return raw.split(b"\0", 1)[0].decode("latin-1")


def parse_entities(text: str) -> list[dict[str, str]]:
    """Every entity as an ordered dict of key -> value (a repeated key keeps the last)."""
    out: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    key: str | None = None
    for m in _TOKEN.finditer(text):
        string, brace = m.group(1), m.group(2)
        if brace == "{":
            if current is not None:
                raise EntityError(f"entity string at {m.start()}: expected '}}', found '{{'")
            current, key = {}, None
        elif brace == "}":
            if current is None or key is not None:
                raise EntityError(f"entity string at {m.start()}: unexpected '}}'")
            out.append(current)
            current = None
        else:
            if current is None:
                raise EntityError(f"entity string at {m.start()}: string outside an entity")
            if key is None:
                key = string
            else:
                current[key] = string
                key = None
    if current is not None:
        raise EntityError("entity string: last entity is not closed")
    return out


def vector(value: str | None) -> list[float] | None:
    if not value:
        return None
    try:
        return [float(v) for v in value.split()]
    except ValueError:
        return None
