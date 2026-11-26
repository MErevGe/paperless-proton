import base64
import threading
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import grpc
from google.protobuf.empty_pb2 import Empty
from google.protobuf.wrappers_pb2 import BoolValue, StringValue

from bridge_container.generated import bridge_pb2 as pb
from bridge_container.generated.bridge_pb2_grpc import BridgeStub
from bridge_container.settings import load_json, text_field


class BridgeAPI:
    def __init__(self, data: Path):
        self.data = data
        self.lock = threading.RLock()
        self.ready = False
        self.login_state: dict[str, Any] = {"step": "idle"}
        self.login_started = 0.0
        self.channel: Any = None
        self.stub: Any = None
        self.metadata: tuple[tuple[str, str], ...] = ()

    def connect(self) -> None:
        config = load_json(self.data / "config/protonmail/bridge-v3/grpcServerConfig.json")
        credentials = grpc.ssl_channel_credentials(config["cert"].encode())
        self.channel = grpc.secure_channel(
            "unix:" + config["fileSocketPath"],
            credentials,
            options=(("grpc.ssl_target_name_override", "127.0.0.1"),),
        )
        self.stub = BridgeStub(self.channel)
        self.metadata = (("server-token", config["token"]),)
        self.call("GuiReady", Empty())
        self.call("SetIsAutomaticUpdateOn", BoolValue(value=False))
        self.ready = True

    def call(self, method: str, request: Any) -> Any:
        if self.stub is None:
            raise RuntimeError("Bridge is starting. Try again shortly.")
        return getattr(self.stub, method)(request, metadata=self.metadata, timeout=15)

    def watch(self, stopped: threading.Event) -> None:
        while not stopped.is_set():
            try:
                self.connect()
                events = self.stub.RunEventStream(
                    pb.EventStreamRequest(ClientPlatform="Linux"), metadata=self.metadata
                )
                for event in events:
                    if stopped.is_set():
                        break
                    if event.HasField("login"):
                        self.handle_event(event.login)
            except OSError, ValueError, grpc.RpcError:
                self.ready = False
            finally:
                self.ready = False
                if self.channel is not None:
                    self.channel.close()
                self.stub = None
            stopped.wait(2)

    def handle_event(self, event: pb.LoginEvent) -> None:
        kind = event.WhichOneof("event")
        with self.lock:
            if kind in ("finished", "alreadyLoggedIn"):
                self.login_state = {"step": "complete"}
            elif kind in ("tfaRequested", "tfaOrFidoRequested"):
                self.login_state.update(step="two_factor", error="")
            elif kind == "twoPasswordRequested":
                self.login_state.update(step="mailbox_password", error="")
            elif kind == "hvRequested":
                url = event.hvRequested.hvUrl
                parsed = urlsplit(url)
                if parsed.scheme == "https" and parsed.hostname == "verify.proton.me":
                    self.login_state.update(step="verification", url=url, error="")
                else:
                    self.login_state.update(step="error", error="Unsupported verification URL")
            elif kind == "fidoRequested":
                self.call("LoginAbort", pb.LoginAbortRequest())
                self.login_state = {
                    "step": "error",
                    "error": "Enable an authenticator code in Proton to sign in here. "
                    "A security key connected to your browser cannot be used by this container.",
                }
            elif kind == "error":
                retry = {pb.TFA_ERROR: "two_factor", pb.TWO_PASSWORDS_ERROR: "mailbox_password"}
                messages = {
                    pb.FREE_USER: "A paid Proton Mail plan is required.",
                    pb.CONNECTION_ERROR: "Proton is unreachable. Try again.",
                    pb.TFA_ERROR: "Invalid authenticator code. Try a new code.",
                    pb.TWO_PASSWORDS_ERROR: "Invalid mailbox password. Try again.",
                    pb.HV_ERROR: "Verification failed. Start the login again.",
                }
                self.login_state.update(
                    step=retry.get(event.error.type, "error"),
                    error=messages.get(event.error.type, "Sign-in failed. Check your credentials."),
                )

    def status(self) -> dict[str, Any]:
        with self.lock:
            if self.login_state["step"] not in ("idle", "complete", "error"):
                if time.monotonic() - self.login_started > 300:
                    self.abort()
                    self.login_state = {"step": "error", "error": "Sign-in expired. Start again."}
            return dict(self.login_state)

    def expire_logins(self, stopped: threading.Event) -> None:
        while not stopped.wait(10):
            try:
                self.status()
            except grpc.RpcError, RuntimeError:
                with self.lock:
                    self.login_state = {
                        "step": "error",
                        "error": "Bridge disconnected. Start again.",
                    }

    def login(self, username: Any, password: Any) -> None:
        username = text_field(username, "Proton username")
        password = text_field(password, "Proton password", 1024)
        with self.lock:
            if self.status()["step"] not in ("idle", "complete", "error"):
                raise ValueError("A sign-in is already in progress. Cancel it first.")
            self.login_started = time.monotonic()
            self.login_state = {"step": "waiting", "username": username}
            try:
                self.call(
                    "Login",
                    pb.LoginRequest(
                        username=username, password=base64.b64encode(password.encode())
                    ),
                )
            except grpc.RpcError, RuntimeError:
                self.login_state = {"step": "error", "error": "Could not start the Proton sign-in."}
                raise

    def answer(self, password: Any) -> None:
        password = text_field(password, "code or password", 1024)
        with self.lock:
            step = self.status()["step"]
            methods = {
                "two_factor": "Login2FA",
                "mailbox_password": "Login2Passwords",
                "verification": "Login",
            }
            if step not in methods:
                raise ValueError("Start the Proton login first")
            request = pb.LoginRequest(
                username=self.login_state["username"],
                password=base64.b64encode(password.encode()),
                useHvDetails=step == "verification",
            )
            self.login_state.update(step="waiting", error="")
            try:
                self.call(methods[step], request)
            except grpc.RpcError, RuntimeError:
                self.login_state = {"step": "error", "error": "Bridge disconnected. Start again."}
                raise

    def abort(self) -> None:
        with self.lock:
            self.call("LoginAbort", pb.LoginAbortRequest())
            self.login_state = {"step": "idle"}

    def accounts(self) -> list[dict[str, Any]]:
        response = self.call("GetUserList", Empty())
        return [
            {
                "id": user.id,
                "username": user.username,
                "addresses": list(user.addresses),
                "connected": user.state == pb.CONNECTED,
                "split": user.splitMode,
            }
            for user in response.users
        ]

    def credentials(self, user_id: Any, address: Any) -> dict[str, str]:
        user_id = text_field(user_id, "account")
        address = text_field(address, "email address")
        user = self.call("GetUser", StringValue(value=user_id))
        if user.state != pb.CONNECTED or address not in user.addresses:
            raise ValueError("Select a connected Proton account and one of its email addresses")
        return {"username": address, "password": bytes(user.password).decode()}

    def logout(self, user_id: Any) -> None:
        user_id = text_field(user_id, "account")
        if user_id not in [account["id"] for account in self.accounts()]:
            raise ValueError("Unknown Proton account")
        self.call("LogoutUser", StringValue(value=user_id))
