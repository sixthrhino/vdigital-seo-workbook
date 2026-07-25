from __future__ import annotations

from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

# Confirmed against a real Google Chat request's token claims: the issuer is
# this fixed service account, and the audience is the webhook URL itself —
# not the numeric project number some docs suggest as the default.
CHAT_ISSUER_EMAIL = "chat@system.gserviceaccount.com"


class ChatAuthError(Exception):
    pass


def verify_chat_bearer_token(authorization_header: str | None, audience: str) -> None:
    """Verify a Google Chat webhook request's bearer token.

    Raises ChatAuthError on any failure; callers should turn that into a 401
    rather than let the underlying exception type leak out.
    """
    if not authorization_header or not authorization_header.startswith("Bearer "):
        raise ChatAuthError("Missing bearer token")
    token = authorization_header.removeprefix("Bearer ")

    try:
        claims = id_token.verify_oauth2_token(token, google_requests.Request(), audience=audience)
    except Exception as exc:  # noqa: BLE001 - any verification failure is an auth failure
        raise ChatAuthError(f"Invalid token: {exc}") from exc

    if claims.get("email") != CHAT_ISSUER_EMAIL:
        raise ChatAuthError(f"Unexpected token issuer: {claims.get('email')!r}")
