import argparse
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

from bridge_container.certificates import prepare_certificates
from bridge_container.credentials import prepare_credentials
from bridge_container.service import healthcheck, serve
from bridge_container.settings import Settings, web_origin


def main(argv: list[str] | None = None, data: Path = Path("/data")) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command",
        choices=(
            "setup",
            "serve",
            "certificate",
            "healthcheck",
            "version",
            "web",
            "web-setup-token",
            "web-certificate",
            "reset-web-access",
        ),
    )
    args = parser.parse_args(argv)
    if args.command == "version":
        return subprocess.run(["proton-bridge", "--version"], check=False).returncode
    if args.command in ("certificate", "web-certificate"):
        directory = data / "web" if args.command == "web-certificate" else data
        sys.stdout.write((directory / "tls" / "ca.crt").read_text())
        return 0
    if args.command == "web-setup-token":
        sys.stdout.write((data / "web" / "setup-token").read_text() + "\n")
        return 0
    if args.command == "reset-web-access":
        from bridge_container.web_auth import Auth

        (data / "web" / "admin.json").unlink(missing_ok=True)
        (data / "web" / "setup-token").unlink(missing_ok=True)
        Auth(data)
        print("Admin access reset. Start the container and retrieve the new setup token.")
        return 0
    if args.command == "web":
        from bridge_container.web_server import run_web

        run_web(data)
        return 0
    if args.command == "healthcheck":
        healthcheck(data)
        return 0
    host = Settings(data).value["host"]
    os.umask(0o077)
    prepare_certificates(data, host)
    prepare_credentials(data)
    if args.command == "setup":
        os.execvp("proton-bridge", ["proton-bridge", "--cli", "--log-level", "warn"])
    prepare_certificates(data / "web", urlsplit(web_origin()).hostname or "localhost")
    return serve(data)


def entrypoint() -> None:
    try:
        code = main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(f"Bridge container: {type(error).__name__}: {error}", file=sys.stderr)
        code = 1
    raise SystemExit(code)


if __name__ == "__main__":
    entrypoint()
