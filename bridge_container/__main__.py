import argparse
import os
import subprocess
import sys
from pathlib import Path

from bridge_container.certificates import prepare_certificates
from bridge_container.credentials import prepare_credentials
from bridge_container.service import healthcheck, serve


def main(argv: list[str] | None = None, data: Path = Path("/data")) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "command", choices=("setup", "serve", "certificate", "healthcheck", "version")
    )
    args = parser.parse_args(argv)
    if args.command == "version":
        return subprocess.run(["proton-bridge", "--version"], check=False).returncode
    if args.command == "certificate":
        sys.stdout.write((data / "tls" / "ca.crt").read_text())
        return 0
    if args.command == "healthcheck":
        healthcheck(data)
        return 0
    host = os.environ.get("BRIDGE_HOST", "")
    if not host:
        raise ValueError("Set BRIDGE_HOST to the hostname or IP used by Paperless")
    os.umask(0o077)
    prepare_certificates(data, host)
    prepare_credentials(data)
    if args.command == "setup":
        os.execvp("proton-bridge", ["proton-bridge", "--cli", "--log-level", "warn"])
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
