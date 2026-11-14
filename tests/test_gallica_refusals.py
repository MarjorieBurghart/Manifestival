# -*- coding: utf-8 -*-
"""Gallica (adresses, API .highres), protections reconnues, journal des refus."""
from __future__ import annotations

from datetime import datetime, timedelta
import json

import pytest

from iiif_test_server import IIIFServer

from manifestival import gallica, images, net
from manifestival.manifest import ImageService, Page
from manifestival.refusals import RefusalLog, is_refusal
from manifestival.sources import load_json, normalize

DOC = "btv1b10510386g"
MANIFEST = f"https://gallica.bnf.fr/iiif/ark:/12148/{DOC}/manifest.json"


@pytest.mark.parametrize("address", [
    f"https://gallica.bnf.fr/ark:/12148/{DOC}",
    f"https://gallica.bnf.fr/ark:/12148/{DOC}/f3.item",
    f"https://gallica.bnf.fr/ark:/12148/{DOC}/f12.image.r=psautier?lang=fr",
    f"gallica.bnf.fr/ark:/12148/{DOC}/f1.highres",
    f"http://gallica.bnf.fr/ark:/12148/{DOC}.texteImage",
    f"https://gallica.bnf.fr/iiif/ark:/12148/{DOC}/f1/full/full/0/native.jpg",
    f"https://gallica.bnf.fr/view3if/ga/ark:/12148/{DOC}",          # visionneuse
    f"https://gallica.bnf.fr/view3if/ga/ark:/12148/{DOC}/f5.item",
])
def test_gallica_pages_lead_to_the_manifest(address):
    assert normalize(address) == MANIFEST


def test_other_addresses_are_untouched():
    assert normalize(MANIFEST) == MANIFEST
    assert gallica.manifest_url("https://gallica.bnf.fr/services/engine/search/sru?q=x") is None
    assert normalize("https://example.org/ark:/12148/x") == "https://example.org/ark:/12148/x"


def test_full_size_gallica_images_use_highres_first():
    page = Page(index=3, label="f. 2r", service=ImageService(f"https://gallica.bnf.fr/iiif/ark:/12148/{DOC}/f3", 1))
    urls = images.image_urls(page, "max")
    assert urls[0] == f"https://gallica.bnf.fr/ark:/12148/{DOC}/f3.highres"
    assert urls[1].endswith("/f3/full/full/0/native.jpg")
    # Taille réduite : l'image IIIF réduite, pas .highres.
    assert not any(u.endswith(".highres") for u in images.image_urls(page, 1500))
    other = Page(index=1, label="", service=ImageService("https://example.org/iiif/x", 3))
    assert not any(u.endswith(".highres") for u in images.image_urls(other))


# ------------------------------------------------------------------ protections

@pytest.mark.parametrize("headers, body, name", [
    ({"Server": "cloudflare"}, b"", "Cloudflare"),
    ({"cf-mitigated": "challenge"}, b"", "Cloudflare"),
    ({"Server": "AkamaiGHost"}, b"", "Akamai"),
    ({"X-Iinfo": "1-2-3"}, b"", "Imperva"),
    ({"X-DataDome": "protected"}, b"", "DataDome"),
    ({"X-Sucuri-ID": "1"}, b"", "Sucuri"),
    ({"x-amzn-waf-action": "block"}, b"", "AWS WAF"),
    ({}, b"<html><title>Making sure you're not a bot!</title>Anubis</html>", "Anubis"),
    ({}, b"<title>Just a moment...</title>", "Cloudflare"),
    ({"Server": "nginx"}, b"<html>Forbidden</html>", ""),
])
def test_protections_are_recognised(headers, body, name):
    assert net.protection_of(headers, body) == name


def test_challenge_page_instead_of_json_is_a_block(monkeypatch):
    def fake(url, **kw):
        return net.Response(b"<html><title>Making sure you're not a bot!</title>anubis</html>", "text/html", url)
    with pytest.raises(net.FetchError) as err:
        load_json("https://iiif.example.org/manifest.json", fetch=fake)
    assert err.value.kind == "blocked" and err.value.protection == "Anubis"


def test_protection_name_travels_with_the_error():
    with IIIFServer() as server:
        server.cloudflare = True
        with pytest.raises(net.FetchError) as err:
            net.fetch(server.base + "/iiif/img1/full/max/0/default.jpg")
    assert err.value.kind == "blocked" and err.value.protection == "Cloudflare"


# ------------------------------------------------------------------ journal

def _blocked(url="https://gallica.bnf.fr/iiif/ark:/12148/x/manifest.json"):
    return net.FetchError("blocked", url, 403, protection="Cloudflare")


def test_only_refusals_are_recorded(tmp_path):
    assert is_refusal(_blocked())
    assert is_refusal(net.FetchError("http", "https://e.org/x", 429))
    assert is_refusal(net.FetchError("unreachable", "https://e.org/x"))
    assert not is_refusal(net.FetchError("http", "https://e.org/x", 404))
    assert not is_refusal(net.FetchError("cancelled", "https://e.org/x"))
    assert not is_refusal(OSError("disque plein"))
    log = RefusalLog(tmp_path / "r.json")
    assert log.record(net.FetchError("timeout", "https://e.org/x"), "download") is None
    assert len(log) == 0


def test_journal_is_kept_merged_and_copied(tmp_path):
    path = tmp_path / "data" / "refusals.json"
    log = RefusalLog(path)
    now = datetime(2026, 11, 14, 10, 0, 5)
    log.record(_blocked(), "manifest", MANIFEST, now=now)
    log.record(_blocked(), "manifest", MANIFEST, now=now + timedelta(seconds=20))   # même minute
    log.record(net.FetchError("http", "https://iiif.other.org/img/1", 403), "download",
               "https://iiif.other.org/m.json", now=now + timedelta(minutes=3))
    assert len(log) == 2 and log.items[0].count == 2
    again = RefusalLog(path)          # relu depuis le fichier
    assert [r.host for r in again.items] == ["gallica.bnf.fr", "iiif.other.org"]
    text = again.as_text()
    assert "gallica.bnf.fr  403 [Cloudflare]  manifest ×2" in text
    assert "iiif.other.org  403  download" in text and "manifest : https://iiif.other.org/m.json" in text
    again.clear()
    assert RefusalLog(path).items == []


def test_journal_limit_and_damaged_file(tmp_path):
    log = RefusalLog(tmp_path / "r.json", limit=3)
    for i in range(5):
        log.record(net.FetchError("http", f"https://h{i}.org/x", 403), "download")
    assert [r.host for r in log.items] == ["h2.org", "h3.org", "h4.org"]
    (tmp_path / "bad.json").write_text("{pas du json", encoding="utf-8")
    assert RefusalLog(tmp_path / "bad.json").items == []
    assert json.loads((tmp_path / "r.json").read_text(encoding="utf-8"))["format"] == "manifestival-refusals"


def test_challenge_message_does_not_show_code_200():
    from manifestival.i18n import Translator
    from manifestival.ui.texts import error_text
    text = error_text(net.FetchError("blocked", MANIFEST, 200, protection="Cloudflare"), Translator("fr"))
    assert "page de vérification" in text and "200" not in text and "Cloudflare" in text
    text = error_text(net.FetchError("blocked", MANIFEST, 403, protection="Cloudflare"), Translator("fr"))
    assert "code 403" in text


def test_gallica_thumbnails_use_the_iiif_api_first():
    page = Page(index=1, label="", service=ImageService(f"https://gallica.bnf.fr/iiif/ark:/12148/{DOC}/f1", 1),
                thumbnail_url=f"https://gallica.bnf.fr/ark:/12148/{DOC}/f1.thumbnail")
    urls = images.thumbnail_urls(page)
    assert urls[0] == f"https://gallica.bnf.fr/iiif/ark:/12148/{DOC}/f1/full/!240,240/0/native.jpg"
    assert urls.index(page.thumbnail_url) > 0
    other = Page(index=1, label="", service=ImageService("https://e.org/iiif/x", 3),
                 thumbnail_url="https://e.org/thumb.jpg")
    assert images.thumbnail_urls(other)[0] == "https://e.org/thumb.jpg"


def test_a_blocked_address_does_not_stop_the_other_official_ones(tmp_path):
    from manifestival import download as dl
    page = Page(index=3, label="f3", service=ImageService(f"https://gallica.bnf.fr/iiif/ark:/12148/{DOC}/f3", 1))
    calls = []

    def web(url, **kw):
        calls.append(url)
        if url.endswith(".highres"):
            raise net.FetchError("blocked", url, 403, protection="Cloudflare")
        return net.Response(b"\xff\xd8\xff\xe0" + bytes(16), "image/jpeg", url)
    results = dl.download([(page, "f3")], tmp_path, fetch=web, sleep=lambda s: None,
                          pacer=dl.Pacer(clock=lambda: 0.0, sleep=lambda s: None))
    assert results[0].status == "ok" and calls[0].endswith(".highres")
    assert calls[1].endswith("/f3/full/full/0/native.jpg")
    # Toutes les adresses refusées : le refus est rendu, et le téléchargement s'arrête.
    calls.clear()
    blocked = lambda url, **kw: (_ for _ in ()).throw(net.FetchError("blocked", url, 403, protection="Cloudflare"))  # noqa: E731
    results = dl.download([(page, "a"), (page, "b")], tmp_path, fetch=blocked, sleep=lambda s: None,
                          pacer=dl.Pacer(clock=lambda: 0.0, sleep=lambda s: None))
    assert [r.error.kind for r in results] == ["blocked", "blocked"]
