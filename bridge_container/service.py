import imaplib
import signal
import ssl
import subprocess
import threading
from pathlib import Path


def healthcheck(data: Path, port: int = 1993) -> None:
    context = ssl.create_default_context(cafile=str(data / "tls" / "ca.crt"))
    with imaplib.IMAP4_SSL("localhost", port, ssl_context=context, timeout=5) as connection:
        status, _ = connection.noop()
        if status != "OK":
            raise RuntimeError("IMAP healthcheck failed")


def serve(data: Path) -> int:
    stopped = threading.Event()
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda _signal, _frame: stopped.set())
    processes: list[subprocess.Popen[bytes]] = []
    try:
        processes.append(subprocess.Popen(["proton-bridge", "--grpc", "--log-level", "warn"]))
        processes.append(subprocess.Popen(["python3", "-m", "bridge_container", "web"]))
        processes.append(
            subprocess.Popen(
                [
                    "socat",
                    f"OPENSSL-LISTEN:8443,reuseaddr,fork,max-children=32,verify=0,"
                    f"min-version=TLS1.2,cert={data}/web/tls/server.crt,"
                    f"key={data}/web/tls/server.key",
                    "TCP:127.0.0.1:8080,connect-timeout=5",
                ]
            )
        )
        processes.append(
            subprocess.Popen(
                [
                    "socat",
                    f"OPENSSL-LISTEN:1993,reuseaddr,fork,max-children=32,verify=0,"
                    f"min-version=TLS1.2,cert={data}/tls/server.crt,key={data}/tls/server.key",
                    "TCP:127.0.0.1:1143,connect-timeout=5",
                ]
            )
        )
        while not stopped.wait(0.25):
            if any(process.poll() is not None for process in processes):
                return 1
        return 0
    finally:
        for process in processes:
            if process.poll() is None:
                process.terminate()
        for process in processes:
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
