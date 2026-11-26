import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge_container.settings import Settings, load_json, save_json, text_field, web_origin
from bridge_container.web_auth import COOKIE, Auth, session_cookie


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)
        self.auth = Auth(self.data)
        self.password = "a-long-test-password"
        self.token = self.auth.token_path.read_text()

    def setup_admin(self):
        return self.auth.login(self.password, self.token)

    def test_bootstrap_password_hash_and_one_time_token(self):
        Auth(self.data)
        self.assertEqual(self.auth.token_path.read_text(), self.token)
        session, csrf = self.setup_admin()
        self.assertTrue(self.auth.configured)
        self.assertFalse(self.auth.token_path.exists())
        self.assertNotIn(self.password, self.auth.path.read_text())
        self.assertEqual(self.auth.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.auth.session(f"{COOKIE}={session}"), (session, csrf))
        with self.assertRaises(PermissionError):
            self.auth.login(self.password, self.token)
        self.assertTrue(Auth(self.data).check_password(self.password))

    def test_invalid_bootstrap_and_short_password(self):
        self.assertFalse(self.auth.check_password(self.password))
        with self.assertRaises(PermissionError):
            self.auth.login(self.password, "wrong")
        with self.assertRaises(ValueError):
            self.auth.login("short", self.token)
        self.assertFalse(self.auth.configured)

    def test_password_rate_limit_and_recovery(self):
        self.setup_admin()
        for _ in range(5):
            with self.assertRaises(PermissionError):
                self.auth.login("incorrect")
        with self.assertRaisesRegex(PermissionError, "Too many"):
            self.auth.login(self.password)
        self.auth.attempts = [time.monotonic() - 61]
        self.assertTrue(self.auth.login(self.password)[0])

    def test_session_expiry_logout_and_password_rotation(self):
        session, _ = self.setup_admin()
        self.auth.sessions[session] = (time.monotonic() - 1, "csrf")
        self.assertEqual(self.auth.session(f"{COOKIE}={session}"), ("", ""))
        self.assertEqual(self.auth.session(""), ("", ""))
        with patch("bridge_container.web_auth.SimpleCookie") as cookie:
            from http.cookies import CookieError

            cookie.return_value.load.side_effect = CookieError()
            self.assertEqual(self.auth.session("broken"), ("", ""))
        session, _ = self.auth.login(self.password)
        self.auth.logout(session)
        self.assertFalse(self.auth.session(f"{COOKIE}={session}")[0])
        self.auth.login(self.password)
        self.auth.set_password("another-long-password")
        self.assertFalse(self.auth.sessions)
        self.assertFalse(self.auth.check_password(self.password))
        self.assertTrue(self.auth.check_password("another-long-password"))
        self.assertIn("Secure; HttpOnly; SameSite=Strict", session_cookie(session))
        self.assertIn("Max-Age=0", session_cookie(""))

    def test_session_count_is_bounded(self):
        self.setup_admin()
        for _ in range(17):
            self.auth.login(self.password)
        self.assertEqual(len(self.auth.sessions), 16)

    def test_settings_persist_and_validate(self):
        with patch.dict("os.environ", {"BRIDGE_HOST": "bridge.test", "BRIDGE_PORT": "1993"}):
            settings = Settings(self.data)
            settings.save({"host": "mail.test"})
            self.assertEqual(Settings(self.data).value["host"], "mail.test")
        save_json(self.data / "nested/test.json", {"key": "value"})
        self.assertEqual(load_json(self.data / "nested/test.json"), {"key": "value"})
        (self.data / "invalid.json").write_text("[]")
        with self.assertRaises(ValueError):
            load_json(self.data / "invalid.json")
        for value in (None, 3, "", "a\nb", "a\x7fb", "x" * 257):
            with self.subTest(value=value), self.assertRaises(ValueError):
                text_field(value, "field")
        self.assertEqual(text_field("", "optional", required=False), "")

    @patch.dict("os.environ", {"BRIDGE_PORT": "0"})
    def test_invalid_published_port(self):
        with self.assertRaises(ValueError):
            Settings(self.data)

    def test_web_origin_validation(self):
        for value in (
            "http://localhost:8443",
            "https://u:p@host",
            "https://host/path",
            "https://host?x=1",
            "https://host#x",
            "https://host:0",
            "https://",
            "https://bad host",
        ):
            with patch.dict("os.environ", {"WEB_ORIGIN": value}), self.assertRaises(ValueError):
                web_origin()
        with patch.dict("os.environ", {"WEB_ORIGIN": "https://admin.test:8443"}):
            self.assertEqual(web_origin(), "https://admin.test:8443")
