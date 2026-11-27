import os
import shlex
import ssl
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class WorkflowDownloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.directory = Path(cls.temporary.name)
        cls.certificate = cls.directory / "certificate.pem"
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
                str(cls.certificate),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        cls.requests = []

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def handle(self):
                try:
                    super().handle()
                except BrokenPipeError:
                    pass

            def do_GET(self):
                scheme = "https" if self.server is cls.https else "http"
                cls.requests.append((scheme, self.path))
                redirects = {
                    "/redirect-http": cls.http_url + "/payload",
                    "/redirect-https": cls.https_url + "/payload",
                }
                if self.path in redirects:
                    self.send_response(302)
                    self.send_header("Location", redirects[self.path])
                    self.end_headers()
                else:
                    payload = b"test-tool-archive"
                    self.send_response(200)
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)

        cls.http = HTTPServer(("127.0.0.1", 0), Handler)
        cls.addClassCleanup(cls.http.server_close)
        cls.https = HTTPServer(("127.0.0.1", 0), Handler)
        cls.addClassCleanup(cls.https.server_close)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cls.certificate, key)
        cls.https.socket = context.wrap_socket(cls.https.socket, server_side=True)
        cls.http_url = f"http://localhost:{cls.http.server_port}"
        cls.https_url = f"https://localhost:{cls.https.server_port}"
        for server in (cls.http, cls.https):
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            cls.addClassCleanup(thread.join, 5)
            cls.addClassCleanup(server.shutdown)
        workflow = Path(".github/workflows/container.yml").read_text()
        cls.commands = [
            shlex.split(line.strip())
            for line in workflow.splitlines()
            if line.strip().startswith("curl ")
        ]
        if not cls.commands:
            raise AssertionError("No workflow downloads found")

    def download(self, command, url, trusted=True):
        self.requests.clear()
        output = self.directory / "download"
        output.unlink(missing_ok=True)
        arguments = command.copy()
        source = next(index for index, value in enumerate(arguments) if "://" in value)
        arguments[source] = url
        arguments[arguments.index("-o") + 1] = str(output)
        arguments[1:1] = ["--disable", "--noproxy", "*", "--max-time", "5"]
        if trusted:
            arguments += ["--cacert", str(self.certificate)]
        environment = {
            key: value
            for key, value in os.environ.items()
            if key not in {"CURL_CA_BUNDLE", "SSL_CERT_FILE", "SSL_CERT_DIR"}
        }
        result = subprocess.run(
            arguments, capture_output=True, text=True, timeout=10, env=environment
        )
        return result, output

    def test_https_redirect_downloads_succeed(self):
        for command in self.commands:
            with self.subTest(command=command):
                result, output = self.download(command, self.https_url + "/redirect-https")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(output.read_bytes(), b"test-tool-archive")
                self.assertEqual(
                    self.requests, [("https", "/redirect-https"), ("https", "/payload")]
                )

    def test_http_downgrades_are_rejected_before_contacting_destination(self):
        for command in self.commands:
            with self.subTest(command=command):
                result, output = self.download(command, self.https_url + "/redirect-http")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(self.requests, [("https", "/redirect-http")])
                self.assertFalse(output.exists())

    def test_plain_http_downloads_are_rejected_before_contacting_destination(self):
        for command in self.commands:
            with self.subTest(command=command):
                result, output = self.download(command, self.http_url + "/payload")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertEqual(self.requests, [])
                self.assertFalse(output.exists())

    def test_untrusted_certificates_remain_rejected(self):
        for command in self.commands:
            with self.subTest(command=command):
                result, output = self.download(command, self.https_url + "/payload", trusted=False)
                self.assertEqual(result.returncode, 60, result.stderr)
                self.assertEqual(self.requests, [])
                self.assertFalse(output.exists())
