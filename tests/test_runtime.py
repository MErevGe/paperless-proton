import io
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from bridge_container.__main__ import entrypoint, main
from bridge_container.credentials import prepare_credentials
from bridge_container.service import healthcheck, serve


class CredentialTests(unittest.TestCase):
    @patch("bridge_container.credentials.subprocess.run")
    def test_keeps_existing_store(self, run):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            (path / "password-store").mkdir()
            (path / "password-store" / ".gpg-id").touch()
            prepare_credentials(path)
            run.assert_not_called()

    @patch("bridge_container.credentials.subprocess.run")
    def test_reuses_key(self, run):
        run.return_value.stdout = "fpr:::::::::ABCDEF:\n"
        with tempfile.TemporaryDirectory() as directory:
            prepare_credentials(Path(directory))
        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args.args[0], ["pass", "init", "ABCDEF"])

    @patch("bridge_container.credentials.subprocess.run")
    def test_creates_key(self, run):
        run.side_effect = [
            subprocess.CompletedProcess([], 0, stdout=""),
            subprocess.CompletedProcess([], 0),
            subprocess.CompletedProcess([], 0, stdout="fpr:::::::::ABCDEF:\n"),
            subprocess.CompletedProcess([], 0),
        ]
        with tempfile.TemporaryDirectory() as directory:
            prepare_credentials(Path(directory))
        self.assertEqual(run.call_count, 4)

    @patch(
        "bridge_container.credentials.subprocess.run",
        side_effect=subprocess.CalledProcessError(1, "gpg"),
    )
    def test_keychain_failure_is_not_ignored(self, _run):
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaises(subprocess.CalledProcessError),
        ):
            prepare_credentials(Path(directory))


class ServiceTests(unittest.TestCase):
    @patch("bridge_container.service.ssl.create_default_context")
    @patch("bridge_container.service.imaplib.IMAP4_SSL")
    def test_healthcheck(self, imap, context):
        imap.return_value.__enter__.return_value.noop.return_value = ("OK", [])
        healthcheck(Path("/data"))
        context.assert_called_once_with(cafile="/data/tls/ca.crt")
        imap.assert_called_once_with("localhost", 1993, ssl_context=context.return_value, timeout=5)
        imap.return_value.__enter__.return_value.noop.return_value = ("NO", [])
        with self.assertRaises(RuntimeError):
            healthcheck(Path("/data"))

    @patch("bridge_container.service.signal.signal")
    @patch("bridge_container.service.threading.Event")
    @patch("bridge_container.service.subprocess.Popen")
    def test_supervisor_stops_children_on_signal(self, popen, event, signal):
        event.return_value.wait.side_effect = [False, True]
        child = popen.return_value
        child.poll.return_value = None
        self.assertEqual(serve(Path("/data")), 0)
        self.assertEqual(child.terminate.call_count, 2)
        signal.call_args.args[1](None, None)
        event.return_value.set.assert_called_once()

    @patch("bridge_container.service.signal.signal")
    @patch("bridge_container.service.threading.Event")
    @patch("bridge_container.service.subprocess.Popen")
    def test_supervisor_fails_when_child_dies(self, popen, event, _signal):
        event.return_value.wait.return_value = False
        dead, live = MagicMock(), MagicMock()
        dead.poll.return_value = 1
        live.poll.return_value = None
        popen.side_effect = [dead, live]
        self.assertEqual(serve(Path("/data")), 1)
        live.terminate.assert_called_once()
        dead.terminate.assert_not_called()

    @patch("bridge_container.service.signal.signal")
    @patch("bridge_container.service.threading.Event")
    @patch("bridge_container.service.subprocess.Popen")
    def test_supervisor_kills_stuck_child(self, popen, event, _signal):
        event.return_value.wait.return_value = True
        child = popen.return_value
        child.poll.return_value = None
        child.wait.side_effect = [subprocess.TimeoutExpired("bridge", 10), 0, 0]
        self.assertEqual(serve(Path("/data")), 0)
        child.kill.assert_called_once()

    @patch("bridge_container.service.signal.signal")
    @patch("bridge_container.service.subprocess.Popen")
    def test_second_process_start_failure_stops_bridge(self, popen, _signal):
        child = MagicMock()
        child.poll.return_value = None
        popen.side_effect = [child, OSError("start failed")]
        with self.assertRaises(OSError):
            serve(Path("/data"))
        child.terminate.assert_called_once()


class CommandTests(unittest.TestCase):
    @patch("bridge_container.__main__.subprocess.run")
    def test_version(self, run):
        run.return_value.returncode = 0
        self.assertEqual(main(["version"]), 0)

    def test_certificate_exports_only_public_data(self):
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(io.StringIO()) as output:
            data = Path(directory)
            (data / "tls").mkdir()
            (data / "tls" / "ca.crt").write_text("PUBLIC CERTIFICATE\n")
            self.assertEqual(main(["certificate"], data), 0)
            self.assertEqual(output.getvalue(), "PUBLIC CERTIFICATE\n")

    @patch("bridge_container.__main__.healthcheck")
    def test_healthcheck_command(self, check):
        self.assertEqual(main(["healthcheck"]), 0)
        check.assert_called_once()

    @patch.dict("os.environ", {}, clear=True)
    def test_missing_hostname(self):
        with self.assertRaises(ValueError):
            main(["serve"])

    @patch.dict("os.environ", {"BRIDGE_HOST": "bridge.test"})
    @patch("bridge_container.__main__.prepare_certificates")
    @patch("bridge_container.__main__.prepare_credentials")
    @patch("bridge_container.__main__.serve", return_value=0)
    def test_service_command(self, service, credentials, certificates):
        self.assertEqual(main(["serve"]), 0)
        certificates.assert_called_once_with(Path("/data"), "bridge.test")
        credentials.assert_called_once()
        service.assert_called_once()

    @patch.dict("os.environ", {"BRIDGE_HOST": "bridge.test"})
    @patch("bridge_container.__main__.prepare_certificates")
    @patch("bridge_container.__main__.prepare_credentials")
    @patch("bridge_container.__main__.os.execvp", side_effect=SystemExit(0))
    def test_setup_command(self, execute, _credentials, _certificates):
        with self.assertRaises(SystemExit):
            main(["setup"])
        self.assertIn("--cli", execute.call_args.args[1])

    @patch("bridge_container.__main__.main", side_effect=ValueError("invalid hostname"))
    def test_entrypoint_reports_failure(self, _main):
        with redirect_stderr(io.StringIO()) as output, self.assertRaises(SystemExit) as failure:
            entrypoint()
        self.assertEqual(failure.exception.code, 1)
        self.assertIn("invalid hostname", output.getvalue())

    @patch("bridge_container.__main__.main", return_value=0)
    def test_entrypoint_success(self, _main):
        with self.assertRaises(SystemExit) as result:
            entrypoint()
        self.assertEqual(result.exception.code, 0)
