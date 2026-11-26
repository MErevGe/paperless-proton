import os
import ssl
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge_container.certificates import prepare_certificates, subject_alt_name


class CertificateTests(unittest.TestCase):
    def test_subject_names(self):
        for name, expected in (
            ("bridge.example.test", "DNS:bridge.example.test"),
            ("192.0.2.2", "IP:192.0.2.2"),
            ("::1", "IP:::1"),
        ):
            with self.subTest(name=name):
                self.assertEqual(subject_alt_name(name), expected)

    def test_rejects_unsafe_names(self):
        for name in (
            "",
            "-bad",
            "bad-",
            "foo..bar",
            "a" * 64,
            "a." * 128,
            "foo\nkeyUsage=keyCertSign",
            "foo,IP:1.2.3.4",
            "$(id)",
            "foo/bar",
        ):
            with self.subTest(name=name), self.assertRaises(ValueError):
                subject_alt_name(name)

    def test_certificate_creation_reuse_and_hostname_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            old_umask = os.umask(0o077)
            try:
                tls = prepare_certificates(data, "bridge.test")
                ca = (tls / "ca.crt").read_bytes()
                certificate = (tls / "server.crt").read_bytes()
                self.assertEqual((tls / "server.key").stat().st_mode & 0o777, 0o600)
                subprocess.run(
                    [
                        "openssl",
                        "verify",
                        "-CAfile",
                        str(tls / "ca.crt"),
                        "-verify_hostname",
                        "bridge.test",
                        str(tls / "server.crt"),
                    ],
                    check=True,
                    capture_output=True,
                )
                prepare_certificates(data, "bridge.test")
                self.assertEqual((tls / "server.crt").read_bytes(), certificate)
                prepare_certificates(data, "192.0.2.2")
                self.assertEqual((tls / "ca.crt").read_bytes(), ca)
                self.assertNotEqual((tls / "server.crt").read_bytes(), certificate)
                long_hostname = ".".join(["a" * 63, "b" * 63, "example", "internal"])
                prepare_certificates(data, long_hostname)
                subprocess.run(
                    [
                        "openssl",
                        "verify",
                        "-CAfile",
                        str(tls / "ca.crt"),
                        "-verify_hostname",
                        long_hostname,
                        str(tls / "server.crt"),
                    ],
                    check=True,
                    capture_output=True,
                )
            finally:
                os.umask(old_umask)

    def test_renews_expiring_certificate(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            tls = prepare_certificates(data, "bridge.test")
            certificate = (tls / "server.crt").read_bytes()
            real_run = subprocess.run

            def run(args, **kwargs):
                if "-checkend" in args:
                    return subprocess.CompletedProcess(args, 1)
                return real_run(args, **kwargs)

            with patch("bridge_container.certificates.subprocess.run", side_effect=run):
                prepare_certificates(data, "bridge.test")
            self.assertNotEqual((tls / "server.crt").read_bytes(), certificate)

    def test_missing_ca_key_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            (data / "tls").mkdir()
            (data / "tls" / "ca.crt").write_text("incomplete")
            with self.assertRaises(ValueError):
                prepare_certificates(data, "bridge.test")

    def test_invalid_certificate_fails_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            tls = prepare_certificates(data, "bridge.test")
            (tls / "server.key").write_text("invalid")
            with self.assertRaises(ssl.SSLError):
                prepare_certificates(data, "bridge.test")
