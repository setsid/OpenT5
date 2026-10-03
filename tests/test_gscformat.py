"""opent5.edit.gscformat: indentation for display, and the exact way back to the stored
form. The whole-corpus proof is tools/gsc_format_all.py."""

from __future__ import annotations

from opent5.edit import gscformat as g

STORED = (
    "#include common_scripts\\utility;\n"
    "main()\n"
    "{\n"
    "if ( isDefined( level.x ) )\n"
    "{\n"
    "foo();\n"
    "return;\n"
    "}\n"
    "else\n"
    "bar();\n"
    "for ( ;; )\n"
    "{\n"
    "switch ( response )\n"
    "{\n"
    'case "A":\n'
    "a();\n"
    "break;\n"
    "default:\n"
    "b();\n"
    "}\n"
    "}\n"
    "/*\n"
    "   kept as it is\n"
    "*/\n"
    'x = "{ not a brace }"; // { nor this\n'
    "if ( a )\n"
    "if ( b )\n"
    "c();\n"
    "d = [];\n"
    "}\n"
)

FORMATTED = (
    "#include common_scripts\\utility;\n"
    "main()\n"
    "{\n"
    "\tif ( isDefined( level.x ) )\n"
    "\t{\n"
    "\t\tfoo();\n"
    "\t\treturn;\n"
    "\t}\n"
    "\telse\n"
    "\t\tbar();\n"
    "\tfor ( ;; )\n"
    "\t{\n"
    "\t\tswitch ( response )\n"
    "\t\t{\n"
    '\t\t\tcase "A":\n'
    "\t\t\t\ta();\n"
    "\t\t\t\tbreak;\n"
    "\t\t\tdefault:\n"
    "\t\t\t\tb();\n"
    "\t\t}\n"
    "\t}\n"
    "\t/*\n"
    "   kept as it is\n"
    "*/\n"
    '\tx = "{ not a brace }"; // { nor this\n'
    "\tif ( a )\n"
    "\t\tif ( b )\n"
    "\t\t\tc();\n"
    "\td = [];\n"
    "}\n"
)


def test_formats_the_structure():
    assert g.format_script(STORED) == FORMATTED


def test_unformat_gives_the_stored_form_back():
    assert g.unformat_script(FORMATTED) == STORED
    assert g.round_trips(STORED)
    assert g.check_consistent(STORED) == []


def test_only_leading_whitespace_changes():
    out = g.format_script(STORED)
    for a, b in zip(STORED.splitlines(), out.splitlines(), strict=True):
        assert b.lstrip(" \t") == a.lstrip(" \t")


def test_crlf_and_whitespace_only_lines_round_trip():
    text = "main()\r\n{\r\n\t\r\nfoo();\r\n}"
    out = g.format_script(text)
    assert out == "main()\r\n{\r\n\t\r\n\tfoo();\r\n}"
    assert g.unformat_script(out) == text


def test_an_edited_line_is_stored_without_its_indentation():
    edited = FORMATTED.replace("\t\tfoo();\n", "\t\tfoo();\n\t\t  extra();\n")
    stored = g.unformat_script(edited)
    assert "\nextra();\n" in stored
    assert stored.replace("extra();\n", "", 1) == STORED


def test_unbalanced_braces_are_reported():
    assert g.check_consistent("main()\n{\nfoo();\n")[0].startswith("end of file")
    assert "closing brace" in g.check_consistent("}\n")[0]
    assert g.round_trips("main()\n{\nfoo();\n")  # still shown and saved exactly


def test_script_names():
    assert g.is_script("maps/mp/_utility.gsc") and g.is_script("clientscripts/x.CSC")
    assert not g.is_script("default.cfg") and not g.is_script(None)
