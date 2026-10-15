import logging
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone

from jose import jwt
from passlib.context import CryptContext

from app.core.config import settings

# passlib 1.7 prints a harmless "error reading bcrypt version" traceback with bcrypt 4.x
logging.getLogger("passlib").setLevel(logging.ERROR)

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
ALGORITHM = "HS256"
# Used when the username does not exist, so a wrong username takes as long as a wrong password.
_DUMMY_HASH = pwd_context.hash("ncm-dummy-password-for-timing")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    if not password_hash:
        pwd_context.verify(password, _DUMMY_HASH)
        return False
    try:
        return pwd_context.verify(password, password_hash)
    except (ValueError, TypeError):
        return False


def create_access_token(subject: str, version: int = 0):
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=settings.access_token_expire_minutes)
    return jwt.encode({"sub": subject, "ver": int(version or 0), "iat": now, "exp": expire, "typ": "access"},
                      settings.secret_key, algorithm=ALGORITHM)


# ───────────────────────── password policy ─────────────────────────

PASSWORD_MIN = 10
_COMMON = {
    "password", "password1", "password123", "passw0rd", "admin", "admin123", "admin@123", "administrator",
    "changeme", "change_this_password", "welcome", "welcome1", "welcome123", "qwerty", "qwerty123",
    "letmein", "123456", "12345678", "123456789", "1234567890", "1q2w3e4r", "huawei", "huawei@123",
    "huawei123", "juniper", "juniper123", "arista", "arista123", "cisco", "cisco123", "network",
    "network123", "secret", "root", "toor", "test", "test123", "india123", "kerala123",
}


def password_problem(password: str, username: str | None = None) -> str | None:
    """Return why a password is not acceptable, or None if it is fine."""
    p = password or ""
    if len(p) < PASSWORD_MIN:
        return f"Password must be at least {PASSWORD_MIN} characters"
    if len(p.encode()) > 72:
        return "Password must be at most 72 bytes"
    low = p.lower()
    if low in _COMMON or re.sub(r"[\d!@#$%^&*._-]+$", "", low) in _COMMON:
        return "This password is too common"
    if username and len(username) >= 3 and username.lower() in low:
        return "Password must not contain the username"
    classes = sum(bool(re.search(rx, p)) for rx in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]"))
    if classes < 3:
        return "Use at least three of: lower-case letters, upper-case letters, digits, symbols"
    if len(set(p)) < 5:
        return "Password repeats too few different characters"
    return None


# ───────────────────────── login throttling ─────────────────────────

class LoginLimiter:
    """In-memory failed sign-in counter per username and per source address.

    A username is locked after LOGIN_MAX_ATTEMPTS failures within the lockout
    window, whether or not it exists (so the lock does not reveal valid names).
    One source address is locked after 4x that many failures across usernames.
    """

    def __init__(self):
        self._fails: dict[str, deque] = {}
        self._lock = threading.Lock()

    def _window(self) -> float:
        return max(1, settings.login_lockout_minutes) * 60

    def _recent(self, key: str, now: float) -> deque:
        q = self._fails.setdefault(key, deque())
        while q and now - q[0] > self._window():
            q.popleft()
        return q

    def locked_for(self, username: str, ip: str | None) -> int:
        """Seconds until sign-in is allowed again (0 = allowed)."""
        now = time.monotonic()
        limit = max(1, settings.login_max_attempts)
        with self._lock:
            waits = []
            for key, lim in ((f"u:{username.lower()}", limit), (f"ip:{ip}", limit * 4)):
                if key.endswith(":None"):
                    continue
                q = self._recent(key, now)
                if len(q) >= lim:
                    waits.append(int(self._window() - (now - q[-lim])) + 1)
            return max(waits) if waits else 0

    def failed(self, username: str, ip: str | None) -> bool:
        """Record a failure. Returns True if this failure caused a lock."""
        now = time.monotonic()
        with self._lock:
            for key in (f"u:{username.lower()}", f"ip:{ip}"):
                self._recent(key, now).append(now)
            return len(self._fails[f"u:{username.lower()}"]) == max(1, settings.login_max_attempts)

    def success(self, username: str):
        with self._lock:
            self._fails.pop(f"u:{username.lower()}", None)

    def clear(self):
        with self._lock:
            self._fails.clear()


login_limiter = LoginLimiter()
