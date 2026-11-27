import os
import socket
import ssl
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bridge_container.import_profiles import ImportProfiles
from bridge_container.paperless import Paperless, target_address, validate_url
from bridge_container.paperless_errors import PaperlessError
from bridge_container.settings import Settings


class PaperlessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.global_settings = Settings(Path(self.directory.name))
        self.settings = ImportProfiles(self.global_settings).get("one")
        self.user = {"id": 3, "username": "archive-user", "is_superuser": False}
        self.identity = {"user": self.user}
        self.global_settings.save({"host": "bridge.test"})
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
            "::ffff:100.100.100.200",
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

    @patch("bridge_container.paperless.socket.getaddrinfo")
    def test_rejects_metadata_in_mixed_dns_without_blocking_vpn_addresses(self, resolve):
        vpn_address = (socket.AF_INET, 1, 6, "", ("100.100.100.199", 443))
        resolve.return_value = [vpn_address]
        self.assertEqual(target_address("vpn.test", 443), "100.100.100.199")
        for metadata_address in ("100.100.100.200", "::ffff:100.100.100.200"):
            metadata = (socket.AF_INET, 1, 6, "", (metadata_address, 443))
            for addresses in ([vpn_address, metadata], [metadata, vpn_address]):
                with self.subTest(addresses=addresses):
                    resolve.return_value = addresses
                    with self.assertRaisesRegex(ValueError, "LAN, VPN or public"):
                        target_address("mixed.test", 443)

    @patch("bridge_container.paperless.subprocess.run")
    def test_saves_encrypted_token_and_settings(self, run):
        self.paperless.save(self.payload)
        self.assertNotIn("token", self.global_settings.path.read_text())
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
            ("maximum_age", -1),
            ("maximum_age", True),
            ("maximum_age", 32768),
            ("maximum_age", 36500),
            ("maximum_age", 36501),
            ("maximum_age", 90.5),
            ("maximum_age", "90"),
            ("maximum_age", None),
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

    def test_token_status_reports_storage_without_reading_secret(self):
        with patch.dict(os.environ, {"PASSWORD_STORE_DIR": self.directory.name}):
            self.assertFalse(self.paperless.token_saved())
            token = self.settings.token_path
            token.parent.mkdir(parents=True)
            token.write_bytes(b"encrypted-content")
            self.assertTrue(self.paperless.token_saved())

    @patch("bridge_container.paperless.target_address", return_value="192.168.1.50")
    @patch("bridge_container.paperless.socket.create_connection")
    @patch("bridge_container.paperless.http.client.HTTPConnection")
    @patch("bridge_container.paperless.ssl.create_default_context")
    def test_https_connection_is_pinned_verified_and_no_redirects(
        self, tls, http, connect, resolve
    ):
        self.settings.save(self.payload)
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
            for code in (301, 307, 400, 401, 403, 404, 405, 429, 500, 502, 503):
                response.status = code
                response.read.return_value = b"private upstream error and token"
                with self.assertRaises(ValueError) as error:
                    self.paperless.request("GET", "mail_accounts/")
                self.assertNotIn("private upstream", str(error.exception))
                self.assertIn(f"HTTP {code}", str(error.exception))
            response.status = 200
            response.read.return_value = b"<html>private error</html>"
            with self.assertRaisesRegex(ValueError, "invalid API response"):
                self.paperless.request("GET", "mail_accounts/")
            response.status = 200
            response.read.return_value = b"x" * 1048577
            with self.assertRaisesRegex(ValueError, "too large"):
                self.paperless.request("GET", "mail_accounts/")
            self.settings.save({"paperless_url": "http://paperless.test:8000", "allow_http": True})
            response.read.return_value = b"{}"
            self.paperless.request("GET", "mail_accounts/")
            resolve.assert_called_with("paperless.test", 8000)
        self.assertTrue(http.return_value.close.called)
        self.assertTrue(connect.return_value.close.called)

    def test_apply_binds_owner_and_resumes_partial_setup(self):
        self.settings.save(self.payload)
        with patch.object(self.paperless, "request") as request:
            request.side_effect = [
                self.identity,
                {},
                {"success": True},
                {"id": 7},
                ValueError("rule rejected"),
            ]
            with self.assertRaisesRegex(ValueError, "rule rejected"):
                self.paperless.apply()
            self.assertEqual(self.settings.value["account_id"], 7)
            self.assertEqual(self.settings.value["linked_user_id"], 3)
            self.assertFalse(self.settings.value["applied"])
            self.assertIsNone(self.settings.value["rule_id"])
            account = request.call_args_list[-2].args[2]
            self.assertEqual(account["imap_security"], 2)
            self.assertEqual(account["password"], "bridge-secret")
            self.assertEqual(account["owner"], 3)
            request.side_effect = [
                self.identity,
                {},
                {"id": 7, "owner": 3},
                {"success": True},
                {},
                {"id": 9},
            ]
            self.paperless.apply()
            self.assertEqual(self.settings.value["rule_id"], 9)
            self.assertTrue(self.settings.value["applied"])
            request.side_effect = [
                self.identity,
                {},
                {"id": 7, "owner": 3},
                {"id": 9, "owner": 3, "account": 7},
                {"success": True},
                {},
                {},
            ]
            self.paperless.apply()
            self.assertEqual(request.call_args.args[:2], ("PATCH", "mail_rules/9/"))
            rule = request.call_args.args[2]
            self.assertTrue(rule["enabled"])
            self.assertEqual(rule["owner"], 3)
            self.assertTrue(rule["assign_owner_from_rule"])
            self.assertEqual(rule["folder"], self.payload["folder"])
            self.assertEqual(rule["action_parameter"], self.payload["destination"])
            self.assertEqual(self.settings.value["paperless_user"], self.user)
            self.assertEqual(request.call_args_list[-3].args[2]["id"], 7)

    def test_invalid_api_access_stops_before_reading_bridge_password(self):
        with patch.object(
            self.paperless, "request", side_effect=ValueError("Invalid API token")
        ) as request:
            with self.assertRaisesRegex(ValueError, "Invalid API token"):
                self.paperless.test()
            request.assert_called_once_with("GET", "ui_settings/")
            self.bridge.credentials.assert_not_called()

    def test_requires_valid_identity_and_refuses_a_different_linked_user(self):
        for response in (
            [],
            {},
            {"user": None},
            {"user": {"id": True}},
            {"user": {"id": 0}},
            {"user": {"id": "3"}},
            {"user": {"id": 3, "username": ""}},
        ):
            with (
                self.subTest(response=response),
                patch.object(self.paperless, "request", return_value=response),
            ):
                with self.assertRaises(ValueError):
                    self.paperless.test()
        self.settings.save({"linked_user_id": 8, "account_id": 7, "rule_id": 9})
        with patch.object(self.paperless, "request", return_value=self.identity) as request:
            with self.assertRaisesRegex(ValueError, "another Paperless user"):
                self.paperless.apply()
            self.assertEqual(request.call_count, 1)
        self.bridge.credentials.assert_not_called()
        self.assertEqual(self.settings.value["rule_id"], 9)

    def test_refuses_wrong_or_missing_owner_and_rule_account(self):
        self.settings.save({"account_id": 7, "rule_id": 9})
        for key, resource in (("account_id", "mail_accounts"), ("rule_id", "mail_rules")):
            for response in ([], {}, {"owner": None}, {"owner": 8}):
                with (
                    self.subTest(key=key, response=response),
                    patch.object(self.paperless, "request", return_value=response),
                ):
                    with self.assertRaisesRegex(ValueError, "another user or has no owner"):
                        self.paperless.existing_id(
                            resource, key, self.user | {"is_superuser": True}
                        )
        with patch.object(self.paperless, "request", return_value={"owner": 3, "account": 8}):
            with self.assertRaisesRegex(ValueError, "another email account"):
                self.paperless.existing_id("mail_rules", "rule_id", self.user)
        self.assertEqual(self.settings.value["rule_id"], 9)

    def test_loopback_bridge_and_failed_imap_validation(self):
        with patch.object(self.paperless, "request") as request:
            for host in (
                "localhost",
                "LOCALHOST.",
                "mail.localhost",
                "127.0.0.1",
                "::1",
                "0.0.0.0",
            ):
                request.reset_mock()
                request.side_effect = [self.identity, {}]
                self.global_settings.save({"host": host})
                with (
                    self.subTest(host=host),
                    self.assertRaisesRegex(ValueError, "API access succeeded"),
                ):
                    self.paperless.test()
                self.assertEqual(request.call_count, 2)
            self.global_settings.save({"host": "192.168.1.20"})
            request.side_effect = [self.identity, {}, {"success": True}]
            self.assertEqual(self.paperless.test(), self.user)
            for response in ({"success": False}, [], {}):
                request.side_effect = [self.identity, {}, response]
                with self.assertRaisesRegex(ValueError, "could not connect"):
                    self.paperless.test()

    @patch("bridge_container.paperless.subprocess.run")
    def test_maximum_age_boundaries_are_saved_without_conversion(self, _run):
        for age in (0, 1, 90, 32767):
            with self.subTest(age=age):
                self.paperless.save(self.payload | {"maximum_age": age})
                self.assertEqual(self.settings.value["maximum_age"], age)

    def test_invalid_saved_age_stops_apply_before_any_paperless_request(self):
        self.settings.save({"maximum_age": 36500})
        with patch.object(self.paperless, "request") as request:
            with self.assertRaisesRegex(ValueError, "0 to 32767"):
                self.paperless.apply()
            request.assert_not_called()

    def test_superuser_recovers_deleted_account_without_creating_during_test(self):
        self.settings.save(
            self.payload | {"account_id": 7, "rule_id": 9, "applied": True, "linked_user_id": 3}
        )
        identity = {"user": self.user | {"is_superuser": True}}
        with patch.object(self.paperless, "request") as request:
            request.side_effect = [
                identity,
                {},
                PaperlessError(404, "mail_accounts/7/", b""),
                {"success": True},
            ]
            self.paperless.test()
            self.assertIsNone(self.settings.value["account_id"])
            self.assertIsNone(self.settings.value["rule_id"])
            self.assertFalse(self.settings.value["applied"])
            self.assertEqual(self.settings.value["linked_user_id"], 3)
            self.assertEqual(request.call_args.args[:2], ("POST", "mail_accounts/test/"))
            self.assertNotIn("id", request.call_args.args[2])
            self.assertEqual(request.call_count, 4)
            request.side_effect = [identity, {}, {"success": True}, {"id": 8}, {"id": 10}]
            self.paperless.apply()
            self.assertEqual(self.settings.value["account_id"], 8)
            self.assertEqual(self.settings.value["rule_id"], 10)
            self.assertTrue(self.settings.value["applied"])

    def test_superuser_recovers_deleted_rule_without_duplicating_account(self):
        self.settings.save(
            self.payload | {"account_id": 7, "rule_id": 9, "applied": True, "linked_user_id": 3}
        )
        with patch.object(self.paperless, "request") as request:
            request.side_effect = [
                {"user": self.user | {"is_superuser": True}},
                {},
                {"id": 7, "owner": 3},
                PaperlessError(404, "mail_rules/9/", b""),
                {"success": True},
            ]
            self.paperless.test()
            self.assertEqual(self.settings.value["account_id"], 7)
            self.assertIsNone(self.settings.value["rule_id"])
            self.assertFalse(self.settings.value["applied"])
            self.assertEqual(self.settings.value["linked_user_id"], 3)
            request.side_effect = [
                {"user": self.user | {"is_superuser": True}},
                {},
                {"id": 7, "owner": 3},
                {"success": True},
                {},
                {"id": 10},
            ]
            self.paperless.apply()
            self.assertEqual(self.settings.value["account_id"], 7)
            self.assertEqual(self.settings.value["rule_id"], 10)
            self.assertTrue(self.settings.value["applied"])
            self.assertEqual(request.call_args.args[:2], ("POST", "mail_rules/"))
            self.assertEqual(request.call_args_list[-2].args[:2], ("PATCH", "mail_accounts/7/"))

    def test_inaccessible_or_failed_lookup_never_discards_existing_links(self):
        self.settings.save(self.payload | {"account_id": 7, "rule_id": 9})
        for key, resource in (("account_id", "mail_accounts"), ("rule_id", "mail_rules")):
            for superuser in (False, "true", None):
                with patch.object(
                    self.paperless, "request", side_effect=PaperlessError(404, resource, b"")
                ):
                    with self.assertRaisesRegex(ValueError, "missing or inaccessible"):
                        self.paperless.existing_id(
                            resource, key, self.user | {"is_superuser": superuser}
                        )
            for status in (401, 403, 500):
                with patch.object(
                    self.paperless, "request", side_effect=PaperlessError(status, resource, b"")
                ) as request:
                    with self.assertRaises(PaperlessError):
                        self.paperless.existing_id(resource, key, self.user)
                    self.assertEqual(request.call_count, 1)
        self.assertEqual(self.settings.value["account_id"], 7)
        self.assertEqual(self.settings.value["rule_id"], 9)

    @patch("bridge_container.paperless.subprocess.run")
    def test_token_rotation_keeps_user_binding_and_url_change_only_resets_this_profile(self, run):
        self.paperless.save(self.payload)
        self.settings.save(
            {
                "linked_user_id": 3,
                "account_id": 7,
                "rule_id": 9,
                "paperless_user": self.user,
                "applied": True,
            }
        )
        other = ImportProfiles(self.global_settings).get("two")
        other.save({"account_id": 11, "rule_id": 12, "folder": "Other"})
        before = other.value
        self.paperless.save(self.payload | {"token": ""})
        self.assertTrue(self.settings.value["applied"])
        self.paperless.save(self.payload | {"token": "replacement-token-value"})
        self.assertEqual(self.settings.value["linked_user_id"], 3)
        self.assertEqual(self.settings.value["rule_id"], 9)
        self.assertIsNone(self.settings.value["paperless_user"])
        self.assertFalse(self.settings.value["applied"])
        run.assert_called_with(
            ["pass", "insert", "--multiline", "--force", self.settings.token_key],
            input="replacement-token-value\n",
            text=True,
            capture_output=True,
            check=True,
        )
        self.paperless.save(self.payload | {"paperless_url": "https://another.test"})
        for key in ("account_id", "rule_id", "linked_user_id"):
            self.assertIsNone(self.settings.value[key])
        self.assertEqual(other.value, before)
