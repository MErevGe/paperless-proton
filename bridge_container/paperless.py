import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from bridge_container.bridge_api import BridgeAPI
from bridge_container.paperless_errors import MAXIMUM_AGE_HELP, MAXIMUM_AGE_LIMIT, PaperlessError
from bridge_container.settings import Settings, text_field


def validate_maximum_age(age: Any) -> int:
    if type(age) is not int or not 0 <= age <= MAXIMUM_AGE_LIMIT:
        raise ValueError("Invalid maximum email age. " + MAXIMUM_AGE_HELP)
    return age


def validate_url(value: Any, allow_http: bool) -> str:
    value = text_field(value, "Paperless URL", 512).rstrip("/")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in ("https", "http")
        or (parsed.scheme == "http" and not allow_http)
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "%" in parsed.netloc
        or "\\" in value
        or not 1 <= (parsed.port if parsed.port is not None else 443) <= 65535
    ):
        raise ValueError("Use an HTTPS Paperless URL without credentials or query parameters")
    return str(value)


def target_address(host: str, port: int) -> str:
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    for entry in addresses:
        address = ipaddress.ip_address(entry[4][0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if (
            address.is_loopback
            or address.is_link_local
            or address.is_unspecified
            or address.is_multicast
            or address.is_reserved
            or address == ipaddress.ip_address("100.100.100.200")
        ):
            raise ValueError("Paperless must use a LAN, VPN or public server address")
    if not addresses:
        raise ValueError("Paperless hostname has no IP addresses")
    return str(addresses[0][4][0])


class Paperless:
    def __init__(self, settings: Settings, bridge: BridgeAPI):
        self.settings = settings
        self.bridge = bridge

    def token_saved(self) -> bool:
        store = Path(os.environ.get("PASSWORD_STORE_DIR", self.settings.data / "password-store"))
        return (store / "paperless-proton/api-token.gpg").is_file()

    def token(self) -> str:
        result = subprocess.run(
            ["pass", "show", "paperless-proton/api-token"],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def save(self, payload: dict[str, Any]) -> None:
        allow_http = payload.get("allow_http", False)
        if not isinstance(allow_http, bool):
            raise ValueError("Invalid HTTP preference")
        url = validate_url(payload.get("paperless_url"), allow_http)
        ca = payload.get("paperless_ca", "")
        if not isinstance(ca, str) or len(ca) > 32768:
            raise ValueError("Invalid Paperless CA certificate")
        if "PRIVATE KEY" in ca:
            raise ValueError("Paste the public Paperless CA certificate without a private key")
        if ca:
            ssl.create_default_context(cadata=ca)
        changes: dict[str, Any] = {
            "paperless_url": url,
            "paperless_ca": ca,
            "allow_http": allow_http,
        }
        for name in (
            "account_name",
            "rule_name",
            "folder",
            "destination",
            "attachment_filter",
            "proton_id",
            "address",
        ):
            changes[name] = text_field(payload.get(name), name.replace("_", " "))
        changes["maximum_age"] = validate_maximum_age(payload.get("maximum_age"))
        if changes["folder"] == changes["destination"]:
            raise ValueError("Source and destination folders must be different")
        token = payload.get("token", "")
        if token:
            if not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{16,256}", token):
                raise ValueError("Invalid Paperless API token")
            subprocess.run(
                ["pass", "insert", "--multiline", "--force", "paperless-proton/api-token"],
                input=token + "\n",
                text=True,
                capture_output=True,
                check=True,
            )
        elif url != self.settings.value["paperless_url"]:
            raise ValueError("Enter an API token for the new Paperless URL")
        else:
            self.token()
        if url != self.settings.value["paperless_url"]:
            changes.update(account_id=None, rule_id=None)
        self.settings.save(changes)

    def request(self, method: str, endpoint: str, payload: dict[str, Any] | None = None) -> Any:
        settings = self.settings.value
        url = urlsplit(validate_url(settings["paperless_url"], settings["allow_http"]))
        host = url.hostname or ""
        port = url.port or (443 if url.scheme == "https" else 80)
        address = target_address(host, port)
        connection = http.client.HTTPConnection(host, port, timeout=20)
        sock = socket.create_connection((address, port), timeout=20)
        try:
            if url.scheme == "https":
                context = ssl.create_default_context(cadata=settings["paperless_ca"] or None)
                sock = context.wrap_socket(sock, server_hostname=host)
            connection.sock = sock
            body = json.dumps(payload).encode() if payload is not None else None
            connection.request(
                method,
                url.path.rstrip("/") + "/api/" + endpoint,
                body=body,
                headers={
                    "Authorization": "Token " + self.token(),
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
            response = connection.getresponse()
            data = response.read(1048577)
            if len(data) > 1048576:
                raise ValueError("Paperless response is too large")
            if not 200 <= response.status < 300:
                raise PaperlessError(response.status, endpoint, data)
            try:
                return json.loads(data)
            except ValueError:
                raise ValueError(
                    "Paperless returned an invalid API response. Check its URL."
                ) from None
        finally:
            connection.close()
            sock.close()

    def account(self) -> dict[str, Any]:
        settings = self.settings.value
        credentials = self.bridge.credentials(settings["proton_id"], settings["address"])
        return {
            "name": settings["account_name"],
            "imap_server": settings["host"],
            "imap_port": settings["port"],
            "imap_security": 2,
            "account_type": 1,
            "is_token": False,
            "character_set": "UTF-8",
            **credentials,
        }

    def test(self) -> None:
        self.request("GET", "mail_accounts/?page_size=1")
        account_id = self.existing_id("mail_accounts", "account_id")
        host = self.settings.value["host"]
        try:
            address = ipaddress.ip_address(host)
            local = address.is_loopback or address.is_unspecified
        except ValueError:
            local = host.lower().rstrip(".") == "localhost" or host.lower().endswith(".localhost")
        if local:
            raise ValueError(
                "Paperless API access succeeded. In Bridge connection, replace localhost or the "
                "loopback address with this server's LAN or VPN address, save and restart Bridge. "
                "Set BRIDGE_BIND_IP to its LAN or VPN address in .env and recreate the container."
            )
        account = self.account()
        if account_id:
            account["id"] = account_id
        response = self.request("POST", "mail_accounts/test/", account)
        if not isinstance(response, dict) or response.get("success") is not True:
            raise ValueError(
                "Paperless could not connect to Bridge. Check its certificate and network."
            )

    def apply(self) -> None:
        validate_maximum_age(self.settings.value["maximum_age"])
        self.test()
        rule_id = self.existing_id("mail_rules", "rule_id")
        account_id = self.settings.value.get("account_id")
        if account_id:
            self.request("PATCH", f"mail_accounts/{int(account_id)}/", self.account())
        else:
            account = self.request("POST", "mail_accounts/", self.account())
            account_id = int(account["id"])
            self.settings.save({"account_id": account_id})
        settings = self.settings.value
        rule = {
            "name": settings["rule_name"],
            "account": account_id,
            "enabled": True,
            "folder": settings["folder"],
            "filter_attachment_filename_include": settings["attachment_filter"],
            "maximum_age": settings["maximum_age"],
            "action": 2,
            "action_parameter": settings["destination"],
            "attachment_type": 1,
            "consumption_scope": 1,
            "assign_title_from": 2,
            "assign_correspondent_from": 1,
        }
        if rule_id:
            self.request("PATCH", f"mail_rules/{int(rule_id)}/", rule)
        else:
            result = self.request("POST", "mail_rules/", rule)
            self.settings.save({"rule_id": int(result["id"])})

    def existing_id(self, resource: str, key: str) -> int | None:
        value = self.settings.value.get(key)
        if not value:
            return None
        identifier = int(value)
        try:
            self.request("GET", f"{resource}/{identifier}/")
        except PaperlessError as error:
            if error.status != 404:
                raise
            identity = self.request("GET", "ui_settings/")
            if identity.get("user", {}).get("is_superuser") is not True:
                raise ValueError(
                    "The saved Paperless email account or import rule is missing or inaccessible. "
                    "Check its owner and permissions in Paperless. An administrator can "
                    "reconnect this setup if the account or rule was deleted."
                ) from None
            changes = {key: None}
            if key == "account_id":
                changes["rule_id"] = None
            self.settings.save(changes)
            return None
        return identifier
