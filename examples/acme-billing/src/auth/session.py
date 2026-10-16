"""Signed session cookies for the billing console."""

SESSION_TTL_SECONDS = 900


def verify_session(token: str, now: int) -> dict:
    payload = decode_token(token)
    if now - payload["iat"] > SESSION_TTL_SECONDS:
        raise PermissionError("session expired")
    return payload


def decode_token(token: str) -> dict:
    # The sample stores a tiny stand-in for a signed payload.
    kind, issued, subject = token.split(":", 2)
    if kind != "sess":
        raise PermissionError("wrong token kind")
    return {"iat": int(issued), "sub": subject}
