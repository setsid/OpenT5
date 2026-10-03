"""HTTP GET for the update check: the only module in OpenT5 that opens connections.

Every request is a plain GET carrying exactly two chosen headers, User-Agent
("OpenT5/<version>") and Accept; urllib adds only Host, Accept-Encoding: identity and
Connection: close. No cookies, no credentials, no proxies, no IDs. HTTPS with the
default certificate and host name checks. Redirects are followed by hand (at most
three), and each hop must again be an allowed host. In test mode (the override in
opent5.update.TEST_URL_ENV) the only allowed address is http://127.0.0.1:<that port>.
"""

from __future__ import annotations

import os
import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from urllib.parse import urljoin, urlsplit

import opent5
from opent5 import update

MAX_REDIRECTS = 3
API_BASE = f"https://{update.API_HOST}"
_TEST_URL = re.compile(r"^http://127\.0\.0\.1:([1-9]\d{0,4})(/[A-Za-z0-9._~/-]*)?$")


class NetError(Exception):
    """A request that failed. kind is one of: offline, dns, timeout, tls, rate-limited,
    not-found, http, refused, too-large, incomplete."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def user_agent() -> str:
    return f"{opent5.APP_NAME}/{opent5.__version__}"


def test_override() -> tuple[str, int] | None:
    """(base URL, port) from the test override, None when it is not set.

    Raises NetError("refused") when it is set to anything but http://127.0.0.1:<port>.
    """
    value = os.environ.get(update.TEST_URL_ENV, "")
    if not value:
        return None
    m = _TEST_URL.match(value)
    if not m or int(m.group(1)) > 65535:
        raise NetError(
            "refused",
            f"{update.TEST_URL_ENV} must be http://127.0.0.1:<port>[/path], found {value!r}",
        )
    return value.rstrip("/"), int(m.group(1))


def check_url(url: str, test_port: int | None) -> None:
    """Raise NetError("refused") unless URL may be fetched."""
    parts = urlsplit(url)
    try:
        port = parts.port
    except ValueError:
        port = -1
    if parts.username is not None or parts.password is not None:
        ok = False
    elif test_port is not None:
        ok = parts.scheme == "http" and parts.hostname == "127.0.0.1" and port == test_port
    else:
        ok = (
            parts.scheme == "https"
            and parts.hostname in update.ALLOWED_HOSTS
            and port in (None, 443)
        )
    if not ok:
        where = (
            f"http://127.0.0.1:{test_port}" if test_port is not None else
            ", ".join(f"https://{h}" for h in update.ALLOWED_HOSTS)
        )  # fmt: skip
        raise NetError("refused", f"refused to fetch {url!r}: only {where} is allowed")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ARG002
        return None  # surfaces as HTTPError 3xx; fetch() follows it after checking the host


def _opener() -> urllib.request.OpenerDirector:
    context = ssl.create_default_context()  # verifies certificates and host names
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        urllib.request.HTTPSHandler(context=context),
        _NoRedirect(),
    )


def fetch(
    url: str,
    *,
    accept: str,
    limit: int,
    test_port: int | None,
    timeout: float = 20.0,
    total_timeout: float = 900.0,
    sink: Callable[[bytes], None] | None = None,
) -> bytes:
    """GET URL and return the body, or pass it to SINK in chunks (then b"" is returned).

    timeout bounds each connect and read; total_timeout bounds the whole transfer.
    """
    deadline = time.monotonic() + total_timeout
    for _ in range(MAX_REDIRECTS + 1):
        check_url(url, test_port)
        request = urllib.request.Request(
            url, headers={"User-Agent": user_agent(), "Accept": accept}, method="GET"
        )
        try:
            response = _opener().open(request, timeout=timeout)
        except urllib.error.HTTPError as exc:
            with exc:
                if exc.code in (301, 302, 303, 307, 308):
                    location = exc.headers.get("Location")
                    if not location:
                        raise NetError("http", f"HTTP {exc.code} without a Location") from None
                    url = urljoin(url, location)
                    continue
                raise _http_error(exc.code, url) from None
        except urllib.error.URLError as exc:
            raise _classify(exc.reason, url) from None
        except OSError as exc:
            raise _classify(exc, url) from None
        with response:
            try:
                return _read(response, limit, deadline, sink)
            except NetError:
                raise
            except OSError as exc:
                raise _classify(exc, url) from None
    raise NetError("http", f"more than {MAX_REDIRECTS} redirects")


def _read(response, limit: int, deadline: float, sink) -> bytes:
    length = response.headers.get("Content-Length")
    if length is not None and length.isdigit() and int(length) > limit:
        raise NetError("too-large", f"expected at most {limit} bytes, server offers {length}")
    chunks: list[bytes] = []
    total = 0
    while True:
        if time.monotonic() > deadline:
            raise NetError("timeout", "the download took too long")
        chunk = response.read(256 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise NetError("too-large", f"expected at most {limit} bytes, received more")
        if sink is None:
            chunks.append(chunk)
        else:
            sink(chunk)
    return b"".join(chunks)


def _http_error(code: int, url: str) -> NetError:
    host = urlsplit(url).hostname
    if code in (403, 429):
        return NetError("rate-limited", f"{host} answered HTTP {code} (rate limited or refused)")
    if code == 404:
        return NetError("not-found", f"{host} answered HTTP 404 (no such repository, or private)")
    return NetError("http", f"{host} answered HTTP {code}")


def _classify(reason, url: str) -> NetError:
    host = urlsplit(url).hostname
    if isinstance(reason, socket.gaierror):
        return NetError("dns", f"could not look up {host}: {reason}")
    if isinstance(reason, TimeoutError | socket.timeout):
        return NetError("timeout", f"{host} did not answer in time")
    if isinstance(reason, ssl.SSLError | ssl.CertificateError):
        return NetError("tls", f"secure connection to {host} failed: {reason}")
    return NetError("offline", f"could not reach {host}: {reason}")
