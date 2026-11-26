import base64
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import grpc

from bridge_container.bridge_api import BridgeAPI
from bridge_container.generated import bridge_pb2 as pb
from bridge_container.settings import save_json


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.data = Path(self.directory.name)
        self.bridge = BridgeAPI(self.data)
        self.bridge.stub = MagicMock()

    def test_authenticated_local_connection_and_updates_disabled(self):
        save_json(
            self.data / "config/protonmail/bridge-v3/grpcServerConfig.json",
            {"cert": "public", "token": "private", "fileSocketPath": "/tmp/bridge.sock"},
        )
        with (
            patch("bridge_container.bridge_api.grpc") as grpc_mock,
            patch("bridge_container.bridge_api.BridgeStub") as stub,
        ):
            self.bridge.connect()
            grpc_mock.secure_channel.assert_called_once()
            self.assertEqual(grpc_mock.secure_channel.call_args.args[0], "unix:/tmp/bridge.sock")
            self.assertEqual(self.bridge.metadata, (("server-token", "private"),))
            self.assertTrue(self.bridge.ready)
            stub.return_value.SetIsAutomaticUpdateOn.assert_called_once()
            self.assertFalse(stub.return_value.SetIsAutomaticUpdateOn.call_args.args[0].value)

    def test_login_serialization_and_challenges(self):
        self.bridge.login("person", "private-password")
        request = self.bridge.stub.Login.call_args.args[0]
        self.assertEqual(base64.b64decode(request.password), b"private-password")
        self.assertNotIn("password", self.bridge.login_state)
        with self.assertRaises(ValueError):
            self.bridge.login("person", "private-password")
        for event, expected, method in (
            (pb.LoginEvent(tfaRequested=pb.LoginTfaRequestedEvent()), "two_factor", "Login2FA"),
            (
                pb.LoginEvent(tfaOrFidoRequested=pb.LoginTfaOrFidoRequestedEvent()),
                "two_factor",
                "Login2FA",
            ),
            (
                pb.LoginEvent(twoPasswordRequested=pb.LoginTwoPasswordsRequestedEvent()),
                "mailbox_password",
                "Login2Passwords",
            ),
            (
                pb.LoginEvent(
                    hvRequested=pb.LoginHvRequestedEvent(
                        hvUrl="https://verify.proton.me/?token=opaque"
                    )
                ),
                "verification",
                "Login",
            ),
        ):
            self.bridge.handle_event(event)
            self.assertEqual(self.bridge.status()["step"], expected)
            self.bridge.answer("answer")
            self.assertEqual(self.bridge.status()["step"], "waiting")
            getattr(self.bridge.stub, method).assert_called()
        self.bridge.handle_event(pb.LoginEvent(finished=pb.LoginFinishedEvent()))
        self.assertEqual(self.bridge.status(), {"step": "complete"})
        self.bridge.handle_event(pb.LoginEvent(alreadyLoggedIn=pb.LoginFinishedEvent()))
        with self.assertRaises(ValueError):
            self.bridge.answer("unexpected")
        self.bridge.abort()
        self.assertEqual(self.bridge.status(), {"step": "idle"})

    def test_login_error_events_and_expiration(self):
        self.bridge.login("person", "secret")
        for code, step in (
            (pb.FREE_USER, "error"),
            (pb.CONNECTION_ERROR, "error"),
            (pb.TFA_ERROR, "two_factor"),
            (pb.TWO_PASSWORDS_ERROR, "mailbox_password"),
            (pb.HV_ERROR, "error"),
            (pb.USERNAME_PASSWORD_ERROR, "error"),
        ):
            self.bridge.handle_event(
                pb.LoginEvent(
                    error=pb.LoginErrorEvent(type=code, message="private upstream detail")
                )
            )
            self.assertEqual(self.bridge.status()["step"], step)
            self.assertNotIn("private upstream", self.bridge.status()["error"])
        self.bridge.handle_event(
            pb.LoginEvent(hvRequested=pb.LoginHvRequestedEvent(hvUrl="https://evil.test/"))
        )
        self.assertEqual(self.bridge.status()["step"], "error")
        self.bridge.handle_event(pb.LoginEvent(fidoRequested=pb.LoginFidoRequestedEvent()))
        self.assertIn("authenticator", self.bridge.status()["error"])
        self.bridge.handle_event(pb.LoginEvent())
        self.bridge.login("person", "secret")
        self.bridge.login_started = time.monotonic() - 301
        self.assertIn("expired", self.bridge.status()["error"])

    def test_account_credentials_and_logout_validation(self):
        user = pb.User(
            id="one",
            username="person",
            state=pb.CONNECTED,
            password=b"bridge-password",
            addresses=["person@example.test"],
        )
        self.bridge.stub.GetUserList.return_value = pb.UserListResponse(users=[user])
        self.bridge.stub.GetUser.return_value = user
        accounts = self.bridge.accounts()
        self.assertNotIn("password", accounts[0])
        self.assertTrue(accounts[0]["connected"])
        self.assertEqual(
            self.bridge.credentials("one", "person@example.test")["password"], "bridge-password"
        )
        with self.assertRaises(ValueError):
            self.bridge.credentials("one", "someone@example.test")
        user.state = pb.SIGNED_OUT
        with self.assertRaises(ValueError):
            self.bridge.credentials("one", "person@example.test")
        self.bridge.logout("one")
        with self.assertRaises(ValueError):
            self.bridge.logout("unknown")
        self.bridge.stub = None
        with self.assertRaises(RuntimeError):
            self.bridge.call("Version", None)

    def test_watcher_retries_and_closes_channels(self):
        stopped = MagicMock()
        stopped.is_set.side_effect = [False, False, True, True]
        channel = MagicMock()
        self.bridge.channel = channel
        self.bridge.stub.RunEventStream.return_value = [
            pb.StreamEvent(login=pb.LoginEvent(finished=pb.LoginFinishedEvent())),
            pb.StreamEvent(),
        ]
        with patch.object(self.bridge, "connect"):
            self.bridge.watch(stopped)
        channel.close.assert_called_once()
        self.assertEqual(self.bridge.status()["step"], "complete")
        for error in (OSError(), grpc.RpcError(), ValueError()):
            stopped.is_set.side_effect = [False, True]
            with patch.object(self.bridge, "connect", side_effect=error):
                self.bridge.watch(stopped)
            self.assertFalse(self.bridge.ready)
        self.bridge.channel = None
        stopped.is_set.side_effect = [False, False, True]
        self.bridge.stub = MagicMock()
        self.bridge.stub.RunEventStream.return_value = [pb.StreamEvent()]
        with patch.object(self.bridge, "connect"):
            self.bridge.watch(stopped)

    def test_failed_rpc_can_be_retried_and_abandoned_login_expires(self):
        self.bridge.stub.Login.side_effect = grpc.RpcError()
        with self.assertRaises(grpc.RpcError):
            self.bridge.login("person", "secret")
        self.assertEqual(self.bridge.status()["step"], "error")
        self.bridge.login_state = {"step": "two_factor", "username": "person"}
        self.bridge.stub.Login2FA.side_effect = grpc.RpcError()
        with self.assertRaises(grpc.RpcError):
            self.bridge.answer("123456")
        self.assertEqual(self.bridge.status()["step"], "error")
        stopped = MagicMock()
        stopped.wait.side_effect = [False, False, True]
        with patch.object(self.bridge, "status", side_effect=[{}, RuntimeError()]):
            self.bridge.expire_logins(stopped)
        self.assertEqual(self.bridge.login_state["step"], "error")
