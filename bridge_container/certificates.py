import ipaddress
import os
import re
import ssl
import subprocess
from pathlib import Path


def subject_alt_name(host: str) -> str:
    try:
        return f"IP:{ipaddress.ip_address(host)}"
    except ValueError:
        if len(host) > 253 or not all(
            re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
            for label in host.split(".")
        ):
            raise ValueError("BRIDGE_HOST must be a hostname or IP address") from None
        return f"DNS:{host}"


def openssl(*args: str) -> None:
    subprocess.run(
        ["openssl", *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )


def prepare_certificates(data: Path, host: str) -> Path:
    san = subject_alt_name(host)
    directory = data / "tls"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    ca = directory / "ca.crt"
    ca_key = directory / "ca.key"
    cert = directory / "server.crt"
    key = directory / "server.key"
    identity = directory / "host"
    if ca.exists() != ca_key.exists():
        raise ValueError("Incomplete certificate authority in /data/tls; restore its backup")
    if not ca.exists():
        openssl(
            "req",
            "-x509",
            "-newkey",
            "rsa:3072",
            "-noenc",
            "-days",
            "3650",
            "-keyout",
            str(ca_key),
            "-out",
            str(ca),
            "-subj",
            "/CN=Paperless Proton CA",
            "-addext",
            "basicConstraints=critical,CA:TRUE",
            "-addext",
            "keyUsage=critical,keyCertSign,cRLSign",
        )
    needs_renewal = (
        not cert.exists()
        or not key.exists()
        or not identity.exists()
        or identity.read_text().strip() != host
    )
    if not needs_renewal:
        needs_renewal = (
            subprocess.run(
                ["openssl", "x509", "-checkend", "2592000", "-noout", "-in", str(cert)],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            ).returncode
            != 0
        )
    if needs_renewal:
        request = directory / "server.csr"
        extensions = directory / "server.ext"
        extensions.write_text(
            f"subjectAltName={san},DNS:localhost,IP:127.0.0.1\n"
            "basicConstraints=critical,CA:FALSE\n"
            "keyUsage=critical,digitalSignature,keyEncipherment\n"
            "extendedKeyUsage=serverAuth\n"
        )
        openssl(
            "req",
            "-newkey",
            "rsa:3072",
            "-noenc",
            "-keyout",
            str(key),
            "-out",
            str(request),
            "-subj",
            "/CN=Paperless Proton Bridge",
        )
        openssl(
            "x509",
            "-req",
            "-in",
            str(request),
            "-CA",
            str(ca),
            "-CAkey",
            str(ca_key),
            "-CAcreateserial",
            "-days",
            "365",
            "-out",
            str(cert),
            "-extfile",
            str(extensions),
        )
        identity.write_text(host + "\n")
        request.unlink()
        extensions.unlink()
    for secret in (ca_key, key):
        os.chmod(secret, 0o600)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    return directory
