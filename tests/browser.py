import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


def browser_checks(origin: str, password: str) -> None:
    with sync_playwright() as playwright:
        options = {"headless": True}
        if os.environ.get("CHROMIUM_EXECUTABLE"):
            options["executable_path"] = os.environ["CHROMIUM_EXECUTABLE"]
        browser = playwright.chromium.launch(**options)
        context = browser.new_context(
            ignore_https_errors=True, viewport={"width": 1440, "height": 1050}
        )
        page = context.new_page()
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        cdp = context.new_cdp_session(page)
        cdp.send("Profiler.enable")
        cdp.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})
        page.goto(origin)
        page.get_by_label("Admin password", exact=True).fill("incorrect-password")
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.locator("#notice")).to_contain_text("Invalid password")
        page.get_by_label("Admin password", exact=True).fill(password)
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.get_by_role("heading", name="From inbox to archive.")).to_be_visible()
        expect(page.locator("#service-badge")).to_have_text("Bridge running", timeout=30000)
        output = Path(".local/verification")
        output.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(output / "web-overview.png"), full_page=True)
        page.get_by_role("link", name="Bridge connection", exact=False).click()
        page.get_by_label("Hostname or IP address").fill("mail.example.test")
        page.get_by_role("button", name="Save address").click()
        expect(page.locator("#restart-notice")).to_be_visible()
        with page.expect_download() as download:
            page.get_by_role("link", name="Download certificate", exact=False).click()
        assert download.value.suggested_filename == "proton-bridge-ca.crt"
        page.get_by_label("Hostname or IP address").fill("localhost")
        page.get_by_role("button", name="Save address").click()
        expect(page.locator("#restart-notice")).to_be_hidden()
        baseline = page.evaluate("fetch('/api/state').then(response => response.json())")
        fixture = baseline | {
            "accounts": [
                {
                    "id": "test-account",
                    "username": "Example account",
                    "addresses": ["mail@example.test"],
                    "connected": True,
                    "split": False,
                }
            ],
            "login": {"step": "idle"},
        }
        page.route("**/api/state", lambda route: route.fulfill(json=fixture))
        page.route(
            "**/api/proton/credentials",
            lambda route: route.fulfill(
                json={"username": "mail@example.test", "password": "test-bridge-password"}
            ),
        )
        page.route(
            "**/api/proton/login",
            lambda route: (
                fixture.update(login={"step": "two_factor", "username": "example"}),
                route.fulfill(json={}),
            ),
        )
        page.route(
            "**/api/proton/answer",
            lambda route: (
                fixture.update(login={"step": "mailbox_password", "username": "example"}),
                route.fulfill(json={}),
            ),
        )
        page.route(
            "**/api/proton/cancel",
            lambda route: (fixture.update(login={"step": "idle"}), route.fulfill(json={})),
        )
        page.route(
            "**/api/proton/logout",
            lambda route: (fixture.update(accounts=[]), route.fulfill(json={})),
        )
        page.get_by_role("link", name="Proton account", exact=False).click()
        expect(page.get_by_text("Example account", exact=True)).to_be_visible(timeout=10000)
        page.get_by_role("button", name="View Bridge credentials").click()
        expect(page.locator("#bridge-password")).to_have_value("test-bridge-password")
        page.get_by_role("button", name="Show password").click()
        expect(page.locator("#bridge-password")).to_have_attribute("type", "text")
        page.get_by_role("button", name="Hide password").click()
        page.get_by_role("button", name="Close", exact=True).click()
        expect(page.locator("#bridge-password")).to_have_value("")
        page.get_by_label("Proton username or email").fill("example")
        page.get_by_label("Proton password", exact=True).fill("test-password")
        page.get_by_role("button", name="Connect account").click()
        expect(page.get_by_label("Authenticator code")).to_be_visible()
        page.get_by_label("Authenticator code").fill("123456")
        page.locator("#challenge-form").get_by_role("button", name="Continue").click()
        expect(page.get_by_label("Mailbox password", exact=True)).to_be_visible()
        page.get_by_role("button", name="Cancel sign-in").click()
        expect(page.locator("#proton-form")).to_be_visible()
        fixture["login"] = {"step": "waiting", "username": "example"}
        expect(page.locator("#login-progress")).to_be_visible(timeout=10000)
        fixture["login"] = {
            "step": "verification",
            "username": "example",
            "url": "https://verify.proton.me/?token=test",
        }
        expect(page.locator("#verification-link")).to_be_visible(timeout=10000)
        fixture["login"] = {"step": "error", "error": "Invalid authenticator code"}
        expect(page.locator("#login-error")).to_contain_text(
            "Invalid authenticator code", timeout=10000
        )
        page.get_by_role("button", name="Disconnect", exact=True).click()
        page.get_by_role("dialog").get_by_role("button", name="Cancel").click()
        original_accounts = list(fixture["accounts"])
        fixture["accounts"].append(
            {
                "id": "signed-out",
                "username": "Disconnected account",
                "addresses": [],
                "connected": False,
                "split": False,
            }
        )
        expect(page.get_by_text("Disconnected account", exact=True)).to_be_visible(timeout=10000)
        fixture["accounts"] = original_accounts
        page.get_by_role("link", name="Paperless setup", exact=False).click()
        page.get_by_label("Paperless URL").fill("https://paperless.example.test")
        page.get_by_label("API token", exact=True).fill("test-paperless-api-token-value")
        page.get_by_label("Proton email address").select_option(label="mail@example.test")
        page.get_by_role("button", name="Save settings", exact=True).click()
        expect(page.locator("#notice")).to_have_text("Paperless settings saved.")
        expect(page.get_by_label("API token", exact=True)).to_have_value("")
        page.route(
            "**/api/paperless/test",
            lambda route: route.fulfill(json={"message": "Test connection successful."}),
        )
        page.route(
            "**/api/paperless/apply",
            lambda route: route.fulfill(
                json={"message": "Email account and import rule saved in Paperless."}
            ),
        )
        page.get_by_role("button", name="Test connection").click()
        expect(page.locator("#paperless-result")).to_have_text("Test connection successful.")
        page.get_by_role("button", name="Apply to Paperless", exact=False).click()
        page.get_by_role("dialog").get_by_role("button", name="Cancel").click()
        page.get_by_role("button", name="Apply to Paperless", exact=False).click()
        page.get_by_role("dialog").get_by_role("button", name="Continue").click()
        expect(page.locator("#paperless-result")).to_have_text(
            "Email account and import rule saved in Paperless."
        )
        page.route(
            "**/api/paperless/test",
            lambda route: route.fulfill(
                status=400, json={"error": "Paperless certificate is not trusted"}
            ),
        )
        page.get_by_role("button", name="Test connection").click()
        expect(page.locator("#paperless-result")).to_have_text(
            "Paperless certificate is not trusted"
        )
        fixture["settings"]["rule_id"] = 1
        fixture["ready"] = False
        expect(page.locator("#service-badge")).to_have_text("Bridge starting", timeout=10000)
        fixture["ready"] = True
        expect(page.locator("#service-badge")).to_have_text("Bridge running", timeout=10000)
        page.screenshot(path=str(output / "web-paperless.png"), full_page=True)
        page.set_viewport_size({"width": 390, "height": 844})
        for section in ("overview", "proton", "connection", "paperless", "security"):
            page.goto(origin + "/#" + section)
            expect(page.locator("#" + section)).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        page.screenshot(path=str(output / "web-mobile.png"), full_page=True)
        page.get_by_label("Current password", exact=True).fill(password)
        page.get_by_label("New password", exact=True).fill(password + "-changed")
        page.get_by_role("button", name="Change password", exact=True).click()
        expect(page.locator("#gate")).to_be_visible()
        page.get_by_label("Admin password", exact=True).fill(password + "-changed")
        page.get_by_role("button", name="Sign in", exact=True).click()
        expect(page.locator("#workspace")).to_be_visible()
        page.get_by_role("button", name="Sign out", exact=False).click()
        expect(page.locator("#gate")).to_be_visible()
        page.route(
            "**/api/session",
            lambda route: route.fulfill(
                json={"configured": False, "authenticated": False, "csrf": ""}
            ),
        )
        page.route("**/api/setup", lambda route: route.fulfill(json={"csrf": "test-csrf"}))
        coverage = cdp.send("Profiler.takePreciseCoverage")["result"]
        page.reload()
        expect(page.get_by_role("heading", name="Make yourself at home.")).to_be_visible()
        page.get_by_label("Setup token", exact=True).fill("test-setup-token")
        page.get_by_label("Admin password", exact=True).fill("test-bootstrap-password")
        page.get_by_role("button", name="Create workspace").click()
        expect(page.locator("#workspace")).to_be_visible()
        coverage += cdp.send("Profiler.takePreciseCoverage")["result"]
        (output / "browser-coverage.json").write_text(
            json.dumps([item for item in coverage if item["url"].endswith("/app.js")])
        )
        assert not errors, errors
        context.close()
        browser.close()
        print(
            "Passed: browser setup flows, secret clearing, forms, confirmation, "
            "mobile layout, password rotation"
        )
