import socket
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bridge_container.paperless import Paperless, target_address, validate_url
from bridge_container.settings import Settings


class PaperlessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.settings = Settings(Path(self.directory.name))
        self.bridge = MagicMock()
        self.bridge.credentials.return_value = {
            "username": "mail@example.test",
            "password": "bridge-secret",
        }
        self.paperless = Paperless(self.settings, self.bridge)
        self.payload = self.settings.value | {
            "paperless_url": "https://paperless.test",
            "token": "test-token-with-enough-length",
            "proton_id": "one",
            "address": "mail@example.test",
        }

    def test_rejects_unsafe_urls(self):
        for value in (
            "http://paperless.test",
            "ftp://paperless.test",
            "https://user:pass@host",
            "https://host?token=secret",
            "https://host#fragment",
            "https://host:0",
            "https://[fe80::1%eth0]",
            "https://host\\bad",
            "https://",
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_url(value, False)
        self.assertEqual(
            validate_url("http://192.168.1.50:8000/", True), "http://192.168.1.50:8000"
        )
        self.assertEqual(
            validate_url("https://paperless.test/archive/", False), "https://paperless.test/archive"
        )

    @patch("bridge_container.paperless.socket.getaddrinfo")
    def test_rejects_metadata_loopback_and_mixed_dns(self, resolve):
        for address in (
            "127.0.0.1",
            "::1",
            "169.254.169.254",
            "0.0.0.0",
            "224.0.0.1",
            "240.1.1.1",
            "100.100.100.200",
            "::ffff:127.0.0.1",
        ):
            resolve.return_value = [(socket.AF_INET, 1, 6, "", (address, 443))]
            with self.subTest(address=address), self.assertRaises(ValueError):
                target_address("paperless.test", 443)
        resolve.return_value = []
        with self.assertRaises(ValueError):
            target_address("empty.test", 443)
        resolve.return_value = [(socket.AF_INET, 1, 6, "", ("192.168.1.50", 443))]
        self.assertEqual(target_address("paperless.test", 443), "192.168.1.50")
        resolve.return_value += [(socket.AF_INET, 1, 6, "", ("127.0.0.1", 443))]
        with self.assertRaises(ValueError):
            target_address("mixed.test", 443)

    @patch("bridge_container.paperless.subprocess.run")
    def test_saves_encrypted_token_and_settings(self, run):
        self.paperless.save(self.payload)
        self.assertNotIn("token", self.settings.path.read_text())
        self.assertEqual(run.call_args.kwargs["input"], self.payload["token"] + "\n")
        self.assertNotIn(self.payload["token"], str(run.call_args.args))
        run.return_value.stdout = "stored-token\n"
        self.payload["token"] = ""
        self.paperless.save(self.payload)
        self.assertEqual(self.paperless.token(), "stored-token")
        self.payload["paperless_url"] = "https://another.test"
        with self.assertRaisesRegex(ValueError, "new Paperless"):
            self.paperless.save(self.payload)

    @patch("bridge_container.paperless.subprocess.run")
    def test_settings_validation(self, _run):
        for field, value in (
            ("allow_http", "yes"),
            ("paperless_ca", 123),
            ("paperless_ca", "x" * 32769),
            ("paperless_ca", "-----BEGIN PRIVATE KEY-----"),
            ("maximum_age", 0),
            ("maximum_age", True),
            ("maximum_age", 36501),
            ("token", "not a token"),
            ("token", 123),
            ("destination", "Folders/Paperless"),
            ("folder", ""),
        ):
            with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                self.paperless.save(self.payload | {field: value})
        with self.assertRaises(ssl.SSLError):
            self.paperless.save(self.payload | {"paperless_ca": "invalid pem"})
        with patch("bridge_container.paperless.ssl.create_default_context") as context:
            self.paperless.save(self.payload | {"paperless_ca": "valid-pem"})
            context.assert_called_with(cadata="valid-pem")

    @patch("bridge_container.paperless.target_address", return_value="192.168.1.50")
    @patch("bridge_container.paperless.socket.create_connection")
    @patch("bridge_container.paperless.http.client.HTTPConnection")
    @patch("bridge_container.paperless.ssl.create_default_context")
    def test_https_connection_is_pinned_verified_and_no_redirects(
        self, tls, http, connect, resolve
    ):
        self.settings.value.update(self.payload)
        self.settings.value.pop("token")
        response = http.return_value.getresponse.return_value
        response.status = 200
        response.read.return_value = b'{"success":true}'
        with patch.object(self.paperless, "token", return_value="opaque-token"):
            self.assertEqual(
                self.paperless.request("POST", "mail_accounts/test/", {"username": "name"}),
                {"success": True},
            )
            connect.assert_called_with(("192.168.1.50", 443), timeout=20)
            tls.return_value.wrap_socket.assert_called_with(
                connect.return_value, server_hostname="paperless.test"
            )
            self.assertIn("Token opaque-token", str(http.return_value.request.call_args))
            for code in (301, 400, 401, 403, 404, 500):
                response.status = code
                response.read.return_value = b"private upstream error and token"
                with self.assertRaises(ValueError) as error:
                    self.paperless.request("GET", "mail_accounts/")
                self.assertNotIn("private upstream", str(error.exception))
            response.status = 200
            response.read.return_value = b"x" * 1048577
            with self.assertRaisesRegex(ValueError, "too large"):
                self.paperless.request("GET", "mail_accounts/")
            self.settings.value.update(paperless_url="http://paperless.test:8000", allow_http=True)
            response.read.return_value = b"{}"
            self.paperless.request("GET", "mail_accounts/")
            resolve.assert_called_with("paperless.test", 8000)
        self.assertTrue(http.return_value.close.called)
        self.assertTrue(connect.return_value.close.called)

    def test_test_and_apply_use_paperless_side_validation_and_resume_partial_setup(self):
        self.settings.value.update(self.payload)
        with patch.object(self.paperless, "request") as request:
            request.side_effect = [{"success": True}, {"id": 7}, ValueError("rule rejected")]
            with self.assertRaisesRegex(ValueError, "rule rejected"):
                self.paperless.apply()
            self.assertEqual(self.settings.value["account_id"], 7)
            self.assertIsNone(self.settings.value.get("rule_id"))
            self.assertEqual(request.call_args_list[0].args[:2], ("POST", "mail_accounts/test/"))
            account = request.call_args_list[1].args[2]
            self.assertEqual(account["imap_security"], 2)
            self.assertEqual(account["password"], "bridge-secret")
            request.side_effect = [{"success": True}, {}, {"id": 9}]
            self.paperless.apply()
            self.assertEqual(self.settings.value["rule_id"], 9)
            request.side_effect = [{"success": True}, {}, {}]
            self.paperless.apply()
            self.assertEqual(request.call_args.args[:2], ("PATCH", "mail_rules/9/"))
            self.assertTrue(request.call_args.args[2]["enabled"])
            request.side_effect = None
            for response in ({"success": False}, [], {}):
                request.return_value = response
                with self.assertRaises(ValueError):
                    self.paperless.test()
