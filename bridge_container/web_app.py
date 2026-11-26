import hmac
import json
import ssl
import subprocess
import threading
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import grpc

from bridge_container.bridge_api import BridgeAPI
from bridge_container.certificates import subject_alt_name
from bridge_container.mail_folders import list_folders
from bridge_container.paperless import Paperless
from bridge_container.settings import Settings, text_field, web_origin
from bridge_container.web_auth import Auth, session_cookie

STATIC = Path(__file__).parent / "static"
ASSETS = {
    "/icon.svg": ("icon.svg", "image/svg+xml"),
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class WebApp:
    def __init__(self, data: Path, bridge: BridgeAPI):
        self.data = data
        self.auth = Auth(data)
        self.settings = Settings(data)
        self.bridge = bridge
        self.paperless = Paperless(self.settings, bridge)
        self.origin = web_origin()
        self.lock = threading.RLock()

    def __call__(self, environ: dict[str, Any], start_response: Any) -> list[bytes]:
        headers = [
            ("Cache-Control", "no-store"),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Frame-Options", "DENY"),
            (
                "Content-Security-Policy",
                "default-src 'self'; script-src 'self'; "
                "style-src 'self'; img-src 'self'; connect-src 'self'; "
                "frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            ),
            ("Permissions-Policy", "camera=(), microphone=(), geolocation=()"),
        ]
        content_type = "application/json"
        try:
            if environ.get("HTTP_HOST") != urlsplit(self.origin).netloc:
                raise PermissionError("Open the configured WEB_ORIGIN address")
            path, method = environ.get("PATH_INFO", "/"), environ["REQUEST_METHOD"]
            if environ.get("HTTP_SEC_FETCH_SITE") in ("cross-site", "same-site"):
                raise PermissionError("Cross-origin requests are not allowed")
            if method == "GET" and path in ASSETS:
                filename, content_type = ASSETS[path]
                status, response = 200, (STATIC / filename).read_bytes()
            else:
                status, result, extra = self.route(environ)
                headers.extend(extra)
                if isinstance(result, bytes):
                    content_type = "application/x-pem-file"
                    response = result
                else:
                    response = json.dumps(result).encode()
        except PermissionError as error:
            status, response = 403, json.dumps({"error": str(error)}).encode()
        except (ValueError, ssl.SSLError) as error:
            status, response = 400, json.dumps({"error": str(error)}).encode()
        except OSError, RuntimeError, subprocess.SubprocessError, grpc.RpcError:
            status, response = (
                503,
                json.dumps(
                    {
                        "error": "Service unavailable. Check connectivity, certificates "
                        "and container status."
                    }
                ).encode(),
            )
        except Exception:
            status, response = 500, b'{"error":"The request could not be completed."}'
        headers.extend((("Content-Type", content_type), ("Content-Length", str(len(response)))))
        start_response(f"{status} {HTTPStatus(status).phrase}", headers)
        return [response]

    def body(self, environ: dict[str, Any]) -> dict[str, Any]:
        if environ.get("CONTENT_TYPE", "").split(";")[0] != "application/json":
            raise ValueError("Use JSON requests")
        size = int(environ.get("CONTENT_LENGTH") or "0")
        if not 0 < size <= 65536:
            raise ValueError("Invalid request size")
        value = json.loads(environ["wsgi.input"].read(size))
        if not isinstance(value, dict):
            raise ValueError("Use a JSON object")
        return value

    def route(self, environ: dict[str, Any]) -> tuple[int, Any, list[tuple[str, str]]]:
        path, method = environ["PATH_INFO"], environ["REQUEST_METHOD"]
        session, csrf = self.auth.session(environ.get("HTTP_COOKIE", ""))
        if method == "GET" and path == "/api/session":
            return (
                200,
                {"configured": self.auth.configured, "authenticated": bool(session), "csrf": csrf},
                [],
            )
        if method == "POST":
            if environ.get("HTTP_ORIGIN") != self.origin:
                raise PermissionError("Invalid request origin")
            payload = self.body(environ)
            if path in ("/api/login", "/api/setup"):
                token = payload.get("setup_token", "") if path == "/api/setup" else None
                session, csrf = self.auth.login(payload.get("password"), token)
                return 200, {"csrf": csrf}, [("Set-Cookie", session_cookie(session))]
        if not session:
            return 401, {"error": "Sign in to continue"}, []
        if method == "GET":
            if path == "/api/state":
                with self.lock:
                    return 200, self.state(), []
            if path == "/api/certificate":
                return (
                    200,
                    (self.data / "tls/ca.crt").read_bytes(),
                    [("Content-Disposition", 'attachment; filename="proton-bridge-ca.crt"')],
                )
        if method != "POST":
            return 404, {"error": "Not found"}, []
        if not hmac.compare_digest(environ.get("HTTP_X_CSRF_TOKEN", ""), csrf):
            raise PermissionError("Invalid session token. Reload the page.")
        if path == "/api/logout":
            self.auth.logout(session)
            return 200, {}, [("Set-Cookie", session_cookie(""))]
        if path == "/api/password":
            with self.auth.lock:
                if self.auth.limited() or not self.auth.check_password(payload.get("current")):
                    raise PermissionError("Invalid current password or too many attempts")
                self.auth.set_password(payload.get("password"))
            return 200, {}, [("Set-Cookie", session_cookie(""))]
        with self.lock:
            return 200, self.action(path, payload), []

    def state(self) -> dict[str, Any]:
        return {
            "ready": self.bridge.ready,
            "accounts": self.bridge.accounts() if self.bridge.ready else [],
            "login": self.bridge.status(),
            "settings": self.settings.value,
            "paperless_token_saved": self.paperless.token_saved(),
            "active_host": (self.data / "tls/host").read_text().strip(),
        }

    def action(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        if path == "/api/proton/login":
            self.bridge.login(payload.get("username"), payload.get("password"))
        elif path == "/api/proton/answer":
            self.bridge.answer(payload.get("password"))
        elif path == "/api/proton/cancel":
            self.bridge.abort()
        elif path == "/api/proton/logout":
            self.bridge.logout(payload.get("id"))
        elif path == "/api/proton/credentials":
            return self.bridge.credentials(payload.get("id"), payload.get("address"))
        elif path == "/api/proton/folders":
            return {
                "folders": list_folders(
                    self.data, self.bridge, payload.get("id"), payload.get("address")
                )
            }
        elif path == "/api/connection":
            host = text_field(payload.get("host"), "Bridge hostname", 253)
            subject_alt_name(host)
            self.settings.save({"host": host})
            return {
                "message": "Saved. Restart the container to apply the new hostname and certificate."
            }
        elif path == "/api/paperless/save":
            self.paperless.save(payload)
        elif path == "/api/paperless/test":
            self.check_hostname()
            self.paperless.test()
            return {"message": "Paperless connected to Bridge successfully using SSL/TLS."}
        elif path == "/api/paperless/apply":
            self.check_hostname()
            self.paperless.apply()
            return {"message": "Email account and import rule saved in Paperless."}
        else:
            raise ValueError("Unknown action")
        return {"message": "Saved"}

    def check_hostname(self) -> None:
        if (self.data / "tls/host").read_text().strip() != self.settings.value["host"]:
            raise ValueError("Restart the container to apply the saved Bridge hostname first")
