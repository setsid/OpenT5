"""The update check end to end against a local fake release server (127.0.0.1 only)."""

from __future__ import annotations

import os
import socket
import sys
import threading
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

from opent5 import update  # noqa: E402
from opent5.update import check, net  # noqa: E402
from opent5.update import version as semver  # noqa: E402
from opent5.update.minisign import SecretKey  # noqa: E402
from update_fake_server import FakeServer, release  # noqa: E402

KEY = SecretKey.generate()
EXE = os.urandom(70_000)
CURRENT = "1.2.3"


@pytest.fixture
def server(monkeypatch):
    with FakeServer() as s:
        monkeypatch.setenv(update.TEST_URL_ENV, s.base)
        monkeypatch.setenv(update.TEST_KEY_ENV, KEY.public.line())
        yield s


def run(server, prefix: str, releases: list[dict], tmp_path: Path, **kw) -> check.Outcome:
    server.add_releases(prefix, releases, kw.pop("status", 200))
    cfg = check.config()
    assert not isinstance(cfg, str), cfg
    cfg.base = server.prefix_url(prefix)
    return check.check(download_dir=tmp_path, current=CURRENT, cfg=cfg, **kw)


def rel(server, prefix: str, version: str = "1.3.0", **kw) -> dict:
    return release(KEY, version, EXE, server.prefix_url(prefix), **kw)


# -- versions and selection ------------------------------------------------------------


def test_version_parse():
    assert semver.parse("v1.2.3") == (1, 2, 3)
    assert semver.parse("1.2.3+build.5") == (1, 2, 3)
    assert semver.parse("1.2.3-rc.1") is None and semver.is_prerelease("1.2.3-rc.1")
    for bad in ("1.2", "01.2.3", "1.2.3.4", "latest", "v1.2.x", ""):
        assert semver.parse(bad) is None


def _r(v: str) -> check.Release:
    return check.Release(semver.parse(v), "v" + v, "", {})


def test_pick_highest_never_downgrade_equal_ignored():
    rels = [_r("1.2.2"), _r("1.2.3"), _r("1.10.0"), _r("1.9.9")]
    assert check.pick(rels, "1.2.3").text == "1.10.0"
    assert check.pick([_r("1.2.3")], "1.2.3") is None  # equal: nothing to do
    assert check.pick([_r("1.0.0"), _r("0.9.0")], "1.2.3") is None  # never a downgrade
    assert check.pick(rels, "1.2.3", failed={"1.10.0"}).text == "1.9.9"


def test_drafts_prereleases_and_odd_tags_ignored():
    body = b"""[
      {"tag_name": "v9.0.0", "draft": true, "prerelease": false, "assets": []},
      {"tag_name": "v8.0.0", "draft": false, "prerelease": true, "assets": []},
      {"tag_name": "v7.0.0-beta.1", "draft": false, "prerelease": false, "assets": []},
      {"tag_name": "nightly", "draft": false, "prerelease": false, "assets": []},
      {"tag_name": "v1.4.0", "draft": false, "prerelease": false, "body": "notes", "assets": []}
    ]"""
    rels = check.parse_releases(body)
    assert [r.text for r in rels] == ["1.4.0"]
    assert rels[0].notes == "notes"


# -- end to end ------------------------------------------------------------------------


def test_ready_downloads_and_verifies(server, tmp_path):
    out = run(server, "ok", [rel(server, "ok", notes="Fixes things.")], tmp_path)
    assert out.status == "ready", out.message
    assert out.version == "1.3.0" and out.notes == "Fixes things." and out.test_mode
    assert out.message == "Version 1.3.0 is ready. Restart to update."
    assert out.path.read_bytes() == EXE
    assert not list(tmp_path.glob("*.part"))
    # Every request carried only the client's two headers plus urllib's three fixed ones.
    for _path, headers in server.requests:
        assert set(headers) == {"Accept-Encoding", "Host", "User-Agent", "Connection", "Accept"}
        assert headers["User-Agent"].startswith("OpenT5/")
        assert headers["Accept-Encoding"] == "identity"
    # A second check reuses the verified file without downloading the exe again.
    before = len(server.requests)
    again = check.check(download_dir=tmp_path, current=CURRENT, cfg=_cfg(server, "ok"))
    assert again.status == "ready" and again.path == out.path
    # The list, then manifest and signature (each an asset URL plus its redirect): no exe.
    assert len(server.requests) - before == 5


def _cfg(server, prefix):
    cfg = check.config()
    cfg.base = server.prefix_url(prefix)
    return cfg


def test_highest_eligible_release_chosen(server, tmp_path):
    rels = [
        rel(server, "many", "1.3.0"),
        rel(server, "many", "2.0.0", prerelease=True),
        rel(server, "many", "1.4.0"),
        rel(server, "many", "1.5.0", draft=True),
    ]
    out = run(server, "many", rels, tmp_path)
    assert out.status == "ready" and out.version == "1.4.0"


@pytest.mark.parametrize(
    "case",
    [
        {"tamper": "exe"},
        {"tamper": "size"},
        {"tamper": "manifest"},
        {"tamper": "comment"},
        {"sign_key": SecretKey.generate()},
        {"manifest_version": "1.3.1"},
    ],
    ids=["tampered-exe", "wrong-size", "tampered-manifest", "tampered-comment", "wrong-key",
         "manifest-version-mismatch"],
)  # fmt: skip
def test_verification_failures_discard(server, tmp_path, case):
    out = run(server, "bad", [rel(server, "bad", **case)], tmp_path)
    assert out.status == "failed-verification", out.message
    assert out.version == "1.3.0"
    assert out.message.startswith("The update to 1.3.0 failed verification and was discarded")
    assert list(tmp_path.glob("*")) == []  # nothing left behind


def test_failed_version_not_retried(server, tmp_path):
    out = run(server, "f", [rel(server, "f", tamper="exe")], tmp_path, failed={"1.3.0"})
    assert out.status == "up-to-date"
    assert [p for p, _ in server.requests if "/assets/" in p] == []  # nothing downloaded


def test_missing_asset_is_retryable_not_failed(server, tmp_path):
    out = run(server, "m", [rel(server, "m", omit=(update.SIGNATURE_ASSET,))], tmp_path)
    assert out.status == "error" and out.error_kind == "incomplete"


@pytest.mark.parametrize(
    "status,kind", [(404, "not-found"), (403, "rate-limited"), (429, "rate-limited"),
                    (500, "http")]
)  # fmt: skip
def test_http_errors_fail_quietly(server, tmp_path, status, kind):
    out = run(server, "e", [], tmp_path, status=status)
    assert out.status == "error" and out.error_kind == kind


def test_offline_connection_refused(monkeypatch, tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()  # nothing listens there now
    monkeypatch.setenv(update.TEST_URL_ENV, f"http://127.0.0.1:{port}")
    monkeypatch.setenv(update.TEST_KEY_ENV, KEY.public.line())
    out = check.check(download_dir=tmp_path, current=CURRENT)
    assert out.status == "error" and out.error_kind == "offline"


def test_timeout(monkeypatch, tmp_path):
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)  # accepts the connection, never answers
    try:
        monkeypatch.setenv(update.TEST_URL_ENV, f"http://127.0.0.1:{s.getsockname()[1]}")
        monkeypatch.setenv(update.TEST_KEY_ENV, KEY.public.line())
        monkeypatch.setattr(check, "LIST_TIMEOUT", 0.3)
        out = check.check(download_dir=tmp_path, current=CURRENT)
    finally:
        s.close()
    assert out.status == "error" and out.error_kind == "timeout"


def _production(monkeypatch):
    monkeypatch.delenv(update.TEST_URL_ENV, raising=False)
    monkeypatch.setattr(update, "REPO", "example/opent5")
    monkeypatch.setattr(update, "PUBLIC_KEY", KEY.public.line())


def _no_sockets(monkeypatch, exc):
    calls = []

    def fail(*a, **k):
        calls.append(a)
        raise exc

    monkeypatch.setattr(socket, "getaddrinfo", fail)
    monkeypatch.setattr(socket.socket, "connect", fail)
    return calls


def test_dns_failure(monkeypatch, tmp_path):
    _production(monkeypatch)
    calls = _no_sockets(monkeypatch, socket.gaierror(-2, "Name or service not known"))
    out = check.check(download_dir=tmp_path, current=CURRENT)
    assert out.status == "error" and out.error_kind == "dns"
    assert calls and calls[0][0] == "api.github.com"


def test_empty_repo_disables_without_any_request(monkeypatch, tmp_path):
    monkeypatch.delenv(update.TEST_URL_ENV, raising=False)
    monkeypatch.setattr(update, "REPO", "")
    calls = _no_sockets(monkeypatch, AssertionError("no network expected"))
    out = check.check(download_dir=tmp_path, current=CURRENT)
    assert out.status == "disabled" and "REPO is empty" in out.message
    assert calls == []


def test_empty_public_key_disables(monkeypatch, tmp_path):
    _production(monkeypatch)
    monkeypatch.setattr(update, "PUBLIC_KEY", "")
    _no_sockets(monkeypatch, AssertionError("no network expected"))
    assert check.check(download_dir=tmp_path, current=CURRENT).status == "disabled"


# -- the override and the host rules ---------------------------------------------------


@pytest.mark.parametrize(
    "value",
    ["https://127.0.0.1:8000", "http://localhost:8000", "http://127.0.0.2:8000",
     "http://0.0.0.0:8000", "http://192.168.1.2:80", "http://user@127.0.0.1:8000",
     "http://127.0.0.1", "http://127.0.0.1:99999", "http://127.0.0.1:80?x=1",
     "https://api.github.com"],
)  # fmt: skip
def test_override_refuses_anything_but_loopback_http(monkeypatch, tmp_path, value):
    monkeypatch.setenv(update.TEST_URL_ENV, value)
    monkeypatch.setenv(update.TEST_KEY_ENV, KEY.public.line())
    with pytest.raises(net.NetError) as e:
        net.test_override()
    assert e.value.kind == "refused"
    out = check.check(download_dir=tmp_path, current=CURRENT)
    assert out.status == "disabled"


def test_override_needs_a_test_key(monkeypatch, tmp_path):
    monkeypatch.setenv(update.TEST_URL_ENV, "http://127.0.0.1:8000")
    monkeypatch.delenv(update.TEST_KEY_ENV, raising=False)
    assert check.check(download_dir=tmp_path, current=CURRENT).status == "disabled"


def test_production_url_rules():
    for ok in ("https://api.github.com/repos/a/b/releases",
               "https://release-assets.githubusercontent.com/x",
               "https://objects.githubusercontent.com/x"):  # fmt: skip
        net.check_url(ok, None)
    for bad in ("http://api.github.com/x", "https://github.com/x", "https://evil.com/x",
                "https://api.github.com:8443/x", "https://u:p@api.github.com/x",
                "https://api.github.com.evil.com/x", "file:///etc/passwd",
                "http://127.0.0.1:1234/x"):  # fmt: skip
        with pytest.raises(net.NetError):
            net.check_url(bad, None)
    net.check_url("http://127.0.0.1:1234/x", 1234)
    with pytest.raises(net.NetError):
        net.check_url("http://127.0.0.1:1235/x", 1234)


def test_redirect_off_the_allowed_host_refused(server, tmp_path):
    # An asset that redirects to another port (another host, in production terms).
    r = rel(server, "redir")
    r["assets"][0]["url"] = "http://127.0.0.1:1/elsewhere"
    out = run(server, "redir", [r], tmp_path)
    assert out.status == "error" and out.error_kind == "refused"


def test_check_runs_in_a_worker_thread_without_blocking(server, tmp_path):
    server.add_releases("t", [rel(server, "t")])
    results = []
    t = threading.Thread(
        target=lambda: results.append(
            check.check(download_dir=tmp_path, current=CURRENT, cfg=_cfg(server, "t"))
        )
    )
    t.start()
    t.join(20)
    assert results and results[0].status == "ready"
