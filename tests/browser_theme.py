from playwright.sync_api import expect


def theme_checks(page, context, origin, output, cdp):
    coverage = []
    page.emulate_media(color_scheme="light")
    page.goto(origin)
    root = page.locator("html")
    select = page.locator("#gate-theme")
    wordmark = page.locator("#gate .brand-wordmark")
    expect(root).to_have_attribute("data-theme", "light")
    expect(select).to_have_value("system")
    expect(wordmark).to_have_css("background-color", "rgb(0, 0, 0)")
    expect(page.locator("#gate-title")).to_have_css("color", "rgb(0, 0, 0)")
    assert page.locator("#gate .brand-icon").evaluate("image => image.naturalWidth > 0")
    assert "<image" not in context.request.get(origin + "/icon.svg").text()
    page.screenshot(path=str(output / "web-sign-in-light.png"), full_page=True)
    select.select_option("dark")
    expect(root).to_have_attribute("data-theme", "dark")
    expect(wordmark).to_have_css("background-color", "rgb(255, 255, 255)")
    expect(page.locator("#gate-title")).to_have_css("color", "rgb(255, 255, 255)")
    expect(page.locator("#workspace-theme")).to_have_value("dark")
    page.screenshot(path=str(output / "web-sign-in-dark.png"), full_page=True)
    coverage += cdp.send("Profiler.takePreciseCoverage")["result"]
    page.reload()
    expect(select).to_have_value("dark")
    expect(root).to_have_attribute("data-theme", "dark")
    page.emulate_media(color_scheme="dark")
    page.emulate_media(color_scheme="light")
    expect(root).to_have_attribute("data-theme", "dark")
    select.select_option("light")
    page.emulate_media(color_scheme="dark")
    expect(root).to_have_attribute("data-theme", "light")
    select.select_option("system")
    expect(root).to_have_attribute("data-theme", "dark")
    page.emulate_media(color_scheme="light")
    expect(root).to_have_attribute("data-theme", "light")
    page.evaluate("localStorage.setItem('paperless-proton-theme', 'invalid')")
    coverage += cdp.send("Profiler.takePreciseCoverage")["result"]
    page.reload()
    expect(select).to_have_value("system")
    expect(root).to_have_attribute("data-theme", "light")
    select.select_option("dark")
    return coverage


def storage_disabled_checks(browser, origin):
    context = browser.new_context(ignore_https_errors=True, color_scheme="dark")
    context.add_init_script("""
        Object.defineProperty(window, 'localStorage', {
            get() { throw new DOMException('Storage is disabled', 'SecurityError'); }
        });
    """)
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    cdp = context.new_cdp_session(page)
    cdp.send("Profiler.enable")
    cdp.send("Profiler.startPreciseCoverage", {"callCount": True, "detailed": True})
    page.goto(origin)
    expect(page.locator("html")).to_have_attribute("data-theme", "dark")
    page.locator("#gate-theme").select_option("light")
    expect(page.locator("html")).to_have_attribute("data-theme", "light")
    expect(page.get_by_role("button", name="Sign in", exact=True)).to_be_visible()
    assert not errors, errors
    coverage = cdp.send("Profiler.takePreciseCoverage")["result"]
    context.close()
    return coverage
