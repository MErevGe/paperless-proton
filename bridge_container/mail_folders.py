import ssl
from imaplib import IMAP4
from pathlib import Path
from typing import Any

from imapclient import IMAPClient

from bridge_container.bridge_api import BridgeAPI
from bridge_container.settings import text_field


def list_folders(data: Path, bridge: BridgeAPI, user_id: Any, address: Any) -> list[str]:
    credentials = bridge.credentials(user_id, address)
    context = ssl.create_default_context(cafile=str(data / "tls/ca.crt"))
    try:
        with IMAPClient("localhost", port=1993, ssl_context=context, timeout=15) as client:
            client.login(credentials["username"], credentials["password"])
            folders = {
                text_field(name, "Proton folder")
                for flags, _delimiter, name in client.list_folders()
                if b"\\noselect" not in {flag.lower() for flag in flags}
            }
    except IMAP4.error, OSError, ValueError:
        raise ValueError(
            "Could not load Proton folders. Check the Proton connection and try Refresh folders."
        ) from None
    return sorted(folders, key=lambda name: (name.upper() != "INBOX", name.casefold(), name))
