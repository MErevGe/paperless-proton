import contextlib
import http.client
import io
import json
import os
import ssl
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import parse_qsl, urlsplit

from sonar_fixtures import PROJECT, REPOSITORY, REVISION, ServiceFixture

from ci.sonar_api import Api, SyncError
from ci.sonar_sync import main


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.api = Api("sonarcloud.io", "test-credential")
        self.connection = Mock()
        self.response = self.connection.getresponse.return_value
        self.response.status = 200
        self.response.read.return_value = b'{"result": "ok"}'
        self.factory = patch("ci.sonar_api.HTTPSConnection", return_value=self.connection)
        self.connection_factory = self.factory.start()
        self.addCleanup(self.factory.stop)

    def test_get_encodes_queries_and_redacts_response_credentials(self):
        self.response.read.return_value = b'{"result": "test-credential"}'
        result = self.api.request(
            "GET", "/api/issues/search", query={"project": "example &/ project"}
        )
        self.assertEqual(result, {"result": "[redacted]"})
        args, kwargs = self.connection.request.call_args
        self.assertEqual(args, ("GET", "/api/issues/search?project=example+%26%2F+project"))
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-credential")
        self.assertNotIn("X-GitHub-Api-Version", kwargs["headers"])
        self.connection.close.assert_called_once()
        context = self.connection_factory.call_args.kwargs["context"]
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)

    def test_github_writes_json_with_a_fixed_api_version(self):
        Api("api.github.com", "test-credential").request(
            "POST", "/repos/example/project/issues", body={"title": "example"}
        )
        kwargs = self.connection.request.call_args.kwargs
        self.assertEqual(json.loads(kwargs["body"]), {"title": "example"})
        self.assertEqual(kwargs["headers"]["Content-Type"], "application/json")
        self.assertEqual(kwargs["headers"]["X-GitHub-Api-Version"], "2022-11-28")

    def test_redirects_permissions_rate_limits_and_server_errors_are_not_retried(self):
        for status in (301, 302, 307, 308, 400, 401, 403, 404, 429, 500, 503):
            with self.subTest(status=status):
                self.connection.reset_mock()
                self.response.status = status
                self.response.read.return_value = b"private error containing test-credential"
                with self.assertRaisesRegex(SyncError, f"HTTP {status}") as raised:
                    self.api.request("POST", "/api/test", body={})
                self.assertNotIn("test-credential", str(raised.exception))
                self.connection.request.assert_called_once()
                self.connection.close.assert_called_once()

    def test_malformed_large_and_failed_responses_do_not_expose_raw_data(self):
        for raw in (b"not JSON test-credential", b"\xff"):
            self.response.read.return_value = raw
            with self.assertRaisesRegex(SyncError, "valid HTTPS response") as raised:
                self.api.request("GET", "/api/test")
            self.assertNotIn("test-credential", str(raised.exception))
        with patch("ci.sonar_api.MAX_RESPONSE_BYTES", 8):
            self.response.read.return_value = b"123456789"
            with self.assertRaisesRegex(SyncError, "size limit"):
                self.api.request("GET", "/api/test")
        for error in (OSError("private message"), http.client.HTTPException("private message")):
            self.connection.request.side_effect = error
            with self.assertRaises(SyncError) as raised:
                self.api.request("GET", "/api/test")
            self.assertNotIn("private message", str(raised.exception))

    def test_hosts_credentials_and_request_shape_are_restricted(self):
        for host, token in (
            ("evil.invalid", "token"),
            ("http://sonarcloud.io", "token"),
            ("sonarcloud.io", ""),
            ("sonarcloud.io", "token\nInjected"),
        ):
            with self.subTest(host=host), self.assertRaises(SyncError):
                Api(host, token)
        for method, path in (("DELETE", "/api/test"), ("GET", "https://evil.invalid")):
            with self.assertRaises(SyncError):
                self.api.request(method, path)
        self.connection.request.assert_not_called()


class HttpsIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.cert = cls.directory / "certificate.pem"
        key = cls.directory / "private.key"
        subprocess.run(
            [
                "openssl",
                "req",
                "-x509",
                "-newkey",
                "rsa:2048",
                "-nodes",
                "-days",
                "1",
                "-subj",
                "/CN=localhost",
                "-addext",
                "subjectAltName=DNS:localhost",
                "-keyout",
                str(key),
                "-out",
                str(cls.cert),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        cls.fixture = ServiceFixture()
        cls.redirect = False
        cls.requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def do_GET(self):
                self.respond()

            def do_POST(self):
                self.respond()

            def do_PATCH(self):
                self.respond()

            def respond(self):
                cls.requests.append((self.command, self.path))
                if cls.redirect:
                    self.send_response(302)
                    self.send_header("Location", "http://127.0.0.1:1/collect-token")
                    self.end_headers()
                    return
                url = urlsplit(self.path)
                token = "sonar-test-token" if url.path.startswith("/api/") else "github-test-token"
                if self.headers.get("Authorization") != "Bearer " + token:
                    self.send_error(401)
                    return
                size = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(size)) if size else None
                result = cls.fixture.request(
                    self.command, url.path, query=dict(parse_qsl(url.query)), body=body
                )
                payload = json.dumps(result).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        cls.server = HTTPServer(("localhost", 0), Handler)
        cls.addClassCleanup(cls.server.server_close)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.cert, key)
        cls.server.socket = context.wrap_socket(cls.server.socket, server_side=True)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.addClassCleanup(cls.thread.join, 5)
        cls.addClassCleanup(cls.server.shutdown)

    def setUp(self):
        type(self).fixture = ServiceFixture()
        type(self).redirect = False
        self.requests.clear()
        original = http.client.HTTPSConnection
        self.destinations = []

        def local_connection(host, **kwargs):
            self.destinations.append(host)
            return original("localhost", self.server.server_port, **kwargs)

        self.factory = patch("ci.sonar_api.HTTPSConnection", side_effect=local_connection)
        self.factory.start()
        self.addCleanup(self.factory.stop)

    def test_full_preview_apply_update_close_and_retry_over_verified_tls(self):
        self.fixture.issues[0]["issueStatus"] = "FALSE_POSITIVE"
        context = ssl.create_default_context(cafile=str(self.cert))
        env = {
            "SONAR_TOKEN": "sonar-test-token",
            "GH_TOKEN": "github-test-token",
            "SONAR_PROJECT_KEY": PROJECT,
            "GITHUB_REPOSITORY": REPOSITORY,
            "SONAR_REVISION": REVISION,
            "GITHUB_ACTIONS": "true",
            "GITHUB_REF": "refs/heads/main",
            "RUNNER_TEMP": str(self.directory),
        }
        plan = self.directory / "sonar-sync" / "plan.json"
        with (
            patch("ci.sonar_api.ssl.create_default_context", return_value=context),
            patch.dict(os.environ, env, clear=True),
            patch("ci.sonar_sync.time.sleep"),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            with patch("sys.argv", ["sonar-sync", "--plan", str(plan)]):
                self.assertEqual(main(), 0)
            self.assertEqual(self.fixture.writes, [])
            self.assertEqual(len(json.loads(plan.read_text())["changes"]), 2)
            with patch("sys.argv", ["sonar-sync", "--plan", str(plan), "--apply"]):
                self.assertEqual(main(), 0)
                self.assertEqual(len(self.fixture.tickets), 2)
                tracked = self.fixture.tickets[1]
                self.assertEqual(
                    (tracked["state"], tracked["state_reason"]), ("closed", "not_planned")
                )
                self.assertIn({"name": "false-positive"}, tracked["labels"])
                self.fixture.issues[0]["issueStatus"] = "OPEN"
                self.assertEqual(main(), 0)
                self.assertEqual(tracked["state"], "open")
                self.assertNotIn({"name": "false-positive"}, tracked["labels"])
                self.fixture.issues[0]["issueStatus"] = "FIXED"
                self.fixture.hotspots[0]["message"] = "Updated review request."
                self.assertEqual(main(), 0)
                self.assertEqual(self.fixture.tickets[1]["state"], "closed")
                self.assertEqual(tracked["state_reason"], "completed")
                self.assertEqual(main(), 0)
            self.assertEqual(json.loads(plan.read_text())["changes"], [])
        self.assertEqual(set(self.destinations), {"api.github.com", "sonarcloud.io"})
        self.assertIn("PATCH", [method for method, _ in self.requests])

    def test_untrusted_certificate_is_rejected_before_credentials_are_sent(self):
        with self.assertRaisesRegex(SyncError, "valid HTTPS response"):
            Api("sonarcloud.io", "sonar-test-token").request("GET", "/api/issues/search")
        self.assertEqual(self.requests, [])

    def test_https_redirect_is_not_followed(self):
        type(self).redirect = True
        context = ssl.create_default_context(cafile=str(self.cert))
        with patch("ci.sonar_api.ssl.create_default_context", return_value=context):
            with self.assertRaisesRegex(SyncError, "HTTP 302"):
                Api("sonarcloud.io", "sonar-test-token").request("GET", "/api/issues/search")
        self.assertEqual(self.requests, [("GET", "/api/issues/search")])

    def test_module_entrypoint_fails_closed_without_credentials(self):
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"SONAR_TOKEN", "GH_TOKEN"}
        }
        result = subprocess.run(
            [sys.executable, "-m", "ci.sonar_sync", "--plan", str(self.directory / "unused.json")],
            env=environment,
            text=True,
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn("token is required", result.stderr)
        self.assertFalse((self.directory / "unused.json").exists())
