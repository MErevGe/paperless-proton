import json
import re
import ssl
from http.client import HTTPException, HTTPSConnection
from typing import Any
from urllib.parse import urlencode

MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class SyncError(Exception):
    pass


def object_value(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise SyncError("The API returned an invalid object.")
    return value


def array_value(value: Any) -> list[Any]:
    if not isinstance(value, list):
        raise SyncError("The API returned an invalid list.")
    return value


def text_value(value: Any) -> str:
    if not isinstance(value, str) or not value or len(value) > 16000:
        raise SyncError("The API returned an invalid text field.")
    return value


def integer_value(value: Any, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise SyncError("The API returned an invalid number.")
    return value


def identifier(value: Any) -> str:
    result = text_value(value)
    if not re.fullmatch(r"[a-zA-Z0-9_.:-]{1,256}", result):
        raise SyncError("The API returned an invalid identifier.")
    return result


class Api:
    def __init__(self, host: str, token: str) -> None:
        if host not in {"sonarcloud.io", "api.github.com"}:
            raise SyncError("Unsupported API host.")
        if not token or any(character.isspace() for character in token):
            raise SyncError("A valid API token is required.")
        self.host = host
        self._token = token

    def request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, Any] | None = None,
    ) -> Any:
        if method not in {"GET", "POST", "PATCH"} or not path.startswith("/"):
            raise SyncError("Unsupported API request.")
        if query:
            path += "?" + urlencode(query)
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
            "User-Agent": "paperless-proton-sonar-sync",
        }
        if self.host == "api.github.com":
            headers["X-GitHub-Api-Version"] = "2022-11-28"
        payload = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            payload = json.dumps(body).encode("utf-8")
        connection = HTTPSConnection(self.host, timeout=30, context=ssl.create_default_context())
        try:
            connection.request(method, path, body=payload, headers=headers)
            response = connection.getresponse()
            if not 200 <= response.status < 300:
                raise SyncError(f"{self.host} returned HTTP {response.status}; sync stopped.")
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            if len(raw) > MAX_RESPONSE_BYTES:
                raise SyncError("The API response exceeds the size limit.")
            decoded = raw.decode("utf-8").replace(self._token, "[redacted]")
            return json.loads(decoded)
        except OSError, HTTPException, ValueError:
            raise SyncError(f"Could not read a valid HTTPS response from {self.host}.") from None
        finally:
            connection.close()
