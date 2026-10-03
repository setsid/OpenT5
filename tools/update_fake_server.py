"""A fake GitHub Releases server on 127.0.0.1, for the update tests and the exe smoke test.

    server = FakeServer()                       # binds 127.0.0.1 on a free port, never 0.0.0.0
    key = SecretKey.generate()                  # a throwaway test key
    server.add_releases("good", [release(key, "9.0.0", b"exe bytes", server.base + "/good")])
    with server:
        ... point OPENT5_UPDATE_TEST_URL at server.base ...
    server.requests                             # [(path, headers)] seen, for assertions

Each scenario is a URL prefix holding a release list at
<prefix>/repos/<owner>/<name>/releases and its assets at <prefix>/assets/<id>, which
redirect (302) to <prefix>/dl/<id>, as GitHub's API redirects to its asset host.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from opent5 import update
from opent5.update.minisign import SecretKey

_ids = itertools.count(1)


def manifest_bytes(version: str, file: str, data: bytes) -> bytes:
    m = {
        "name": "OpenT5",
        "version": version,
        "file": file,
        "size": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }
    return (json.dumps(m, indent=2, sort_keys=True) + "\n").encode()


def trusted_comment(version: str, file: str) -> str:
    return f"OpenT5 manifest version:{version} file:{file}"


def release(
    key: SecretKey,
    version: str,
    exe: bytes,
    prefix_url: str,
    *,
    file: str = "OpenT5.exe",
    tamper: str = "",
    draft: bool = False,
    prerelease: bool = False,
    notes: str = "",
    sign_key: SecretKey | None = None,
    manifest_version: str | None = None,
    omit: tuple[str, ...] = (),
) -> dict:
    """A release dict (GitHub's shape) plus a "_files" map {asset id: bytes} to serve.

    tamper: "exe" (one byte of the served exe changed), "manifest" (the served manifest
    edited after signing), "comment" (the trusted comment edited), "size" (the served
    exe has extra bytes), "" (none). sign_key signs instead of KEY (a wrong key).
    manifest_version writes a different version into the (validly signed) manifest.
    """
    manifest = manifest_bytes(manifest_version or version, file, exe)
    sig = (sign_key or key).sign(manifest, trusted_comment(manifest_version or version, file))
    served_exe, served_manifest = exe, manifest
    if tamper == "exe":
        served_exe = bytes([exe[0] ^ 1]) + exe[1:]
    elif tamper == "size":
        served_exe = exe + b"\0" * 16
    elif tamper == "manifest":
        served_manifest = manifest.replace(b'"name": "OpenT5"', b'"name": "OpenT6"')
    elif tamper == "comment":
        sig = sig.replace("version:", "version: ")
    files = {update.MANIFEST_ASSET: served_manifest, update.SIGNATURE_ASSET: sig.encode()}
    files[file] = served_exe
    assets, by_id = [], {}
    for name, data in files.items():
        if name in omit:
            continue
        i = next(_ids)
        by_id[i] = data
        size = len(exe) if name == file else len(data)  # GitHub lists the uploaded size
        assets.append(
            {
                "id": i,
                "name": name,
                "size": size,
                "url": f"{prefix_url}/assets/{i}",
                "browser_download_url": f"{prefix_url}/download/{name}",
            }
        )
    return {
        "tag_name": f"v{version}",
        "name": f"OpenT5 {version}",
        "draft": draft,
        "prerelease": prerelease,
        "body": notes,
        "assets": assets,
        "_files": by_id,
    }


class FakeServer:
    def __init__(self) -> None:
        self.lists: dict[str, list[dict]] = {}
        self.status: dict[str, int] = {}  # prefix -> forced HTTP status for the list
        self.files: dict[tuple[str, int], bytes] = {}
        self.requests: list[tuple[str, dict]] = []
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # quiet
                pass

            def do_GET(self):  # noqa: N802
                server.requests.append((self.path, dict(self.headers)))
                server._handle(self)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        self._thread: threading.Thread | None = None

    def prefix_url(self, prefix: str) -> str:
        return f"{self.base}/{prefix}" if prefix else self.base

    def add_releases(self, prefix: str, releases: list[dict], status: int = 200) -> None:
        self.lists[prefix] = [{k: v for k, v in r.items() if k != "_files"} for r in releases]
        self.status[prefix] = status
        for r in releases:
            for i, data in r.get("_files", {}).items():
                self.files[(prefix, i)] = data

    def _handle(self, h: BaseHTTPRequestHandler) -> None:
        path = h.path.split("?", 1)[0]
        for prefix in sorted(self.lists, key=len, reverse=True):
            lead = f"/{prefix}" if prefix else ""
            if not path.startswith(lead + "/"):
                continue
            rest = path[len(lead) :]
            if rest.startswith("/repos/") and rest.endswith("/releases"):
                status = self.status.get(prefix, 200)
                body = json.dumps(self.lists[prefix]).encode() if status == 200 else b"{}"
                return self._send(h, status, body, "application/json")
            if rest.startswith("/assets/"):
                h.send_response(302)
                h.send_header("Location", f"{lead}/dl/{rest[len('/assets/') :]}")
                h.send_header("Content-Length", "0")
                h.end_headers()
                return None
            if rest.startswith("/dl/"):
                data = self.files.get((prefix, int(rest[len("/dl/") :])))
                if data is not None:
                    return self._send(h, 200, data, "application/octet-stream")
        return self._send(h, 404, b"{}", "application/json")

    @staticmethod
    def _send(h, status: int, body: bytes, ctype: str) -> None:
        h.send_response(status)
        h.send_header("Content-Type", ctype)
        h.send_header("Content-Length", str(len(body)))
        h.end_headers()
        h.wfile.write(body)

    def __enter__(self) -> FakeServer:
        self._thread = threading.Thread(
            target=self.httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
