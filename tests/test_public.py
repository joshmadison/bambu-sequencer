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


def test_about_support_link_follows_env(client, monkeypatch):
    monkeypatch.delenv("SUPPORT_URL", raising=False)
    page = client.get("/about").text
    assert "https://ko-fi.com/G4J729EFX3" in page

    monkeypatch.setenv("SUPPORT_URL", "")
    page = client.get("/about").text
    assert "ko-fi.com" not in page


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
