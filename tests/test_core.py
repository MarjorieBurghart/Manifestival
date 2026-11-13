# -*- coding: utf-8 -*-
"""Lecture des manifests, adresses d'images, noms de fichiers, téléchargement
(sans Qt)."""
from __future__ import annotations

import csv
import json
import threading

import pytest

from iiif_test_server import FAKE_JPEG, IIIFServer, collection, manifest_v2, manifest_v3

from manifestival import download as dl
from manifestival import images, naming, net
from manifestival.manifest import (ManifestError, Page, collection_entries, is_collection,
                                       parse_manifest, safe_html, text_of)
from manifestival.sources import SourceError, load_json, normalize

BASE = "https://example.org"


# ------------------------------------------------------------------ manifest

def test_v3_manifest_in_the_chosen_language():
    m = parse_manifest(manifest_v3(BASE), "src", "fr")
    assert m.version == 3 and m.label == "Psautier de test"
    assert len(m.pages) == 5 and m.canvas_count == 5
    assert m.pages[0].label == "f. 1r" and m.pages[0].index == 1
    assert m.pages[0].service.url == f"{BASE}/iiif/img1" and m.pages[0].service.version == 3
    assert m.pages[0].width == 2000 and m.pages[0].meta("folio") == "1"
    assert ("Cote", "Ms. lat. 1234") in m.metadata
    assert m.meta("Date") == "XIIIe siècle"
    assert m.required_statement == ("Attribution", "Test Library")
    assert m.rights.startswith("http://creativecommons.org")
    english = parse_manifest(manifest_v3(BASE), "src", "en")
    assert english.label == "Test psalter" and english.pages[4].label == "Binding / spine"
    # Langue absente : repli sur l'anglais.
    assert parse_manifest(manifest_v3(BASE), "src", "ja").label == "Test psalter"


def test_v2_manifest():
    m = parse_manifest(manifest_v2(BASE), "src", "fr")
    assert m.version == 2 and m.label == "Livre d'heures"
    assert [p.label for p in m.pages] == ["p. 1", "p. 2", "p. 3"]
    assert m.pages[0].service.version == 2 and m.pages[0].service.url.endswith("/iiif/v2img1")
    assert m.meta("Langue") == "latin"
    assert m.required_statement == ("", "Bibliothèque de test")
    assert m.summary == "Un manuscrit de test."
    assert parse_manifest(manifest_v2(BASE), "", "en").meta("Langue") == "Latin"


def test_canvases_without_image_keep_their_position():
    data = manifest_v3(BASE)
    data["items"][1]["items"] = []
    m = parse_manifest(data)
    assert [p.index for p in m.pages] == [1, 3, 4, 5]
    assert m.canvas_count == 5


def test_v3_choice_and_canvas_thumbnail():
    data = manifest_v3(BASE, 1)
    body = data["items"][0]["items"][0]["items"][0]["body"]
    data["items"][0]["items"][0]["items"][0]["body"] = {"type": "Choice", "items": [body]}
    data["items"][0]["thumbnail"] = [{"id": f"{BASE}/thumb1.jpg", "type": "Image"}]
    page = parse_manifest(data).pages[0]
    assert page.service.url == f"{BASE}/iiif/img1"
    assert images.thumbnail_urls(page)[0] == f"{BASE}/thumb1.jpg"


def test_only_web_addresses_are_kept():
    data = manifest_v3(BASE, 1)
    body = data["items"][0]["items"][0]["items"][0]["body"]
    body["id"] = "file:///etc/passwd"
    body["service"] = [{"id": "file:///etc", "type": "ImageService3"}]
    with pytest.raises(ManifestError) as err:
        parse_manifest(data)
    assert err.value.kind == "no_images"


@pytest.mark.parametrize("data, kind", [([], "not_json_object"), ({"a": 1}, "not_manifest"),
                                         ({"items": []}, "no_images")])
def test_manifest_errors(data, kind):
    with pytest.raises(ManifestError) as err:
        parse_manifest(data)
    assert err.value.kind == kind


def test_collections():
    data = collection(BASE)
    assert is_collection(data) and not is_collection(manifest_v3(BASE))
    assert collection_entries(data) == [("Psalter", f"{BASE}/manifest3.json", False),
                                        ("Hours", f"{BASE}/manifest2.json", False)]


def test_html_values_are_sanitized():
    html = safe_html('<p>A <b>bold</b> <a href="javascript:x()">x</a> <a href="https://e.org">y</a>'
                     '<img src="https://e.org/i.png"><script>alert(1)</script></p>')
    assert "<b>bold</b>" in html and '<a href="https://e.org">' in html
    assert "javascript" not in html and "<img" not in html and "alert" not in html
    assert safe_html("a < b\nc") == "a &lt; b<br>c"
    assert safe_html("<b>x</b> and more") == "&lt;b&gt;x&lt;/b&gt; and more"  # pas du HTML


def test_text_of_variants():
    assert text_of({"en": ["a", "b"]}) == "a; b"
    assert text_of([{"@value": "x", "@language": "de"}], "fr") == "x"
    assert text_of(None) == "" and text_of(12) == "12"


# ------------------------------------------------------------------ adresses

def test_image_addresses():
    v3 = parse_manifest(manifest_v3(BASE)).pages[0]
    assert images.image_urls(v3, "max")[0] == f"{BASE}/iiif/img1/full/max/0/default.jpg"
    assert images.image_urls(v3, 1500)[0] == f"{BASE}/iiif/img1/full/!1500,1500/0/default.jpg"
    # Image plus petite que la taille demandée : la taille maximale suffit.
    assert images.image_urls(v3, 3000)[0] == f"{BASE}/iiif/img1/full/max/0/default.jpg"
    v2 = parse_manifest(manifest_v2(BASE)).pages[0]
    assert images.image_urls(v2, "max")[:2] == [f"{BASE}/iiif/v2img1/full/full/0/default.jpg",
                                                f"{BASE}/iiif/v2img1/full/max/0/default.jpg"]
    assert images.thumbnail_urls(v2)[0] == f"{BASE}/iiif/v2img1/full/!240,240/0/default.jpg"
    bare = Page(index=1, label="", image_url=f"{BASE}/a.png")
    assert images.image_urls(bare) == [f"{BASE}/a.png"]


def test_image_api_1_services_use_native_quality():
    from manifestival.manifest import image_service
    svc = image_service({"@id": "https://g.fr/iiif/ark:/1/f1",
                         "profile": "http://library.stanford.edu/iiif/image-api/1.1/compliance.html#level2"})
    assert svc.version == 1
    page = Page(index=1, label="", service=svc)
    assert images.image_urls(page)[0] == "https://g.fr/iiif/ark:/1/f1/full/full/0/native.jpg"
    assert image_service([{"@id": "https://a/auth", "@type": "AuthCookieService1"}]) is None


def test_extensions():
    assert images.extension_for("image/png", b"") == ".png"
    assert images.extension_for("application/octet-stream", FAKE_JPEG) == ".jpg"
    assert images.extension_for("", b"", "https://x/y/img.TIFF") == ".tif"
    assert images.extension_for("", b"") == ".jpg"


# ------------------------------------------------------------------ noms

def _page(i, label, **kw):
    return Page(index=i, label=label, **kw)


def test_default_names_with_shelfmark_prefix():
    opts = naming.NamingOptions(prefix="BnF lat. 1234")
    assert naming.render(_page(7, "f. 1r"), None, opts, 250) == "BnF_lat_1234_007_f_1r"
    assert naming.render(_page(7, "f. 1r"), None, opts, 1200) == "BnF_lat_1234_0007_f_1r"


def test_empty_parts_leave_no_stray_separator():
    opts = naming.NamingOptions(prefix="", template="{prefix}_{n}_{label}")
    assert naming.render(_page(3, ""), None, opts, 10) == "003"
    assert naming.render(_page(3, ""), None, naming.NamingOptions(template="{label}"), 10) == "image_003"


def test_forbidden_characters_spaces_and_accents():
    opts = naming.NamingOptions(template="{prefix}-{label}", prefix="Médiathèque", separator="-",
                                strip_accents=True)
    assert naming.render(_page(1, 'Fol. 3v / 4r: "détail"?'), None, opts, 9) == "Mediatheque-Fol-3v-4r-detail"
    keep = naming.NamingOptions(template="{label}", replace_spaces=False)
    assert naming.render(_page(1, "f. 1r"), None, keep, 9) == "f. 1r"
    assert naming.render(_page(1, "CON"), None, naming.NamingOptions(template="{label}"), 9) == "CON_"
    assert naming.render(_page(1, "日本 語"), None, naming.NamingOptions(template="{label}",
                                                                      strip_accents=True), 9) == "日本_語"


def test_metadata_and_title_tags():
    m = parse_manifest(manifest_v3(BASE), "", "fr")
    opts = naming.NamingOptions(template="{title}_{meta:Folio}_{meta:cote}")
    assert naming.render(m.pages[1], m, opts) == "Psautier_de_test_2_Ms_lat_1234"
    assert naming.build_template(["prefix", "n", "meta"], "-", "Folio") == "{prefix}-{n}-{meta:Folio}"


def test_unique_names():
    assert naming.unique_stems(["a", "A", "a", "a_2"]) == ["a", "A_2", "a_3", "a_2_2"]
    pages = [_page(1, "f. 1"), _page(2, "f. 1")]
    assert naming.file_stems(pages, None, naming.NamingOptions(template="{label}")) == ["f_1", "f_1_2"]


def test_prefix_suggestions_put_shelfmarks_first():
    m = parse_manifest(manifest_v3(BASE), "", "en")
    suggestions = naming.prefix_suggestions(m)
    assert suggestions[0] == "Ms. lat. 1234" and "Test psalter" in suggestions


# ------------------------------------------------------------------ téléchargement

class FakeWeb:
    """Remplace net.fetch : ``answers[url]`` est une liste de réponses
    (octets) ou d'erreurs, consommées dans l'ordre."""

    def __init__(self, answers=None, default=FAKE_JPEG):
        self.answers = answers or {}
        self.default = default
        self.calls = []

    def __call__(self, url, **kw):
        self.calls.append(url)
        queue = self.answers.get(url)
        answer = queue.pop(0) if queue else self.default
        if isinstance(answer, Exception):
            raise answer
        if answer is None:
            raise net.FetchError("http", url, 404)
        return net.Response(answer, "image/jpeg", url)


def _jobs(n=3):
    m = parse_manifest(manifest_v3(BASE, n), "", "fr")
    opts = naming.NamingOptions(prefix="Ms")
    return m, list(zip(m.pages, naming.file_stems(m.pages, m, opts)))


def test_download_writes_named_files(tmp_path):
    m, jobs = _jobs()
    seen = []
    results = dl.download(jobs, tmp_path, fetch=FakeWeb(), sleep=lambda s: None,
                          progress=lambda d, t, r: seen.append((d, t, r.status)))
    assert [r.status for r in results] == ["ok"] * 3
    assert sorted(p.name for p in tmp_path.iterdir()) == ["Ms_001_f_1r.jpg", "Ms_002_f_1v.jpg",
                                                          "Ms_003_f_2r.jpg"]
    assert seen[-1] == (3, 3, "ok")
    assert not list(tmp_path.glob("*.part"))


def test_retry_after_server_overload_then_next_address(tmp_path):
    data = manifest_v3(BASE, 1)
    data["items"][0]["items"][0]["items"][0]["body"]["id"] = f"{BASE}/direct/img1.jpg"
    m = parse_manifest(data)
    jobs = [(m.pages[0], "x")]
    url = images.image_urls(m.pages[0])[0]
    busy = net.FetchError("http", url, 503, retry_after=0)
    web = FakeWeb({url: [busy, busy, None]})
    waits = []
    results = dl.download(jobs, tmp_path, fetch=web, sleep=lambda s: None, notify=waits.append)
    assert results[0].status == "ok"
    # Deux attentes, puis 404 : on passe à l'adresse suivante (l'image elle-même).
    assert waits == [0, 0] and web.calls[-1] == m.pages[0].image_url


def test_forbidden_image_is_reported(tmp_path):
    m, jobs = _jobs(1)
    denied = net.FetchError("http", "", 403)
    web = FakeWeb({u: [denied] for u in images.image_urls(m.pages[0])})
    results = dl.download(jobs, tmp_path, fetch=web, sleep=lambda s: None)
    assert results[0].status == "failed" and results[0].error.status == 403


def test_html_instead_of_image_is_refused(tmp_path):
    m, jobs = _jobs(1)

    def web(url, **kw):
        return net.Response(b"<html>login</html>", "text/html", url)
    results = dl.download(jobs, tmp_path, fetch=web, sleep=lambda s: None)
    assert results[0].status == "failed" and results[0].error.kind == "not_image"
    assert not list(tmp_path.iterdir())


def test_existing_files_skip_replace_or_keep_both(tmp_path):
    m, jobs = _jobs(1)
    (tmp_path / "Ms_001_f_1r.jpg").write_bytes(b"old")
    r = dl.download(jobs, tmp_path, fetch=FakeWeb(), if_exists=dl.SKIP)[0]
    assert r.status == "skipped" and (tmp_path / "Ms_001_f_1r.jpg").read_bytes() == b"old"
    r = dl.download(jobs, tmp_path, fetch=FakeWeb(), if_exists=dl.KEEP_BOTH)[0]
    assert r.path.name == "Ms_001_f_1r_2.jpg"
    r = dl.download(jobs, tmp_path, fetch=FakeWeb(), if_exists=dl.REPLACE)[0]
    assert r.status == "ok" and (tmp_path / "Ms_001_f_1r.jpg").read_bytes() == FAKE_JPEG


def test_cancel_stops_before_the_next_image(tmp_path):
    m, jobs = _jobs(3)
    cancel = threading.Event()
    results = dl.download(jobs, tmp_path, fetch=FakeWeb(), cancel=cancel,
                          progress=lambda d, t, r: cancel.set())
    assert [r.status for r in results] == ["ok", "cancelled", "cancelled"]


def test_index_files(tmp_path):
    m, jobs = _jobs(2)
    results = dl.download(jobs, tmp_path, fetch=FakeWeb())
    csv_path, json_path = dl.write_index(tmp_path, results, m, "Ms", ";")
    assert csv_path.name == "Ms_images.csv" and json_path.name == "Ms_manifest.json"
    rows = list(csv.reader(csv_path.open(encoding="utf-8-sig"), delimiter=";"))
    assert rows[0] == list(dl.INDEX_COLUMNS)
    assert rows[1][:4] == ["Ms_001_f_1r.jpg", "1", "f. 1r", "ok"] and "Folio: 1" in rows[1][6]
    assert json.loads(json_path.read_text(encoding="utf-8"))["type"] == "Manifest"


# ------------------------------------------------------------------ sources et réseau

def test_normalize_addresses(tmp_path):
    assert normalize("  gallica.bnf.fr/iiif/ark:/x/manifest.json ") == "https://gallica.bnf.fr/iiif/ark:/x/manifest.json"
    assert normalize("http://a.b/c") == "http://a.b/c"
    f = tmp_path / "m.json"
    f.write_text("{}")
    assert normalize(str(f)) == str(f)


def test_load_json_from_file_and_errors(tmp_path):
    f = tmp_path / "m.json"
    f.write_text(json.dumps(manifest_v2(BASE)), encoding="utf-8")
    assert load_json(str(f))["@type"] == "sc:Manifest"
    assert load_json(f.as_uri())["@type"] == "sc:Manifest"
    with pytest.raises(SourceError) as err:
        load_json(str(tmp_path / "missing.json"))
    assert err.value.kind == "not_found"
    with pytest.raises(SourceError):
        load_json("")


def test_real_http_server():
    with IIIFServer() as server:
        data = load_json(server.base + "/manifest3.json")
        assert data["type"] == "Manifest"
        with pytest.raises(SourceError) as err:
            load_json(server.base + "/page.html")
        assert err.value.kind == "not_json"
        with pytest.raises(net.FetchError) as err:
            net.fetch(server.base + "/missing")
        assert err.value.status == 404 and not err.value.retryable
        server.failures["img1"] = [429]
        with pytest.raises(net.FetchError) as err:
            net.fetch(server.base + "/iiif/img1/full/max/0/default.jpg")
        assert err.value.status == 429 and err.value.retryable and err.value.retry_after == 0
        with pytest.raises(net.FetchError) as err:
            net.fetch(server.base + "/iiif/img1/full/max/0/default.jpg", max_bytes=4)
        assert err.value.kind == "too_large"
        with pytest.raises(net.FetchError) as err:
            net.fetch("file:///etc/passwd")
        assert err.value.kind == "not_web"
