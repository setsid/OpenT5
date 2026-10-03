"""Syntax highlighting for GSC/CSC scripts, cfg files, menu text and entity strings.

Plain regular-expression rules plus a block-comment state; colours are theme
tokens (``syn_*``), re-read when the theme changes.
"""

from __future__ import annotations

import re

from PySide6.QtGui import QColor, QFont, QSyntaxHighlighter, QTextCharFormat

from opent5.gui import theme

GSC_KEYWORDS = (
    "if else for foreach in while do switch case default break continue return wait "
    "waittill waittillmatch waittillframeend notify endon thread childthread self level "
    "game anim undefined true false isdefined isDefined"
).split()
GSC_BUILTINS = (
    "getent getentarray spawn spawnstruct getdvar getdvarint getdvarfloat setdvar "
    "setdvar makedvarserverinfo precachemodel precacheshader precachestring precacheitem "
    "precacherumble loadfx playfx playfxontag playsound playloopsound iprintln iprintlnbold "
    "array_thread array_randomize randomint randomfloat randomintrange randomfloatrange "
    "distance distancesquared vectornormalize vectortoangles anglestoforward anglestoright "
    "anglestoup bullettrace physicstrace gettime int float abs min max cos sin tan sqrt "
    "assert assertmsg assertex println print setmodel attach detach delete hide show "
    "moveto rotateto linkto unlink giveweapon takeweapon switchtoweapon setclientdvar "
    "getplayers isplayer isalive isai gettagorigin gettagangles strtok getsubstr tolower "
    "issubstr spawnfx triggerfx setcontents solid notsolid freezecontrols setorigin "
    "setplayerangles"
).split()
CFG_COMMANDS = (
    "set seta sets setu bind bind2 unbind unbindall exec vstr toggle reset echo wait "
    "say map map_restart devmap fast_restart kick clientkick seta_mp writeconfig"
).split()
PREPROC = ("#include", "#using_animtree", "#define", "#ifdef", "#ifndef", "#endif", "#else")


def mode_for(name: str) -> str:
    low = name.lower()
    if low.endswith((".gsc", ".csc", ".gsh", ".script", ".atr")):
        return "gsc"
    if low.endswith((".cfg", ".txt")) or "config" in low:
        return "cfg"
    if low.endswith((".menu", ".inc")):
        return "gsc"
    return "gsc"


def _fmt(colour: str, bold: bool = False, italic: bool = False) -> QTextCharFormat:
    f = QTextCharFormat()
    f.setForeground(QColor(colour))
    if bold:
        f.setFontWeight(QFont.Weight.DemiBold)
    if italic:
        f.setFontItalic(True)
    return f


def _words(words) -> str:
    return (
        r"\b(?:"
        + "|".join(re.escape(w) for w in sorted(set(words), key=len, reverse=True))
        + r")\b"
    )


class Highlighter(QSyntaxHighlighter):
    def __init__(self, document, mode: str = "gsc"):
        super().__init__(document)
        self.mode = mode
        self.rules: list[tuple[re.Pattern, QTextCharFormat]] = []
        self.formats: dict[str, QTextCharFormat] = {}
        self.rebuild(theme.current())
        theme.on_change(self._theme_changed, self)

    def _theme_changed(self, t) -> None:
        self.rebuild(t)
        self.rehighlight()

    def set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self.rebuild(theme.current())
            self.rehighlight()

    def rebuild(self, t) -> None:
        f = self.formats = {
            "keyword": _fmt(t.syn_keyword),
            "builtin": _fmt(t.syn_builtin),
            "string": _fmt(t.syn_string),
            "comment": _fmt(t.syn_comment, italic=True),
            "number": _fmt(t.syn_number),
            "preproc": _fmt(t.syn_preproc),
            "ref": _fmt(t.syn_builtin),
        }
        number = re.compile(r"\b(?:0x[0-9a-fA-F]+|\d+\.?\d*(?:[eE][-+]?\d+)?)\b")
        if self.mode == "cfg":
            self.rules = [
                (re.compile(r"^\s*" + _words(CFG_COMMANDS), re.IGNORECASE), f["keyword"]),
                (number, f["number"]),
                (re.compile(r'"[^"\n]*"?'), f["string"]),
                (re.compile(r"(?://|#).*$"), f["comment"]),
            ]
        elif self.mode == "ents":
            self.rules = [
                (re.compile(r'^\s*"[^"]*"'), f["keyword"]),
                (re.compile(r'(?<=" )"[^"]*"'), f["string"]),
                (re.compile(r"[{}]"), f["preproc"]),
                (re.compile(r"//.*$"), f["comment"]),
            ]
        else:
            self.rules = [
                (re.compile(_words(GSC_KEYWORDS)), f["keyword"]),
                (re.compile(_words(GSC_BUILTINS), re.IGNORECASE), f["builtin"]),
                (
                    re.compile(r"^\s*(?:" + "|".join(re.escape(p) for p in PREPROC) + r")\b"),
                    f["preproc"],
                ),
                (re.compile(r"(?:\b[\w\\/]+)?::\w+"), f["ref"]),
                (number, f["number"]),
                (re.compile(r'[&%]?"(?:[^"\\\n]|\\.)*"?'), f["string"]),
                (re.compile(r"//.*$"), f["comment"]),
            ]

    def highlightBlock(self, text: str) -> None:  # noqa: N802 (Qt override)
        string, comment = self.formats["string"], self.formats["comment"]
        taken = bytearray(len(text))
        # strings and line comments first, left to right: whichever starts first wins
        lexical = [(p, f) for p, f in self.rules if f is string or f is comment]
        at = 0
        while at < len(text):
            best = None
            for pattern, fmt in lexical:
                m = pattern.search(text, at)
                if m and m.end() > m.start() and (best is None or m.start() < best[0].start()):
                    best = (m, fmt)
            if best is None:
                break
            m, fmt = best
            self.setFormat(m.start(), m.end() - m.start(), fmt)
            taken[m.start() : m.end()] = b"\x01" * (m.end() - m.start())
            at = m.end()
        for pattern, fmt in self.rules:
            if fmt is string or fmt is comment:
                continue
            for m in pattern.finditer(text):
                a, b = m.span()
                if a < b and not any(taken[a:b]):
                    self.setFormat(a, b - a, fmt)
        self._block_comments(text)

    def _block_comments(self, text: str) -> None:
        if self.mode == "cfg":
            return
        fmt = self.formats["comment"]
        self.setCurrentBlockState(0)
        start = 0
        if self.previousBlockState() != 1:
            start = _find_open(text, 0)
        while start >= 0:
            end = text.find(
                "*/", start + (0 if self.previousBlockState() == 1 and start == 0 else 2)
            )
            if end < 0:
                self.setCurrentBlockState(1)
                self.setFormat(start, len(text) - start, fmt)
                break
            self.setFormat(start, end + 2 - start, fmt)
            start = _find_open(text, end + 2)


def _find_open(text: str, at: int) -> int:
    """The next /* that is not inside a string or a // comment."""
    in_str = False
    i = at
    while i < len(text) - 1:
        c = text[i]
        if c == '"' and (i == 0 or text[i - 1] != "\\"):
            in_str = not in_str
        elif not in_str and text.startswith("//", i):
            return -1
        elif not in_str and text.startswith("/*", i):
            return i
        i += 1
    return -1
