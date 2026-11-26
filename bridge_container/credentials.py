import os
import subprocess
from pathlib import Path


def prepare_credentials(data: Path) -> None:
    os.umask(0o077)
    for name in ("gnupg", "password-store", "config", "cache", "share"):
        (data / name).mkdir(mode=0o700, parents=True, exist_ok=True)
    if (data / "password-store" / ".gpg-id").exists():
        return
    listing = subprocess.run(
        ["gpg", "--batch", "--with-colons", "--list-secret-keys"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    if not any(line.startswith("fpr:") for line in listing.splitlines()):
        subprocess.run(
            [
                "gpg",
                "--batch",
                "--pinentry-mode",
                "loopback",
                "--passphrase",
                "",
                "--quick-generate-key",
                "Paperless Proton Bridge",
                "default",
                "default",
                "never",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
        )
        listing = subprocess.run(
            ["gpg", "--batch", "--with-colons", "--list-secret-keys"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    fingerprint = next(
        line.split(":")[9] for line in listing.splitlines() if line.startswith("fpr:")
    )
    subprocess.run(["pass", "init", fingerprint], check=True, stdout=subprocess.DEVNULL)
