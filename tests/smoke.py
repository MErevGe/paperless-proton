import imaplib
import json
import socket
import ssl
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

from web_smoke import web_checks


def docker(*args: str, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], check=True, text=True, capture_output=True, **kwargs)


def smoke(image: str, run_browser: bool = False) -> None:
    name = "paperless-proton-test-" + uuid.uuid4().hex[:12]
    volume = name + "-data"
    with socket.socket() as reserve:
        reserve.bind(("127.0.0.1", 0))
        web_port = reserve.getsockname()[1]
    origin = f"https://localhost:{web_port}"
    docker("volume", "create", volume)
    common = [
        "--init",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges:true",
        "--tmpfs",
        "/tmp:rw,nosuid,nodev,noexec,size=64m,mode=1777",
        "--mount",
        f"type=volume,source={volume},target=/data",
        "-e",
        "BRIDGE_HOST=localhost",
        "-e",
        f"WEB_ORIGIN={origin}",
    ]
    try:
        version = docker("run", "--rm", image, "version").stdout
        assert "Proton Mail Bridge 3." in version, version
        docker(
            "run",
            "-d",
            "--name",
            name,
            "-p",
            "127.0.0.1::1993",
            "-p",
            f"127.0.0.1:{web_port}:8443",
            *common,
            image,
        )
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            state = json.loads(docker("inspect", name).stdout)[0]["State"]
            if state["Status"] != "running":
                logs = docker("logs", name)
                raise AssertionError(logs.stdout + logs.stderr)
            if state["Health"]["Status"] == "healthy":
                break
            time.sleep(2)
        else:
            logs = docker("logs", name)
            raise AssertionError(f"Container unhealthy: {state}\n{logs.stdout}\n{logs.stderr}")
        port = int(docker("port", name, "1993/tcp").stdout.strip().rsplit(":", 1)[1])
        exported = docker("exec", name, "python3", "-m", "bridge_container", "certificate").stdout
        assert exported.startswith("-----BEGIN CERTIFICATE-----")
        assert "PRIVATE KEY" not in exported
        with tempfile.TemporaryDirectory() as directory:
            ca = Path(directory) / "ca.crt"
            ca.write_text(exported)
            context = ssl.create_default_context(cafile=str(ca))
            with imaplib.IMAP4_SSL("127.0.0.1", port, ssl_context=context, timeout=5) as mail:
                try:
                    mail.login("nonexistent-user", "invalid-password")
                except imaplib.IMAP4.error:
                    pass
                else:
                    raise AssertionError("Invalid credentials were accepted")
            with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
                with context.wrap_socket(connection, server_hostname="localhost") as secured:
                    assert secured.recv(4096).startswith(b"* OK")
            for context, hostname in (
                (ssl.create_default_context(), "localhost"),
                (ssl.create_default_context(cafile=str(ca)), "wrong.invalid"),
            ):
                try:
                    with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
                        context.wrap_socket(connection, server_hostname=hostname)
                except ssl.SSLCertVerificationError:
                    pass
                else:
                    raise AssertionError("Untrusted certificate or wrong hostname was accepted")
            with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
                connection.sendall(b"a LOGIN username password\r\n")
                try:
                    response = connection.recv(4096)
                except ConnectionResetError:
                    response = b""
                assert b"OK" not in response, "Plaintext IMAP was accepted"
        web_checks(name, origin, docker, run_browser)
        key_id = docker("exec", name, "cat", "/data/password-store/.gpg-id").stdout
        docker(
            "exec",
            "-i",
            name,
            "pass",
            "insert",
            "--multiline",
            "--force",
            "smoke-test",
            input="roundtrip-test-value\n",
        )
        docker("restart", "--time", "20", name)
        assert docker("exec", name, "cat", "/data/password-store/.gpg-id").stdout == key_id
        assert docker("exec", name, "cat", "/data/tls/ca.crt").stdout == exported
        assert docker("exec", name, "pass", "show", "smoke-test").stdout == "roundtrip-test-value\n"
        if run_browser:
            persisted = docker("exec", name, "pass", "show", "paperless-proton/api-token").stdout
            assert persisted == "test-paperless-api-token-value\n"
        docker("stop", "--time", "20", name)
        configured = docker(
            "run",
            "--rm",
            "-i",
            *common,
            image,
            "setup",
            input="updates autoupdates disable\nexit\n",
            timeout=90,
        )
        assert "panic" not in configured.stderr.lower(), configured.stderr
        print(f"Passed: {image}: startup, TLS, validation, plaintext rejection, persistence")
    finally:
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(["docker", "volume", "rm", volume], capture_output=True, check=False)


if __name__ == "__main__":
    smoke(sys.argv[1], "--browser" in sys.argv[2:])
