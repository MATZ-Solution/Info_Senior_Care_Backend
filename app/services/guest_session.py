"""
Guest sessions -- for users who tap "Continue as Guest" and never sign up.

We do NOT create a Supabase auth user for guests (that would pollute the
auth.users table and complicate the real user base). Instead we issue our
own short-lived signed token, prefixed 'guest_' so it's trivially
distinguishable from a Supabase JWT at the dependency layer.

Guest sessions are intentionally limited: no saved facilities persistence
across devices, no inquiry history -- just enough identity to let them
browse and submit a single inquiry. If a guest later signs up, the app
should call /api/v1/auth/sync-profile with their new real token; merging
guest activity into a real account is a product decision left as a TODO
hook (`migrate_guest_activity`) below.
"""
import time
import uuid
from typing import Optional

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.core.config import settings

_GUEST_TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days

_serializer = URLSafeTimedSerializer(
    secret_key=settings.SUPABASE_JWT_SECRET,  # reuse existing secret, no new config needed
    salt="guest-session-v1",
)


def issue_guest_token() -> str:
    guest_id = str(uuid.uuid4())
    signed = _serializer.dumps({"guest_id": guest_id, "issued_at": int(time.time())})
    return f"guest_{signed}"


def verify_guest_token(token: str) -> Optional[str]:
    if not token.startswith("guest_"):
        return None
    raw = token[len("guest_"):]
    try:
        data = _serializer.loads(raw, max_age=_GUEST_TOKEN_MAX_AGE_SECONDS)
        return data.get("guest_id")
    except (BadSignature, SignatureExpired):
        return None


def migrate_guest_activity(guest_id: str, real_user_id: str) -> None:
    """
    TODO (product decision needed): when a guest signs up for real, decide
    whether to transfer their guest inquiries to the new account. Left as an
    explicit hook rather than silently doing nothing, so it isn't forgotten.
    """
    raise NotImplementedError(
        "Guest-to-real-account migration not yet implemented -- "
        "confirm desired behavior before wiring this up."
    )
