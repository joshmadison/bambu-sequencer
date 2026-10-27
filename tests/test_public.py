"""Public-deployment behaviour: legal pages, footer link and abuse limits."""
from app import middleware


def test_legal_pages_render(client):
    impressum = client.get("/impressum")
    assert impressum.status_code == 200
    assert "Impressum" in impressum.text
    assert "§ 5 DDG" in impressum.text

    datenschutz = client.get("/datenschutz")
    assert datenschutz.status_code == 200
    assert "Datenschutzerklärung" in datenschutz.text
    assert "Server-Logfiles" in datenschutz.text

    imprint = client.get("/imprint")
    assert imprint.status_code == 200
    assert "Legal notice" in imprint.text
    assert "convenience translation" in imprint.text

    privacy = client.get("/privacy")
    assert privacy.status_code == 200
    assert "Privacy policy" in privacy.text
    assert "Server log files" in privacy.text
    assert "convenience translation" in privacy.text

    css = client.get("/legal.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")


def test_support_button_is_injected(client, monkeypatch):
    monkeypatch.setenv("SUPPORT_URL", "https://buymeacoffee.com/example")
    monkeypatch.setenv("SUPPORT_LABEL", "Buy me a coffee")
    html = client.get("/").text
    assert 'window.APP_SUPPORT_URL = "https://buymeacoffee.com/example"' in html
    assert 'window.APP_SUPPORT_LABEL = "Buy me a coffee"' in html
    assert 'id="supportButton"' in html
    assert 'id="supportLink"' not in html
    assert 'href="/imprint"' in html
    assert 'href="/privacy"' in html


def test_support_button_is_on_by_default(client, monkeypatch):
    monkeypatch.delenv("SUPPORT_URL", raising=False)
    monkeypatch.delenv("SUPPORT_LABEL", raising=False)
    html = client.get("/").text
    assert 'window.APP_SUPPORT_URL = "https://ko-fi.com/G4J729EFX3"' in html
    assert 'window.APP_SUPPORT_LABEL = "Buy me a coffee"' in html


def test_support_button_can_be_hidden(client, monkeypatch):
    monkeypatch.setenv("SUPPORT_URL", "")
    html = client.get("/").text
    assert 'window.APP_SUPPORT_URL = ""' in html


def test_privacy_uses_default_support_link(client, monkeypatch):
    monkeypatch.delenv("SUPPORT_URL", raising=False)
    monkeypatch.delenv("SUPPORT_LABEL", raising=False)
    page = client.get("/privacy").text
    assert "Ko-fi" in page
    assert "Buy me a coffee" in page


def test_security_headers(client):
    response = client.get("/")
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in response.headers["content-security-policy"]


def test_verification_links_are_present(client):
    html = client.get("/").text
    assert "PRINTER_TEST_PROTOCOL.md" in html
    assert "printer-verification.yml" in html
    assert "Report" in html


def test_about_page(client):
    response = client.get("/about")
    assert response.status_code == 200
    assert "How PlateLoop works" in response.text
    assert "The problem it solves" in response.text
    assert "Create a sequence" in response.text
    assert 'href="/"' in response.text

    html = client.get("/").text
    assert 'href="/about"' in html


def test_about_has_no_support_button(client, monkeypatch):
    # Support stays a single, quiet entry point in the app header.
    monkeypatch.delenv("SUPPORT_URL", raising=False)
    page = client.get("/about").text
    assert "ko-fi.com" not in page
    assert "{{" not in page


def test_about_footer_links_english_legal_pages_only(client):
    page = client.get("/about").text
    footer = page[page.index("<footer>"):]
    assert 'href="/imprint"' in footer
    assert 'href="/privacy"' in footer
    assert 'href="/impressum"' not in footer
    assert 'href="/datenschutz"' not in footer


def test_rate_limit_returns_429(client, monkeypatch):
    monkeypatch.setattr(middleware, "RATE_LIMIT_REQUESTS", 1)
    monkeypatch.setattr(middleware, "RATE_LIMIT_WINDOW_S", 600)
    middleware.reset_limits()
    try:
        first = client.post(
            "/api/inspect",
            files={"file": ("broken.3mf", b"not a zip", "application/octet-stream")},
        )
        assert first.status_code == 400

        second = client.post(
            "/api/inspect",
            files={"file": ("broken.3mf", b"not a zip", "application/octet-stream")},
        )
        assert second.status_code == 429
        assert int(second.headers["retry-after"]) > 0
    finally:
        middleware.reset_limits()


def test_brand_assets_are_served(client):
    css = client.get("/assets/base.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert "Space Grotesk" in css.text

    for path, kind in [
        ("/assets/logo.svg", "image/svg+xml"),
        ("/assets/favicon.svg", "image/svg+xml"),
        ("/favicon.ico", "image/x-icon"),
        ("/apple-touch-icon.png", "image/png"),
        ("/assets/fonts/space-grotesk-latin.woff2", "font/woff2"),
        ("/assets/fonts/jetbrains-mono-latin.woff2", "font/woff2"),
    ]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert response.headers["content-type"].startswith(kind), path
        assert response.content, path

    font = client.get("/assets/fonts/space-grotesk-latin.woff2")
    assert "immutable" in font.headers["cache-control"]


def test_assets_are_allow_listed(client):
    for path in ["/assets/nope.css", "/assets/../main.py", "/assets/%2e%2e/main.py",
                 "/assets/fonts", "/assets/"]:
        assert client.get(path).status_code == 404, path


def test_pages_use_shared_brand(client):
    for path in ["/", "/about"]:
        html = client.get(path).text
        assert '/assets/base.css' in html, path
        assert '/assets/logo.svg' in html, path
        assert 'rel="icon"' in html, path
    assert '@import url("/assets/base.css")' in client.get("/legal.css").text
    assert 'rel="icon"' in client.get("/impressum").text
