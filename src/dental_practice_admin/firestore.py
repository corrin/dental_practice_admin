"""Scoped Firestore reads with renewable Firebase user authentication."""
from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx2 as httpx

from dental_practice_admin.audit import observed
from dental_practice_admin.config import PRINCIPLE_WEB_URLS, Settings


class Firestore:
    """A user's database permissions, narrowed to the configured practice root."""

    def __init__(self, settings: Settings,
                 transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.client = httpx.AsyncClient(transport=transport, timeout=60,
            headers={"Referer": PRINCIPLE_WEB_URLS[settings.environment] + "/"})
        self.token, self.refresh_token, self.expires = "", "", 0.0
        self.lock = asyncio.Lock()
        self.root = (f"projects/{settings.firebase_project}/databases/(default)/documents/"
                     + settings.firestore_root)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def authenticate(self) -> str:
        """Refresh before expiry; a rejected refresh gets one password sign-in."""
        async with self.lock:
            if time.monotonic() < self.expires:
                return self.token
            if self.refresh_token:
                response = await self.client.post(
                    self.settings.firebase_url("securetoken") + "/v1/token",
                    params={"key": self.settings.firebase_key},
                    data={"grant_type": "refresh_token", "refresh_token": self.refresh_token})
                if response.is_success:
                    value = response.json()
                    self.token, self.refresh_token = value["id_token"], value["refresh_token"]
                    self.expires = time.monotonic() + int(value["expires_in"]) - 60
                    return self.token
                if response.status_code >= 500:
                    raise RuntimeError("Firebase renewal unavailable")
                self.refresh_token = ""
            response = await self.client.post(
                self.settings.firebase_url("identitytoolkit")
                + "/v1/accounts:signInWithPassword",
                params={"key": self.settings.firebase_key}, json={
                    "email": self.settings.ui_email,
                    "password": self.settings.ui_password.get_secret_value(),
                    "returnSecureToken": True})
            if not response.is_success:
                raise RuntimeError("Firebase sign-in failed")
            value = response.json()
            self.token, self.refresh_token = value["idToken"], value["refreshToken"]
            self.expires = time.monotonic() + int(value["expiresIn"]) - 60
            return self.token

    @observed("firestore")
    async def read(self, path: str, query: dict[str, Any] | None = None,
                   aggregate: bool = False) -> Any:
        """Read a relative document or query parent; mutation endpoints are not accepted."""
        invalid = any(p in {".", "..", ""} for p in path.split("/")) and path
        if any(c in path for c in "%?#\\:") or invalid:
            raise ValueError("Expected a relative Firestore path")
        kind = "structuredAggregationQuery" if aggregate else "structuredQuery"
        if query is not None and kind not in query:
            raise ValueError("Expected a structured read query")
        token = await self.authenticate()
        url = (self.settings.firebase_url("firestore") + "/v1/" + self.root
               + ("/" + path if path else ""))
        if query is not None:
            url += ":runAggregationQuery" if aggregate else ":runQuery"
        response = await self.client.request("POST" if query is not None else "GET", url,
            json=query, headers={"Authorization": "Bearer " + token})
        if response.status_code == 401:
            self.expires = 0
            token = await self.authenticate()
            response = await self.client.request("POST" if query is not None else "GET", url,
                json=query, headers={"Authorization": "Bearer " + token})
        if not response.is_success:
            raise RuntimeError(f"Firestore read failed: {response.status_code}")
        return response.json()
