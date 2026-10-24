"""Render the legal pages from templates plus env config.

German is the legally binding version for a German operator; the English pages
are convenience translations for the English UI. The routes exist even when the
deployment is not configured yet; in that case the page shows a visible notice
instead of silently publishing incomplete legal information.
"""
from __future__ import annotations

import datetime
import html
import os
from pathlib import Path

from .site import DEFAULT_SUPPORT_LABEL, DEFAULT_SUPPORT_URL

_LEGAL_DIR = Path(__file__).parent / "static" / "legal"
_ENGLISH_PAGES = {"imprint", "privacy"}


def _value(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _escaped(value: str) -> str:
    return html.escape(value, quote=True)


def _address_html(value: str) -> str:
    return _escaped(value).replace("\n", "<br>")


def _email_link(lang: str) -> str:
    email = _value("LEGAL_EMAIL")
    if not email:
        return "[Add email]" if lang == "en" else "[E-Mail ergänzen]"
    return f'<a href="mailto:{_escaped(email)}">{_escaped(email)}</a>'


def _notice(missing: list[str], lang: str) -> str:
    if not missing:
        return ""
    fields = ", ".join(f"<code>{field}</code>" for field in missing)
    if lang == "en":
        return (
            '<div class="notice"><strong>Note:</strong> This page is not fully '
            f"configured yet. Missing: {fields}. Add them to the <code>.env</code> "
            "before the site is publicly reachable.</div>"
        )
    return (
        '<div class="notice"><strong>Hinweis:</strong> Diese Seite ist noch nicht '
        f"vollständig konfiguriert. Fehlend: {fields}. Bitte in der <code>.env</code> "
        "ergänzen, bevor die Seite öffentlich erreichbar ist.</div>"
    )


def _support_provider(lang: str) -> str:
    """Best-effort provider name for the privacy page, derived from the URL."""
    url = _value("SUPPORT_URL", DEFAULT_SUPPORT_URL).lower()
    if "ko-fi.com" in url:
        return "Ko-fi"
    if "buymeacoffee.com" in url:
        return "Buy Me a Coffee"
    if "github.com" in url:
        return "GitHub Sponsors"
    return "provider" if lang == "en" else "Anbieter"


def _support_section(lang: str) -> str:
    url = _value("SUPPORT_URL", DEFAULT_SUPPORT_URL)
    if not url:
        if lang == "en":
            return (
                "<p>No donation links or external payment services are currently "
                "embedded on this website.</p>"
            )
        return (
            "<p>Es werden derzeit keine Spenden-Links oder externen Zahlungsdienste "
            "eingebunden.</p>"
        )
    label = _value("SUPPORT_LABEL", DEFAULT_SUPPORT_LABEL)
    provider = _support_provider(lang)
    if lang == "en":
        return (
            "<p>This website contains a link to an external donation service "
            f"({_escaped(provider)}, “{_escaped(label)}”). Data is only transferred "
            "to the provider after you click the link; this includes your IP address. "
            "Processing on the provider's website is governed by that provider's "
            "privacy policy. The legal basis for embedding the link is our legitimate "
            "interest in funding the project (Art. 6(1)(f) GDPR).</p>"
        )
    return (
        "<p>Auf dieser Website finden Sie einen Link zu einem externen "
        f"Spenden-Dienst ({_escaped(provider)}, „{_escaped(label)}“). Erst wenn Sie "
        "diesen Link anklicken, werden Sie auf die Website des Anbieters "
        "weitergeleitet; dabei wird unter anderem Ihre IP-Adresse an den Anbieter "
        "übermittelt. Die Verarbeitung dort richtet sich nach den "
        "Datenschutzbestimmungen des jeweiligen Anbieters. Rechtsgrundlage für die "
        "Einbindung des Links ist unser berechtigtes Interesse an der Finanzierung "
        "des Projekts (Art. 6 Abs. 1 lit. f DSGVO).</p>"
    )


def _ads_section(lang: str) -> str:
    provider = _value("ADS_PROVIDER")
    if not provider:
        if lang == "en":
            return (
                "<p>No advertising networks, tracking services or analytics tools "
                "are currently used. No marketing or analytics cookies are set.</p>"
            )
        return (
            "<p>Es werden derzeit keine Werbenetzwerke, Tracking-Dienste oder "
            "Analyse-Tools eingesetzt. Es werden keine Marketing- oder "
            "Analyse-Cookies gesetzt.</p>"
        )
    if lang == "en":
        return (
            f"<p>To fund the project, ads from <strong>{_escaped(provider)}</strong> "
            "are embedded. The provider and the scope of data processed are set out "
            "in the consent you give when visiting the website. No ad scripts are "
            "loaded without consent. You may withdraw consent at any time with effect "
            "for the future.</p>"
        )
    return (
        f"<p>Zur Finanzierung werden Anzeigen von <strong>{_escaped(provider)}</strong> "
        "eingebunden. Anbieter und Umfang der dabei verarbeiteten Daten ergeben sich "
        "aus der Einwilligung, die Sie beim Aufruf der Website erteilen. Ohne "
        "Einwilligung werden keine Anzeigen-Skripte geladen. Sie können die "
        "Einwilligung jederzeit mit Wirkung für die Zukunft widerrufen.</p>"
    )


def render_legal(name: str) -> str:
    lang = "en" if name in _ENGLISH_PAGES else "de"
    template = (_LEGAL_DIR / f"{name}.html").read_text(encoding="utf-8")

    missing = []
    if not _value("LEGAL_NAME"):
        missing.append("LEGAL_NAME")
    if not _value("LEGAL_ADDRESS"):
        missing.append("LEGAL_ADDRESS")
    if not _value("LEGAL_EMAIL"):
        missing.append("LEGAL_EMAIL")

    vat_id = _value("LEGAL_VAT_ID")
    vat_label = "VAT ID" if lang == "en" else "Umsatzsteuer-ID"
    values = {
        "NOTICE": _notice(missing, lang),
        "NAME": _escaped(
            _value("LEGAL_NAME", "[Add name]" if lang == "en" else "[Name ergänzen]")
        ),
        "ADDRESS": _address_html(
            _value(
                "LEGAL_ADDRESS",
                "[Add address]" if lang == "en" else "[Anschrift ergänzen]",
            )
        ),
        "EMAIL_LINK": _email_link(lang),
        "VAT": f"<p>{vat_label}: {_escaped(vat_id)}</p>" if vat_id else "",
        "DOMAIN": _escaped(
            _value("DOMAIN", "this website" if lang == "en" else "diese Website")
        ),
        "UPDATED": _escaped(
            _value("LEGAL_UPDATED", datetime.date.today().strftime("%d.%m.%Y"))
        ),
        "HOSTING_PROVIDER": _escaped(
            _value(
                "HOSTING_PROVIDER",
                "the hosting provider for this website"
                if lang == "en"
                else "der Hosting-Anbieter dieser Website",
            )
        ),
        "LOG_RETENTION_DAYS": _escaped(_value("LOG_RETENTION_DAYS", "30")),
        "SUPPORT_SECTION": _support_section(lang),
        "ADS_SECTION": _ads_section(lang),
    }

    for key, value in values.items():
        template = template.replace("{{" + key + "}}", value)
    return template
