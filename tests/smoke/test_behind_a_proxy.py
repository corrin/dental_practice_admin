"""The application must know its own public origin when Caddy is in front of it.

Behind a reverse proxy the socket says `127.0.0.1:8080` and the browser says
`https://admin.massey-smiles.co.nz`. Anything the application builds from the former is wrong, and
the Google OAuth redirect is exact-match: a redirect naming the internal host is refused by Google
and sign-in fails for everyone.

The headers sent here are the ones Caddy sends by default, so this exercises the real mechanism:
uvicorn's `--proxy-headers` handling and the application's own URL construction.
"""

from __future__ import annotations

import httpx2 as httpx
import pytest

pytestmark = pytest.mark.smoke

PUBLIC_HOST = "admin.massey-smiles.co.nz"

# What Caddy puts on every proxied request.
CADDY_HEADERS = {
    "X-Forwarded-Proto": "https",
    "X-Forwarded-Host": PUBLIC_HOST,
    "X-Forwarded-For": "203.0.113.7",
    "Host": PUBLIC_HOST,
}


def test_health_reports_the_public_origin_not_the_socket(spine: dict[str, str]) -> None:
    """The application must build URLs from the origin staff reach, not the one it listens on.

    Removing `--proxy-headers` from the service definition, or reading the socket directly, breaks
    Google sign-in in production while every other test stays green. This is the cheapest place to
    observe it, and `scripts/verify.ps1` can check the same field on the real host.
    """
    health = httpx.get(f"{spine['APP_URL']}/health", headers=CADDY_HEADERS, timeout=30).json()
    assert health["baseUrl"] == f"https://{PUBLIC_HOST}", (
        f"the application believes it is at {health.get('baseUrl')!r}; "
        "a Google redirect built from that will not match the registered URI"
    )


def test_without_proxy_headers_the_origin_is_the_socket(spine: dict[str, str]) -> None:
    """Unproxied, the application must report where it actually is.

    Guards the opposite mistake: hard-coding the public hostname would make the reported origin
    right in production and wrong everywhere else, including this suite.
    """
    health = httpx.get(f"{spine['APP_URL']}/health", timeout=30).json()
    assert health["baseUrl"].startswith("http://127.0.0.1:")
