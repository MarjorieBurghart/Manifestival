# -*- coding: utf-8 -*-
"""Respect des serveurs : identification, protections anti-robots, rythme."""
from __future__ import annotations

import threading

import pytest

from iiif_test_server import FAKE_JPEG, IIIFServer, manifest_v3

from manifestival import download as dl
from manifestival import naming, net
from manifestival.manifest import ImageService, Page, parse_manifest
from manifestival.politeness import Pacer, is_full_size, rule_for

G = "https://gallica.bnf.fr/iiif/ark:/12148/btv1b10510386g/f1"


def test_user_agent_names_the_application_and_its_project():
    assert net.USER_AGENT.startswith("Manifestival/")
    assert "github.com/MarjorieBurghart/Manifestival" in net.USER_AGENT
    assert "python" not in net.USER_AGENT.lower()


def test_cloudflare_refusal_is_a_block_and_is_not_retried():
    assert net.is_bot_block(403, {"Server": "cloudflare"})
    assert net.is_bot_block(403, {"CF-RAY": "abc"})
    assert not net.is_bot_block(403, {"Server": "nginx"})
    assert not net.is_bot_block(404, {"Server": "cloudflare"})
    with IIIFServer() as server:
        server.cloudflare = True
        with pytest.raises(net.FetchError) as err:
            net.fetch(server.base + "/iiif/img1/full/max/0/default.jpg")
    assert err.value.kind == "blocked" and err.value.status == 403 and not err.value.retryable


def test_a_block_stops_the_download_without_more_requests(tmp_path):
    m = parse_manifest(manifest_v3("https://e.org", 3))
    jobs = list(zip(m.pages, naming.file_stems(m.pages, m, naming.NamingOptions())))
    calls = []

    def blocked(url, **kw):
        calls.append(url)
        raise net.FetchError("blocked", url, 403)
    results = dl.download(jobs, tmp_path, fetch=blocked, sleep=lambda s: None)
    assert [r.status for r in results] == ["failed"] * 3
    assert len(calls) == 1 and all(r.error.kind == "blocked" for r in results)


def test_gallica_rules():
    rule = rule_for(G + "/full/max/0/native.jpg")
    assert rule.full_interval == 12.0 and rule.full_per_minute == 5 and rule.concurrency == 2
    assert rule_for("https://example.org/iiif/x/full/max/0/default.jpg").full_interval == 0
    assert is_full_size(G + "/full/full/0/native.jpg")
    assert is_full_size("https://e.org/images/f1.jpg")          # image sans service
    assert not is_full_size(G + "/full/!1500,1500/0/native.jpg")
    assert not is_full_size(G + "/0,0,512,512/512,/0/native.jpg")


class FakeClock:
    def __init__(self):
        self.now = 1000.0
        self.slept = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(round(seconds, 3))
        self.now += seconds


def test_pacer_spaces_full_size_requests_to_the_same_server():
    clock = FakeClock()
    pacer = Pacer(clock=clock, sleep=clock.sleep)
    full = G + "/full/max/0/native.jpg"
    for _ in range(3):
        pacer.wait(full)
    assert clock.slept == [12.0, 12.0]
    pacer.wait(G + "/full/!800,800/0/native.jpg")   # 1 s après la précédente
    assert clock.slept[-1] == 1.0
    pacer.wait("https://example.org/iiif/x/full/max/0/default.jpg")
    assert len(clock.slept) == 3                     # autre serveur : sans attente
    clock.now += 60
    pacer.wait(full)
    assert len(clock.slept) == 3                     # assez attendu déjà


def test_pacer_wait_can_be_cancelled():
    clock = FakeClock()
    pacer = Pacer(clock=clock, sleep=clock.sleep)
    pacer.wait(G + "/full/max/0/native.jpg")
    cancel = threading.Event()
    cancel.set()
    assert pacer.wait(G + "/full/max/0/native.jpg", cancel) is False
    paced = pacer.wrap(lambda url, **kw: net.Response(FAKE_JPEG, "image/jpeg", url))
    with pytest.raises(net.FetchError) as err:
        paced(G + "/full/max/0/native.jpg", cancel=cancel)
    assert err.value.kind == "cancelled"


def test_download_follows_the_pace(tmp_path):
    clock = FakeClock()
    pages = [Page(index=i, label=f"f{i}", service=ImageService(G.replace("f1", f"f{i}"), 1)) for i in (1, 2, 3)]
    web = lambda url, **kw: net.Response(FAKE_JPEG, "image/jpeg", url)  # noqa: E731
    results = dl.download([(p, p.label) for p in pages], tmp_path, fetch=web,
                          pacer=Pacer(clock=clock, sleep=clock.sleep))
    assert [r.status for r in results] == ["ok"] * 3
    assert clock.slept == [12.0, 12.0]
