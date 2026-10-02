"""Read-only Principle staging probes; import StagingBrowser for further browser scripts."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

import httpx2 as httpx
from dotenv import dotenv_values
from playwright.sync_api import Page, Response, sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from dental_practice_admin.config import STAGING_API_URL, Environment, Settings
from dental_practice_admin.principle import PrincipleClient

UI_URL = "https://staging.principle.dental"
DOCUMENT_PREFIX = "projects/principle-staging/databases/(default)/documents/"


def document_names(body: str) -> set[str]:
    """Extract staging references from JSON or escaped WebChannel response frames.

    References can describe missing documents; only a successful GET proves a record exists.
    Document IDs may contain punctuation, including dots and email addresses.
    """
    return set(re.findall(re.escape(DOCUMENT_PREFIX) + r'[^\s"\\]+', body))


async def check_api(env_file: Path) -> dict[str, Any]:
    """Read practices and practitioners using the application's staging configuration."""
    options: dict[str, Any] = {"_env_file": env_file, "environment": Environment.STAGING}
    settings = Settings(**options)
    if settings.api_base_url.rstrip("/") != STAGING_API_URL:
        raise ValueError("The check requires the standard staging API URL")
    async with PrincipleClient(settings) as client:
        practices = (await client.get("listPractices"))["data"]
        matched = any(row["id"] == settings.practice_id for row in practices)
        result: dict[str, Any] = {
            "ok": False,
            "practice_count": len(practices),
            "configured_practice_found": matched,
        }
        # A single returned practice is unambiguous for a read-only connectivity probe.
        practice = next((row for row in practices if row["id"] == settings.practice_id), None)
        if practice is None and len(practices) == 1:
            practice = practices[0]
        if practice is not None:
            rows = (await client.get(
                "listPractitioners", path_params={"practiceId": practice["id"]}
            ))["data"]
            result["practitioner_count"] = len(rows)
            result["ok"] = matched and bool(rows)
        return result


class StagingBrowser:
    """Authenticated staging page and bounded Firestore reads, with credentials in memory."""

    def __init__(self, page: Page) -> None:
        self.page = page
        self._token = ""
        self.documents: set[str] = set()
        self.firestore_statuses: set[int] = set()
        page.on("response", self._observe)

    def _observe(self, response: Response) -> None:
        url = urlsplit(response.url)
        if url.hostname == "identitytoolkit.googleapis.com" and (
            url.path.endswith("accounts:signInWithPassword") and response.status == 200
        ):
            self._token = response.json()["idToken"]
        if url.hostname == "firestore.googleapis.com":
            self.firestore_statuses.add(response.status)
            # Long-lived Listen responses can close before their bodies are available.
            with suppress(Exception):
                self.documents.update(document_names(response.text()))

    @property
    def token(self) -> str:
        """The Firebase ID token from the last login; it expires after an hour."""
        if not self._token:
            raise ValueError("Login is required before using the token")
        return self._token

    def login(self, email: str, password: str, redirect_to: str | None = None) -> None:
        """Submit the login form and require Firebase to issue an authenticated token."""
        if redirect_to is not None:
            entry = f"{UI_URL}/login?redirectTo={quote(redirect_to, safe='')}"
        else:
            entry = UI_URL
        self.page.goto(entry, wait_until="domcontentloaded")
        self.page.get_by_placeholder("Email", exact=True).fill(email)
        field = self.page.get_by_placeholder("Password", exact=True)
        field.fill(password)
        with self.page.expect_response(
            lambda r: urlsplit(r.url).hostname == "identitytoolkit.googleapis.com"
            and urlsplit(r.url).path.endswith("accounts:signInWithPassword")
        ) as response:
            field.press("Enter")
        if response.value.status != 200 or not self._token:
            raise ValueError("Staging login did not issue a token")

    def read_document(self, name: str) -> httpx.Response:
        """GET an exact staging document with the logged-in user's existing permissions."""
        if not name.startswith(DOCUMENT_PREFIX):
            raise ValueError("Only principle-staging documents are allowed")
        path = name.removeprefix(DOCUMENT_PREFIX)
        if any(part in {"", ".", ".."} for part in path.split("/")) or any(
            character in name for character in "?#%\\"
        ):
            raise ValueError("Expected an unescaped Firestore document name")
        if not self._token:
            raise ValueError("Login is required before reading Firestore")
        return httpx.get(
            "https://firestore.googleapis.com/v1/" + name,
            headers={"Authorization": "Bearer " + self._token},
            timeout=30,
        )


@contextmanager
def staging_browser(headed: bool = False) -> Iterator[StagingBrowser]:
    """Open an isolated browser; closing it discards the authenticated session."""
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=not headed)
        try:
            context = browser.new_context()
            yield StagingBrowser(context.new_page())
        finally:
            browser.close()


def check_browser(env_file: Path, workspace: str, headed: bool) -> dict[str, Any]:
    """Check workspace access and at most three observed Firestore document references."""
    values = {**dotenv_values(env_file), **os.environ}
    email = values.get("PRINCIPLE_UI_EMAIL")
    password = values.get("PRINCIPLE_UI_PASSWORD")
    if not email or not password:
        raise ValueError("PRINCIPLE_UI_EMAIL and PRINCIPLE_UI_PASSWORD are required")
    with staging_browser(headed) as session:
        session.login(email, password)
        page = session.page
        selected = False
        try:
            option = page.get_by_text(workspace, exact=True).first
            option.wait_for(state="visible", timeout=20000)
            option.click()
            page.wait_for_url(lambda url: "/login" not in url, timeout=20000)
            selected = True
        except PlaywrightTimeout:
            pass
        website = {"ok": selected, "login_succeeded": True, "workspace_opened": selected}
        reads = []
        for name in sorted(session.documents)[:3]:
            response = session.read_document(name)
            data = response.json() if response.status_code == 200 else {}
            reads.append({
                "status": response.status_code,
                "field_count": len(data.get("fields", {})),
            })
        return {
            "website": website,
            "firestore": {
                "ok": any(row["status"] == 200 and row["field_count"] > 0 for row in reads),
                "transport_statuses": sorted(session.firestore_statuses),
                "observed_document_references": len(session.documents),
                "reads": reads,
            },
        }


def main() -> int:
    """Print counts and statuses only; failed or incomplete checks exit nonzero."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", choices=("all", "api", "browser"), default="all")
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--ui-env-file", type=Path, help="Explicit separate browser credentials file"
    )
    parser.add_argument("--workspace", default="Massey Smiles")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    results: dict[str, Any] = {}
    if args.only in {"all", "api"}:
        try:
            results["api"] = asyncio.run(check_api(args.env_file))
        except Exception as error:
            results["api"] = {"ok": False, "error_type": type(error).__name__}
    if args.only in {"all", "browser"}:
        try:
            results.update(check_browser(
                args.ui_env_file or args.env_file, args.workspace, args.headed
            ))
        except Exception as error:
            # Exceptions can include request headers, document IDs, or rendered patient text.
            results["browser"] = {"ok": False, "error_type": type(error).__name__}
    print(json.dumps(results, indent=2))
    return 0 if all(result["ok"] for result in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
