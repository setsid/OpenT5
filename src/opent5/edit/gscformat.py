"""Indentation for GSC / CSC scripts, for display and editing (pure functions, no Qt).

The scripts in console zones are stored without indentation: every line starts at column 0
(``maps/mp/animscripts/dog_combat.gsc`` in patch_mp begins ``main()\\n{\\ndebug_anim_print(``).
``format_script`` adds leading tabs from the brace structure so the code reads normally;
it changes nothing else (no spacing inside a line, no line breaks), so its output differs
from the input only in leading whitespace.

The save policy (docs/edit-api.md, "GSC formatting"): ``unformat_script`` removes the
leading spaces and tabs of every line that the formatter would indent, which gives back the
stored form exactly for an unedited script and stores an edited line the same way the game's
own files are stored. Lines whose original leading whitespace is significant (inside a
``/* */`` comment or a string that spans lines) are kept as they are, both ways.

The proof over every GSC / CSC rawfile of every zone is ``tools/gsc_format_all.py``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

INDENT = "\t"
#: Script extensions the formatter applies to.
SCRIPT_SUFFIXES = (".gsc", ".csc")

_CONTROL = re.compile(r"(?:if|else|for|foreach|while)\b")
_CASE = re.compile(r"(?:case\b[^:]*|default\s*):")


def is_script(name: str | None) -> bool:
    return bool(name) and name.lower().endswith(SCRIPT_SUFFIXES)


@dataclass
class _Line:
    """What the scanner found on one line."""

    #: True when the line starts inside a block comment or a multi-line string: its leading
    #: whitespace is content and is never changed.
    verbatim: bool
    #: The code of the line with strings and comments blanked (for the structure rules).
    code: str
    opens: int = 0
    closes: int = 0
    #: Closing braces before any other code on the line (dedent the line itself).
    leading_closes: int = 0


@dataclass
class Structure:
    """The brace structure of a script, as ``analyse`` finds it."""

    lines: list[_Line] = field(default_factory=list)
    #: Problems: unbalanced braces, with 1-based line numbers.
    problems: list[str] = field(default_factory=list)


def _split(text: str) -> list[str]:
    """Lines with their endings ("\\n", "\\r\\n" or none for the last)."""
    return text.splitlines(keepends=True) if text else []


def _body(line: str) -> tuple[str, str]:
    """(content, line ending)."""
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith(("\n", "\r")):
        return line[:-1], line[-1]
    return line, ""


def analyse(text: str) -> Structure:
    """Scan strings, comments and braces line by line."""
    out = Structure()
    in_block = False  # inside /* */
    in_string = False
    depth = 0
    for n, raw in enumerate(_split(text), 1):
        body, _ = _body(raw)
        verbatim = in_block
        code: list[str] = []
        i = 0
        while i < len(body):
            c = body[i]
            if in_block:
                if body.startswith("*/", i):
                    in_block = False
                    code.append("  ")
                    i += 2
                else:
                    code.append(" ")
                    i += 1
                continue
            if in_string:
                if c == "\\":
                    code.append("  ")
                    i += 2
                    continue
                if c == '"':
                    in_string = False
                code.append(" ")
                i += 1
                continue
            if body.startswith("//", i):
                break
            if body.startswith("/*", i):
                in_block = True
                code.append("  ")
                i += 2
                continue
            if c == '"':
                in_string = True
            code.append(c)
            i += 1
        text_code = "".join(code).strip()
        opens, closes = text_code.count("{"), text_code.count("}")
        prefix = text_code[: len(text_code) - len(text_code.lstrip("} \t"))]
        leading = prefix.count("}")
        in_string = False  # GSC strings do not span lines
        depth += opens - closes
        if depth < 0:
            out.problems.append(f"line {n}: a closing brace with no opening brace before it")
            depth = 0
        out.lines.append(_Line(verbatim, text_code, opens, closes, leading))
    if depth > 0:
        out.problems.append(f"end of file: {depth} opening brace(s) never closed")
    if in_block:
        out.problems.append("end of file: a /* comment is never closed")
    return out


def _is_control(code: str) -> bool:
    """A control header whose body is the next statement (no brace, no semicolon)."""
    if not _CONTROL.match(code):
        return False
    return not code.endswith((";", "{", "}"))


def indents(text: str, structure: Structure | None = None) -> list[int | None]:
    """The indentation level of every line (None for verbatim lines, kept as they are).

    Rules: one level per open brace; a brace on its own line sits at the level of the line
    before it (Allman style, as the game's scripts are written); the statement after a
    control header with no brace (``if (x)`` / ``else`` / ``for`` / ``while`` /
    ``foreach``) is one level deeper; statements under a ``case`` / ``default`` label are one
    level deeper than the label."""
    s = structure or analyse(text)
    out: list[int | None] = []
    #: one frame per open brace: [level of its contents, inside a case label]
    frames: list[list] = [[0, False]]
    pending = 0
    for line in s.lines:
        code = line.code
        lead = min(line.leading_closes, len(frames) - 1)
        frame = frames[len(frames) - 1 - lead]
        is_case = bool(_CASE.match(code))
        if lead:
            # a closing brace sits at the level of the line that opened its block
            level = frames[len(frames) - lead][0] - 1
        else:
            level = frame[0]
            if frame[1] and not is_case:
                level += 1
            if code and not code.startswith("{"):
                level += pending
        out.append(None if line.verbatim else level)
        if not code:
            continue
        for _ in range(min(line.closes, len(frames) - 1)):
            frames.pop()
        if line.opens:
            for k in range(line.opens):
                frames.append([level + 1 + k, False])
            pending = 0
        elif is_case:
            frames[-1][1] = True
            pending = 0
        elif _is_control(code):
            pending += 1
        else:
            pending = 0
    return out


def format_script(text: str) -> str:
    """The script with leading tabs from its structure. Only leading whitespace changes."""
    s = analyse(text)
    levels = indents(text, s)
    parts = []
    for raw, level in zip(_split(text), levels, strict=True):
        if level is None:
            parts.append(raw)
            continue
        body, end = _body(raw)
        stripped = body.lstrip(" \t")
        # a line holding only whitespace is kept as it is (some retail scripts have them)
        parts.append(INDENT * level + stripped + end if stripped else raw)
    return "".join(parts)


def unformat_script(text: str) -> str:
    """The stored form: leading spaces and tabs removed from every line except verbatim
    ones (inside a block comment or a string spanning lines)."""
    s = analyse(text)
    parts = []
    for raw, line in zip(_split(text), s.lines, strict=True):
        if line.verbatim:
            parts.append(raw)
            continue
        body, end = _body(raw)
        stripped = body.lstrip(" \t")
        parts.append(stripped + end if stripped else raw)
    return "".join(parts)


def round_trips(text: str) -> bool:
    """True when formatting then unformatting gives ``text`` back exactly, which is the
    condition for showing a script formatted (the editor falls back to the stored text)."""
    return unformat_script(format_script(text)) == text


def check_consistent(text: str) -> list[str]:
    """Problems in a formatted script's indentation: each closing brace at the start of a
    line must sit at the level of the line that opened it."""
    s = analyse(text)
    levels = indents(text, s)
    problems = list(s.problems)
    stack: list[int] = []
    for n, (line, level) in enumerate(zip(s.lines, levels, strict=True), 1):
        if level is None:
            continue
        for _ in range(line.leading_closes):
            if stack:
                want = stack.pop()
                if want != level and len(problems) < 20:
                    problems.append(f"line {n}: closing brace at level {level}, opened at {want}")
        for _ in range(line.opens):
            stack.append(level)
        for _ in range(line.closes - line.leading_closes):
            if stack:
                stack.pop()
    return problems
