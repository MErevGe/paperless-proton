import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bridge_container.web_app import WebApp
from bridge_container.web_server import run_web


class WebTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)
        (self.data / "tls").mkdir()
        (self.data / "tls/host").write_text("localhost")
        (self.data / "tls/ca.crt").write_text("PUBLIC CERTIFICATE")
        self.bridge = MagicMock()
        self.bridge.ready = True
        self.bridge.accounts.return_value = []
        self.bridge.status.return_value = {"step": "idle"}
        self.app = WebApp(self.data, self.bridge)
        self.cookie = ""
        self.csrf = ""
        self.password = "long-admin-test-password"

    def request(self, path, payload=None, **overrides):
        data = json.dumps(payload).encode() if payload is not None else b""
        environ = {
            "REQUEST_METHOD": "POST" if payload is not None else "GET",
            "PATH_INFO": path,
            "HTTP_HOST": "localhost:8443",
            "HTTP_ORIGIN": "https://localhost:8443",
            "CONTENT_TYPE": "application/json",
            "CONTENT_LENGTH": str(len(data)),
            "wsgi.input": io.BytesIO(data),
            "HTTP_COOKIE": self.cookie,
            "HTTP_X_CSRF_TOKEN": self.csrf,
        } | overrides
        start = MagicMock()
        body = b"".join(self.app(environ, start))
        status, headers = start.call_args.args
        headers = dict(headers)
        result = json.loads(body) if headers["Content-Type"] == "application/json" else body
        return int(status[:3]), result, headers

    def authenticate(self):
        code, result, headers = self.request(
            "/api/setup",
            {"setup_token": self.app.auth.token_path.read_text(), "password": self.password},
        )
        self.assertEqual(code, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.csrf = result["csrf"]

    def test_public_shell_and_authenticated_state_and_certificate(self):
        for path in ("/", "/app.js", "/style.css"):
            code, _, headers = self.request(path)
            self.assertEqual(code, 200)
            self.assertEqual(headers["Cache-Control"], "no-store")
            self.assertIn("frame-ancestors 'none'", headers["Content-Security-Policy"])
        self.assertFalse(self.request("/api/session")[1]["configured"])
        self.assertEqual(self.request("/api/state")[0], 401)
        self.assertEqual(self.request("/api/certificate")[0], 401)
        self.assertEqual(self.request("/api/paperless/apply", {})[0], 401)
        self.assertEqual(self.request("/api/proton/folders", {})[0], 401)
        self.authenticate()
        self.assertTrue(self.request("/api/session")[1]["authenticated"])
        self.assertEqual(self.request("/api/state")[1]["active_host"], "localhost")
        self.assertEqual(self.request("/api/certificate")[1], b"PUBLIC CERTIFICATE")
        self.bridge.ready = False
        self.assertEqual(self.request("/api/state")[1]["accounts"], [])
        self.assertEqual(self.request("/missing")[0], 404)
        self.assertEqual(self.request("/api/state", REQUEST_METHOD="DELETE")[0], 404)
        self.assertEqual(self.request("/api/unknown", {})[0], 400)

    def test_host_origin_csrf_and_content_type_guards(self):
        self.assertEqual(self.request("/api/setup", {}, HTTP_HOST="evil.test")[0], 403)
        for site in ("cross-site", "same-site"):
            self.assertEqual(self.request("/", HTTP_SEC_FETCH_SITE=site)[0], 403)
        for origin in ("https://evil.test", "null", ""):
            self.assertEqual(self.request("/api/login", {}, HTTP_ORIGIN=origin)[0], 403)
        self.authenticate()
        self.assertEqual(
            self.request("/api/connection", {"host": "safe.test"}, HTTP_X_CSRF_TOKEN="")[0], 403
        )
        self.assertEqual(self.request("/api/connection", {}, CONTENT_TYPE="text/plain")[0], 400)
        for size in ("0", "65537", "-1", "invalid"):
            self.assertEqual(self.request("/api/connection", {}, CONTENT_LENGTH=size)[0], 400)
        self.assertEqual(self.request("/api/connection", [], CONTENT_LENGTH="2")[0], 400)
        self.assertEqual(self.request("/api/connection", {"host": "bad\nhost"})[0], 400)
        self.assertEqual(self.request("/api/connection", {"host": "mail.test"})[0], 200)
        self.assertEqual(self.app.settings.value["host"], "mail.test")
        self.assertEqual(self.request("/api/paperless/test", {})[0], 400)

    def test_login_logout_and_password_rotation(self):
        self.authenticate()
        self.assertEqual(self.request("/api/logout", {})[0], 200)
        self.assertEqual(self.request("/api/state")[0], 401)
        code, result, headers = self.request("/api/login", {"password": self.password})
        self.assertEqual(code, 200)
        self.csrf = result["csrf"]
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]
        self.assertEqual(
            self.request("/api/password", {"current": "wrong", "password": "long-enough-new"})[0],
            403,
        )
        self.assertEqual(
            self.request(
                "/api/password", {"current": self.password, "password": "long-enough-new"}
            )[0],
            200,
        )
        self.assertEqual(self.request("/api/state")[0], 401)
        self.assertTrue(self.app.auth.check_password("long-enough-new"))

    def test_routes_bridge_and_paperless_actions(self):
        self.authenticate()
        self.bridge.credentials.return_value = {"username": "person", "password": "bridge-secret"}
        for path in (
            "proton/login",
            "proton/answer",
            "proton/cancel",
            "proton/logout",
            "proton/credentials",
        ):
            self.assertEqual(
                self.request(
                    "/api/" + path,
                    {
                        "username": "person",
                        "password": "secret",
                        "id": "one",
                        "address": "person@test",
                    },
                )[0],
                200,
            )
        self.bridge.login.assert_called_with("person", "secret")
        with patch("bridge_container.web_app.list_folders", return_value=["INBOX"]) as folders:
            self.assertEqual(
                self.request("/api/proton/folders", {"id": "one", "address": "mail@test"})[1],
                {"folders": ["INBOX"]},
            )
            folders.assert_called_once_with(self.data, self.bridge, "one", "mail@test")
            self.assertEqual(self.request("/api/proton/folders", {}, HTTP_X_CSRF_TOKEN="")[0], 403)
        with patch.object(self.app, "paperless") as paperless:
            for path in ("paperless/save", "paperless/test", "paperless/apply"):
                self.assertEqual(self.request("/api/" + path, {})[0], 200)
            paperless.test.assert_called_once()
            paperless.apply.assert_called_once()
        for error, status in (
            (RuntimeError("sensitive"), 503),
            (OSError("private"), 503),
            (KeyError("private"), 500),
        ):
            self.bridge.login.side_effect = error
            code, body, _ = self.request("/api/proton/login", {})
            self.assertEqual(code, status)
            self.assertNotIn("private", str(body))
            self.assertNotIn("sensitive", str(body))

    def test_web_server_closes_grpc_when_server_stops(self):
        with (
            patch("bridge_container.web_server.BridgeAPI") as bridge,
            patch("bridge_container.web_server.WebApp"),
            patch("bridge_container.web_server.threading.Thread") as thread,
            patch("bridge_container.web_server.serve") as serve,
        ):
            run_web(self.data)
            serve.assert_called_once()
            self.assertEqual(serve.call_args.kwargs["host"], "127.0.0.1")
            bridge.return_value.channel.close.assert_called_once()
            self.assertEqual(thread.return_value.join.call_count, 2)
            bridge.return_value.channel = None
            run_web(self.data)
