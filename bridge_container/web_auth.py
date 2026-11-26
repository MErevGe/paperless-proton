import hashlib
import hmac
import secrets
import threading
import time
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Any

from bridge_container.settings import load_json, save_json, text_field

COOKIE = "__Host-paperless-proton"
SESSION_SECONDS = 3600


class Auth:
    def __init__(self, data: Path):
        self.path = data / "web" / "admin.json"
        self.token_path = data / "web" / "setup-token"
        self.lock = threading.RLock()
        self.sessions: dict[str, tuple[float, str]] = {}
        self.attempts: list[float] = []
        if not self.path.exists():
            self.token_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            if not self.token_path.exists():
                self.token_path.write_text(secrets.token_urlsafe(32))
                self.token_path.chmod(0o600)

    @property
    def configured(self) -> bool:
        return self.path.exists()

    def limited(self) -> bool:
        now = time.monotonic()
        self.attempts = [attempt for attempt in self.attempts if now - attempt < 60]
        if len(self.attempts) >= 5:
            return True
        self.attempts.append(now)
        return False

    def set_password(self, password: Any) -> None:
        password = text_field(password, "admin password", 1024)
        if len(password) < 12:
            raise ValueError("Use at least 12 characters for the admin password")
        salt = secrets.token_bytes(32)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 600000)
        save_json(self.path, {"salt": salt.hex(), "hash": digest.hex()})
        self.sessions.clear()

    def check_password(self, password: Any) -> bool:
        password = text_field(password, "password", 1024)
        if not self.configured:
            return False
        saved = load_json(self.path)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(saved["salt"]), 600000
        )
        return hmac.compare_digest(digest.hex(), saved["hash"])

    def login(self, password: Any, setup_token: Any = None) -> tuple[str, str]:
        with self.lock:
            if self.limited():
                raise PermissionError("Too many attempts. Wait one minute and try again.")
            if setup_token is not None:
                token = text_field(setup_token, "setup token", 128)
                if self.configured or not hmac.compare_digest(
                    token.encode(), self.token_path.read_bytes()
                ):
                    raise PermissionError("Invalid setup token")
                self.set_password(password)
                self.token_path.unlink()
            elif not self.check_password(password):
                raise PermissionError("Invalid password")
            self.attempts.clear()
            session = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(32)
            now = time.monotonic()
            self.sessions = {key: val for key, val in self.sessions.items() if val[0] > now}
            if len(self.sessions) >= 16:
                self.sessions.pop(next(iter(self.sessions)))
            self.sessions[session] = (now + SESSION_SECONDS, csrf)
            return session, csrf

    def session(self, cookie: str) -> tuple[str, str]:
        parsed = SimpleCookie()
        try:
            parsed.load(cookie)
        except CookieError:
            return "", ""
        item = parsed.get(COOKIE)
        token = item.value if item else ""
        with self.lock:
            expires, csrf = self.sessions.get(token, (0, ""))
            if expires <= time.monotonic():
                self.sessions.pop(token, None)
                return "", ""
            return token, csrf

    def logout(self, token: str) -> None:
        with self.lock:
            self.sessions.pop(token, None)


def session_cookie(token: str) -> str:
    age = SESSION_SECONDS if token else 0
    return f"{COOKIE}={token}; Path=/; Secure; HttpOnly; SameSite=Strict; Max-Age={age}"
