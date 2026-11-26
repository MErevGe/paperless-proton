import http.client
import json
import secrets
import ssl
import time
from urllib.parse import urlsplit


def web_checks(name, origin, docker, run_browser=False):
    public_ca = docker("exec", name, "python3", "-m", "bridge_container", "web-certificate").stdout
    context = ssl.create_default_context(cadata=public_ca)
    parsed = urlsplit(origin)
    cookie = ""
    csrf = ""

    def request(path, payload=None, headers=None):
        connection = http.client.HTTPSConnection(
            parsed.hostname, parsed.port, context=context, timeout=10
        )
        try:
            body = json.dumps(payload).encode() if payload is not None else None
            connection.request(
                "POST" if payload is not None else "GET",
                "/api/" + path,
                body=body,
                headers={
                    "Content-Type": "application/json",
                    "Cookie": cookie,
                    "Origin": origin,
                    "X-CSRF-Token": csrf,
                }
                | (headers or {}),
            )
            response = connection.getresponse()
            data = response.read()
            return response.status, data, dict(response.getheaders())
        finally:
            connection.close()

    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            status, data, _ = request("session")
            if status == 200:
                break
        except OSError, http.client.HTTPException:
            pass
        time.sleep(1)
    else:
        raise AssertionError("Web interface did not start")
    assert not json.loads(data)["configured"]
    assert request("state")[0] == 401
    assert request("certificate")[0] == 401
    token = docker(
        "exec", name, "python3", "-m", "bridge_container", "web-setup-token"
    ).stdout.strip()
    password = secrets.token_urlsafe(24)
    assert (
        request(
            "setup", {"setup_token": token, "password": password}, {"Origin": "https://other.test"}
        )[0]
        == 403
    )
    status, data, headers = request("setup", {"setup_token": token, "password": password})
    assert status == 200, (status, data)
    cookie = headers["Set-Cookie"].split(";")[0]
    assert "Secure; HttpOnly; SameSite=Strict" in headers["Set-Cookie"]
    csrf = json.loads(data)["csrf"]
    assert request("setup", {"setup_token": token, "password": password})[0] == 403
    assert request("connection", {"host": "localhost"}, {"X-CSRF-Token": ""})[0] == 403
    assert request("state", headers={"Host": "other.test"})[0] == 403
    while time.monotonic() < deadline:
        status, data, _ = request("state")
        if status == 200 and json.loads(data)["ready"]:
            break
        time.sleep(1)
    else:
        raise AssertionError("Web interface could not connect to the real Bridge gRPC service")
    assert json.loads(data)["accounts"] == []
    assert request("certificate")[1].startswith(b"-----BEGIN CERTIFICATE-----")
    logs = docker("logs", name)
    assert token not in logs.stdout + logs.stderr
    assert password not in logs.stdout + logs.stderr
    admin = docker("exec", name, "cat", "/data/web/admin.json").stdout
    assert password not in admin
    assert request("logout", {})[0] == 200
    assert request("state")[0] == 401
    if run_browser:
        from browser import browser_checks

        browser_checks(origin, password)
    print(
        "Passed: HTTPS admin, one-time setup, CSRF, origin checks, "
        "real Bridge gRPC, secret isolation"
    )
