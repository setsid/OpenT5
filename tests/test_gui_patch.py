"""The mod-patch dialogs and the worker wrappers, offscreen, without pytest-qt.

The dialogs are built from fake result objects (attribute access only), and the
``patchops`` wrappers are checked to call ``opent5.patch`` with the right arguments
without touching real zones.
"""

from __future__ import annotations

import os
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QLabel, QPlainTextEdit  # noqa: E402

APP = QApplication.instance() or QApplication([])

from opent5.gui import dialogs, patchops  # noqa: E402


def _change(index, type_name, name, op, size):
    return SimpleNamespace(index=index, type_name=type_name, name=name, op=op, size=size)


def _create_result():
    return SimpleNamespace(
        source_zone="patch_mp",
        source_ff_sha1="a" * 40,
        source_content_sha1="b" * 40,
        result_content_sha1="c" * 40,
        signed=True,
        changes=[_change(95, "localize", "CGAME_SB_ACCURACY", "localize", 15)],
        source_bytes=3715811,
        edited_bytes=3715827,
        patch_bytes=325,
        patch=b"O5PATCH\x00rest",
        signature_note="The console signature no longer matches; loads only on a patched client.",
    )


def _apply_result(*, matched=True, reproduces=True, verified=True, problems=()):
    return SimpleNamespace(
        source_zone="patch_mp",
        expected_ff_sha1="a" * 40,
        expected_content_sha1="b" * 40,
        found_ff_sha1="a" * 40,
        found_content_sha1=("b" if matched else "9") * 40,
        verified=verified,
        changes=[_change(95, "localize", "CGAME_SB_ACCURACY", "localize", 15)],
        output="/tmp/out/patch_mp.ff",
        output_bytes=1196064,
        output_sha1="d" * 40,
        reproduces_target=reproduces,
        signature_note="loads only on a client with the check patched out.",
        notes=["the .ff was repacked but its content matches, so the result is reproduced."],
        problems=list(problems),
    )


def _all_text(widget) -> str:
    parts = []
    for w in [*widget.findChildren(QLabel), *widget.findChildren(QPlainTextEdit)]:
        parts.append(w.text() if isinstance(w, QLabel) else w.toPlainText())
    return "\n".join(parts)


def test_create_dialog_shows_summary_and_changes():
    d = dialogs.CreatePatchResultDialog(_create_result())
    text = _all_text(d)
    assert "patch_mp" in text
    assert "325 bytes" in text
    assert "CGAME_SB_ACCURACY" in text  # the changed-asset list
    assert "localize" in text
    assert "patched client" in text  # the signature note


def test_create_dialog_save_button_calls_back():
    saved = []
    d = dialogs.CreatePatchResultDialog(_create_result(), on_save=lambda dlg: saved.append(dlg))
    from PySide6.QtWidgets import QPushButton

    save = next(b for b in d.findChildren(QPushButton) if "Save" in b.text())
    save.click()
    assert saved == [d]


def test_apply_dialog_verified_state():
    d = dialogs.ApplyPatchResultDialog(_apply_result())
    text = _all_text(d)
    assert "applied and verified" in text.lower()
    assert "your stock content sha1 matches the patch" in text
    assert "/tmp/out/patch_mp.ff" in text
    assert "repacked" in text  # the note


def test_apply_dialog_problem_state():
    d = dialogs.ApplyPatchResultDialog(
        _apply_result(reproduces=False, verified=False, problems=["rebuilt content does not match"])
    )
    text = _all_text(d)
    assert "with problems" in text.lower()
    assert "rebuilt content does not match" in text


def test_create_patch_wrapper_calls_api(monkeypatch, tmp_path):
    import opent5.patch as patch

    seen = {}

    def fake_create(stock, edited):
        seen["args"] = (stock, edited)
        return _create_result()

    monkeypatch.setattr(patch, "create", fake_create)
    stock, edited = tmp_path / "stock.ff", tmp_path / "edited.ff"
    result = patchops.create_patch(stock, edited)
    assert seen["args"] == (stock, edited)
    assert result.source_zone == "patch_mp"


def test_apply_patch_wrapper_reads_and_calls(monkeypatch, tmp_path):
    import opent5.patch as patch

    pf = tmp_path / "p.o5patch"
    pf.write_bytes(b"PATCHBYTES")
    seen = {}

    def fake_apply(data, stock, out):
        seen["args"] = (data, stock, out)
        return _apply_result()

    monkeypatch.setattr(patch, "apply", fake_apply)
    stock, out = tmp_path / "s.ff", tmp_path / "o.ff"
    result = patchops.apply_patch(pf, stock, out)
    assert seen["args"] == (b"PATCHBYTES", stock, out)
    assert result.reproduces_target


def test_save_patch_writes_bytes(tmp_path):
    out = tmp_path / "x.o5patch"
    n = patchops.save_patch(_create_result(), out)
    assert out.read_bytes() == b"O5PATCH\x00rest"
    assert n == len(b"O5PATCH\x00rest")
