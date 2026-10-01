"""Who is asking. Google holds the credentials; this application holds the guest list.

There is no password store here, which is the point: nothing to leak, nothing to reset, and
staff use the account they already sign in to every morning. Authorisation is an allowlist of
addresses plus an optional Workspace domain, both in `Settings`.

The identity a request carries is the trusted scope for everything downstream — a chat thread
belongs to an address, and the ChatKit store checks it on every operation. It never comes from
a request body or a model argument.

Two providers behind one dependency:

  production/staging  Google, via authlib
  fake                a fixed local user, so the test suite and a developer's browser need no
                      OAuth round-trip. Unavailable outside `environment=fake` by construction.

Deployment note: Google refuses a redirect URI that is neither HTTPS nor `localhost`. Staff
sign-in therefore requires a stable internal hostname with a certificate, which settles the
HTTPS question in ARCHITECTURE.md rather than leaving it open.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Any, cast

from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse
from starlette.responses import Response

from principle_admin.config import Environment, Settings, current_settings
from principle_admin.storage import Storage

GOOGLE_METADATA_URL = "https://accounts.google.com/.well-known/openid-configuration"

# The local identity used when `environment=fake`. The address is in a reserved TLD so it can
# never collide with a real account, and its presence in a production database would be
# obvious rather than plausible.
FAKE_STAFF = "dev@fake.invalid"

SESSION_USER_KEY = "staff"


@dataclass(frozen=True)
class StaffUser:
    """An authenticated member of practice staff.

    `email` is the durable identity: it scopes conversations and is recorded as the initiator
    of anything the user causes. `name` is for display only and may change.
    """

    email: str
    name: str

    @property
    def is_fake(self) -> bool:
        return self.email == FAKE_STAFF


def build_oauth(settings: Settings) -> OAuth:
    """Google, configured from settings. Discovery gives us the endpoints and the JWKS."""
    oauth = OAuth()
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
        server_metadata_url=GOOGLE_METADATA_URL,
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth


def staff_from_session(request: Request, settings: Settings) -> StaffUser | None:
    """The signed-in user, or None.

    In `fake` there is no sign-in at all: a developer running locally, and every test, gets the
    same local identity. This branch is guarded on the environment rather than on a flag so
    that it cannot be switched on in production by configuration alone.
    """
    if settings.environment is Environment.FAKE:
        return StaffUser(email=FAKE_STAFF, name="Development user")
    stored = _session(request).get(SESSION_USER_KEY)
    if not isinstance(stored, dict):
        return None
    email, name = stored.get("email"), stored.get("name")
    if not isinstance(email, str) or not isinstance(name, str):
        return None
    # Re-check the allowlist on every request. Revoking access must take effect immediately,
    # not whenever the user's cookie happens to expire.
    if not settings.admits(email, email_verified=True):
        return None
    return StaffUser(email=email, name=name)


def require_staff(
    request: Request, settings: Annotated[Settings, Depends(current_settings)]
) -> StaffUser:
    """The signed-in user, or a refusal. Every staff-facing route depends on this."""
    user = staff_from_session(request, settings)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in with an approved Google account.",
            headers={"Location": "/auth/login"},
        )
    return user


CurrentStaff = Annotated[StaffUser, Depends(require_staff)]


router = APIRouter(prefix="/auth", tags=["auth"])


def _session(request: Request) -> dict[str, Any]:
    """The signed cookie session.

    Refuses clearly when `SessionMiddleware` is absent. Without this the framework's own assertion
    surfaces as a request that never completes, and the caller sees a hang rather than a cause.
    """
    if "session" not in request.scope:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="SessionMiddleware is not installed; sign-in cannot work",
        )
    session: dict[str, Any] = request.session
    return session


@router.get("/login")
async def login(
    request: Request, settings: Annotated[Settings, Depends(current_settings)]
) -> Response:
    """Start the Google flow, or go straight in when running against the fake."""
    if settings.environment is Environment.FAKE:
        return RedirectResponse("/")
    oauth: OAuth = request.app.state.oauth
    # Built from the public origin, not from `url_for` alone: behind Caddy the latter names the
    # internal host and port, and Google matches the registered redirect URI exactly.
    callback = f"{settings.public_origin(request)}{request.url_for('callback').path}"
    redirect = await oauth.google.authorize_redirect(request, callback)
    return cast("Response", redirect)


@router.get("/callback", name="callback")
async def callback(
    request: Request, settings: Annotated[Settings, Depends(current_settings)]
) -> Response:
    """Complete the flow, check the guest list, and start a session.

    A Google account that is not on the list gets 403, not a redirect back to the login page: a
    loop would leave the person guessing, and the refusal is deliberate rather than transient.
    """
    oauth: OAuth = request.app.state.oauth
    token: dict[str, Any] = await oauth.google.authorize_access_token(request)
    claims = token.get("userinfo") or {}
    email = str(claims.get("email") or "")
    verified = bool(claims.get("email_verified"))

    if not settings.admits(email, verified):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"{email or 'that account'} is not approved for this practice. "
                "Ask whoever administers Principle_admin to add it."
            ),
        )
    address = email.strip().lower()
    request.session[SESSION_USER_KEY] = {
        "email": address,
        "name": str(claims.get("name") or email),
    }

    storage = Storage(settings.database_path)
    try:
        storage.record_sign_in(address, request.client.host if request.client else None)
    finally:
        storage.close()
    return RedirectResponse("/")


@router.get("/logout")
async def logout(
    request: Request, settings: Annotated[Settings, Depends(current_settings)]
) -> Response:
    """End the session. Google's own sign-in is untouched: this is not a Google logout.

    Signing out of the fake environment is a no-op, because there was never a session: the
    middleware that provides one is only installed outside `fake`. Reaching for
    `request.session` unconditionally is what made this route hang instead of redirect.
    """
    if settings.environment is not Environment.FAKE:
        _session(request).pop(SESSION_USER_KEY, None)
    return RedirectResponse("/")
