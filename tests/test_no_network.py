"""OpenT5 makes exactly one kind of network access: the update check in opent5.update.net.

(a) Static: no module under src/opent5 except the allowed list imports or uses a
    network-capable API (sockets, TLS, HTTP clients, Qt networking, opening URLs in a
    browser, or shelling out to a downloader).
(b) Runtime: with every socket connect and name lookup made to fail loudly, opening,
    parsing, editing, saving, exporting and the GUI's open and save paths all run.
(c) The update module's hosts are exactly GitHub's API and release-asset hosts, plus
    127.0.0.1 only when the test override is set; a real check connects nowhere else.
"""

from __future__ import annotations

import ast
import os
import socket
import sys
from pathlib import Path

import pytest

import opent5
from opent5 import update
from opent5.update import check, net

SRC = Path(opent5.__file__).resolve().parent

#: Modules (or module prefixes) whose import means network access.
NETWORK_MODULES = (
    "socket", "ssl", "http.client", "http.server", "http.cookiejar", "urllib.request",
    "urllib3", "requests", "httpx", "aiohttp", "ftplib", "smtplib", "poplib", "imaplib",
    "telnetlib", "nntplib", "xmlrpc", "socketserver", "asyncio.streams", "webbrowser",
    "PySide6.QtNetwork", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets", "PySide6.QtHttpServer", "PySide6.QtRemoteObjects",
)  # fmt: skip
#: Names whose use means network access or opening a URL.
NETWORK_NAMES = ("QDesktopServices", "open_connection", "create_connection", "urlopen")
#: Strings that mean shelling out to a downloader.
DOWNLOADERS = ("curl", "wget", "invoke-webrequest", "bitsadmin", "certutil", "start-bitstransfer")


def _hits(module_name: str, tree: ast.AST) -> list[str]:
    out = []

    def banned(name: str) -> bool:
        return any(name == m or name.startswith(m + ".") for m in NETWORK_MODULES)

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [f"import {a.name}" for a in node.names if banned(a.name)]
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if banned(node.module):
                out.append(f"from {node.module} import ...")
            out += [
                f"from {node.module} import {a.name}"
                for a in node.names
                if banned(f"{node.module}.{a.name}") or a.name in NETWORK_NAMES
            ]
        elif isinstance(node, ast.Attribute) and node.attr in NETWORK_NAMES:
            out.append(f".{node.attr}")
        elif isinstance(node, ast.Name) and node.id in NETWORK_NAMES:
            out.append(node.id)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            if banned(node.value) and node.value not in ("socket", "ssl"):
                out.append(f"string {node.value!r}")  # importlib / __import__ by name
            out += [f"string mentions {d!r}" for d in DOWNLOADERS if d in low.split()]
    return out


def _modules():
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC.parent).with_suffix("")
        name = ".".join(rel.parts).removesuffix(".__init__")
        yield name, ast.parse(path.read_text(encoding="utf-8"), str(path))


def test_scanner_detects_network_use():
    sample = (
        "import socket\nfrom urllib.request import urlopen\nfrom PySide6 import QtNetwork\n"
        "from PySide6.QtGui import QDesktopServices\nimport webbrowser\n"
        "import subprocess\nsubprocess.run(['curl', 'x'])\nimport http.client\n"
    )
    hits = _hits("sample", ast.parse(sample))
    for needle in ("import socket", "urllib.request", "QtNetwork", "QDesktopServices",
                   "webbrowser", "'curl'", "http.client"):  # fmt: skip
        assert any(needle in h for h in hits), (needle, hits)


def test_only_the_allowed_modules_use_the_network():
    assert update.NETWORK_MODULES == ("opent5.update.net",)
    offenders, allowed_seen = {}, set()
    for name, tree in _modules():
        hits = _hits(name, tree)
        if name in update.NETWORK_MODULES:
            allowed_seen.add(name)
            assert hits, f"{name} is allowed but the scanner found nothing: scanner broken?"
        elif hits:
            offenders[name] = hits
    assert offenders == {}, f"network-capable code outside opent5.update.net: {offenders}"
    assert allowed_seen == set(update.NETWORK_MODULES)


def test_the_exe_build_excludes_qt_networking():
    spec = (SRC.parents[1] / "packaging" / "opent5.spec").read_text()
    assert '"PySide6.QtNetwork"' in spec


# -- (b) runtime ---------------------------------------------------------------------------


@pytest.fixture
def no_sockets(monkeypatch):
    attempts: list[tuple] = []

    def refuse(name):
        def fn(*args, **kwargs):
            attempts.append((name, args[1:] if name.startswith("socket.") else args))
            raise AssertionError(f"network access attempted: {name}{args!r}")

        return fn

    monkeypatch.setattr(socket.socket, "connect", refuse("socket.connect"))
    monkeypatch.setattr(socket.socket, "connect_ex", refuse("socket.connect_ex"))
    monkeypatch.setattr(socket, "getaddrinfo", refuse("getaddrinfo"))
    monkeypatch.setattr(socket, "gethostbyname", refuse("gethostbyname"))
    monkeypatch.setattr(socket, "create_connection", refuse("create_connection"))
    monkeypatch.delenv(update.TEST_URL_ENV, raising=False)
    return attempts


@pytest.fixture
def zone(tmp_path):
    from test_edit_document import synthetic_ff

    path = tmp_path / "zones" / "mp_testedit.ff"
    path.parent.mkdir()
    path.write_bytes(synthetic_ff())
    return path


def test_open_parse_edit_save_export_make_no_connections(no_sockets, zone, tmp_path, capsys):
    from opent5 import cli
    from opent5.edit import Document

    doc = Document.open(zone)
    doc.text(2)
    doc.set_text(2, "set a 2\n")
    doc.save(tmp_path / "edited.ff")
    for argv in (
        ["info", zone],
        ["list", zone, "--inline"],
        ["extract", zone, tmp_path / "x", "--name", "*"],
        ["rebuild", zone, "-o", tmp_path / "r.ff"],
        ["verify", tmp_path / "edited.ff", "--against", zone],
        ["unpack", zone, tmp_path / "u"],
        ["pack", tmp_path / "u", "-o", tmp_path / "p.ff"],
    ):
        assert cli.main([str(a) for a in argv]) in (0, 1)
    capsys.readouterr()
    assert no_sockets == []


def test_gui_open_and_save_make_no_connections(no_sockets, zone, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication

    QApplication.instance() or QApplication([])
    from opent5.gui.mainwindow import MainWindow

    win = MainWindow(QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat))
    win.ask_before_discard = False
    win.modal_reports = False  # the save report must not block the test
    (page,) = win.open_paths([zone], sync=True)
    for ref in list(page.doc.all_refs)[:6]:
        for kind in page.available(ref):
            page.open_ref(ref, kind)
    win._save_to(page, tmp_path / "saved.ff", sync=True)
    win.close()
    assert no_sockets == []


# -- (c) the update module's hosts ---------------------------------------------------------


def test_allowed_hosts_are_exactly_github():
    assert update.ALLOWED_HOSTS == (
        "api.github.com",
        "release-assets.githubusercontent.com",
        "objects.githubusercontent.com",
    )
    assert net.API_BASE == "https://api.github.com"


def test_production_check_contacts_only_the_api_host(monkeypatch, tmp_path):
    monkeypatch.delenv(update.TEST_URL_ENV, raising=False)
    monkeypatch.setattr(update, "REPO", "example/opent5")
    from opent5.update.minisign import SecretKey

    monkeypatch.setattr(update, "PUBLIC_KEY", SecretKey.generate().public.line())
    looked_up = []

    def lookup(host, *args, **kwargs):
        looked_up.append(host)
        raise socket.gaierror(-2, "offline in tests")

    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    out = check.check(download_dir=tmp_path, current="1.0.0")
    assert out.status == "error" and looked_up == ["api.github.com"]


def test_test_mode_contacts_only_loopback(monkeypatch, tmp_path):
    sys.path.insert(0, str(SRC.parents[1] / "tools"))
    from opent5.update.minisign import SecretKey
    from update_fake_server import FakeServer, release

    key = SecretKey.generate()
    targets = []
    real_connect = socket.socket.connect

    def connect(self, address):
        targets.append(address)
        return real_connect(self, address)

    with FakeServer() as server:
        server.add_releases("", [release(key, "9.0.0", b"exe" * 100, server.base)])
        monkeypatch.setenv(update.TEST_URL_ENV, server.base)
        monkeypatch.setenv(update.TEST_KEY_ENV, key.public.line())
        monkeypatch.setattr(socket.socket, "connect", connect)
        out = check.check(download_dir=tmp_path, current="1.0.0")
    assert out.status == "ready", out.message
    assert targets and {t[:2] for t in targets} == {("127.0.0.1", server.port)}
