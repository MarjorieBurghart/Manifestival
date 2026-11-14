# -*- coding: utf-8 -*-
"""Serveurs lents, muets, qui coupent ou disparaissent : rien ne reste
bloqué, l'utilisateur peut toujours annuler, et un serveur qui ne répond plus
n'est pas sollicité sans fin."""
from __future__ import annotations

import threading
import time

import pytest

from bad_server import BadServer, closed_url, timed

from manifestival import download as dl
from manifestival import net
from manifestival.manifest import Page


@pytest.fixture
def bad():
    with BadServer(slow_delay=0.4, trickle_delay=0.2) as server:
        yield server


@pytest.fixture
def quick(monkeypatch):
    """Délais raccourcis : 1 s de silence, 2 s par image, une seule reprise."""
    monkeypatch.setattr(dl, "IMAGE_TIMEOUT", 1)
    monkeypatch.setattr(dl, "IMAGE_DEADLINE", 2)
    monkeypatch.setattr(dl, "RETRY_DELAYS", (0.1,))


def _pages(bad, *paths):
    return [Page(index=i, label=f"p{i}", image_url=f"{bad.base}{path}/{i}.jpg")
            for i, path in enumerate(paths, 1)]


# ------------------------------------------------------------------ réseau

def test_silent_server_times_out(bad):
    error, elapsed = timed(lambda: net.fetch(bad.base + "/hang", timeout=1))
    assert error.kind == "timeout" and elapsed < 3


def test_trickling_server_is_cut_by_the_deadline(bad):
    error, elapsed = timed(lambda: net.fetch(bad.base + "/trickle", timeout=1, deadline=1.5))
    assert error.kind == "timeout" and error.detail == "deadline" and elapsed < 3


def test_cut_transfer_is_an_error_not_a_truncated_image(bad):
    error, _ = timed(lambda: net.fetch(bad.base + "/cut", timeout=2))
    assert isinstance(error, net.FetchError) and error.kind == "network" and error.retryable


def test_server_down_fails_at_once():
    error, elapsed = timed(lambda: net.fetch(closed_url(), timeout=5))
    assert error.kind == "network" and elapsed < 2


def test_cancel_works_even_when_the_server_says_nothing(bad):
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    error, elapsed = timed(lambda: net.fetch(bad.base + "/hang", timeout=30, cancel=cancel))
    assert error.kind == "cancelled" and elapsed < 1.5


def test_slow_but_working_server_is_waited_for(bad):
    reply, elapsed = timed(lambda: net.fetch(bad.base + "/slow", timeout=3, deadline=5))
    assert isinstance(reply, net.Response) and elapsed >= 0.35


def test_progress_is_reported(bad):
    seen = []
    net.fetch(bad.base + "/ok", progress=lambda r, t: seen.append((r, t)), deadline=5)
    assert seen and seen[-1][0] == seen[-1][1] > 0


# ------------------------------------------------------------------ téléchargement

def test_unreachable_server_stops_the_download(bad, quick, tmp_path):
    pages = _pages(bad, "/hang", "/hang", "/hang", "/hang", "/hang")
    results, elapsed = timed(lambda: dl.download([(p, p.label) for p in pages], tmp_path))
    assert [r.status for r in results] == ["failed"] * 5
    assert [r.error.kind for r in results] == ["timeout", "timeout", "unreachable", "unreachable", "unreachable"]
    # Deux images essayées (chacune une fois de plus), puis plus rien.
    assert bad.hits.get("/hang/1.jpg") == 2 and bad.hits.get("/hang/2.jpg") == 2
    assert "/hang/3.jpg" not in bad.hits
    assert elapsed < 12


def test_one_bad_image_does_not_stop_the_others(bad, quick, tmp_path):
    pages = _pages(bad, "/cut", "/ok", "/error500", "/slow", "/trickle", "/ok")
    results = dl.download([(p, p.label) for p in pages], tmp_path)
    assert [r.status for r in results] == ["failed", "ok", "failed", "ok", "failed", "ok"]
    assert results[2].error.status == 500 and results[4].error.detail == "deadline"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["p2.jpg", "p4.jpg", "p6.jpg"]
    assert not list(tmp_path.glob("*.part"))


def test_server_down_from_the_start(quick, tmp_path):
    pages = [Page(index=i, label=f"p{i}", image_url=closed_url()) for i in (1, 2, 3)]
    results, elapsed = timed(lambda: dl.download([(p, p.label) for p in pages], tmp_path))
    assert [r.error.kind for r in results] == ["network", "network", "unreachable"]
    assert elapsed < 3


def test_cancel_during_a_silent_download(bad, monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "IMAGE_TIMEOUT", 30)
    pages = _pages(bad, "/hang", "/ok")
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    results, elapsed = timed(lambda: dl.download([(p, p.label) for p in pages], tmp_path, cancel=cancel))
    assert [r.status for r in results] == ["cancelled", "cancelled"] and elapsed < 2


def test_disk_error_stops_instead_of_failing_every_image(bad, quick, tmp_path):
    pages = _pages(bad, "/ok", "/ok", "/ok")
    jobs = [(pages[0], "missing-folder/p1"), (pages[1], "p2"), (pages[2], "p3")]
    results = dl.download(jobs, tmp_path)
    assert [r.status for r in results] == ["failed"] * 3
    assert all(isinstance(r.error, OSError) for r in results)
    assert "/ok/2.jpg" not in bad.hits


def test_retry_resumes_after_the_server_comes_back(bad, quick, tmp_path):
    pages = _pages(bad, "/hang", "/hang", "/hang")
    first = dl.download([(p, p.label) for p in pages], tmp_path)
    assert all(r.status == "failed" for r in first)
    # Le serveur « revient » : les mêmes pages, servies normalement.
    back = [Page(index=p.index, label=p.label, image_url=p.image_url.replace("/hang/", "/ok/")) for p in pages]
    second = dl.download([(p, p.label) for p in back], tmp_path)
    assert all(r.status == "ok" for r in second)


def test_waiting_for_a_busy_server_is_announced(bad, monkeypatch, tmp_path):
    monkeypatch.setattr(dl, "RETRY_DELAYS", (0.1, 0.1))
    waits = []
    pages = _pages(bad, "/error500")
    start = time.monotonic()
    dl.download([(pages[0], "x")], tmp_path, notify=waits.append)
    assert waits == [0.1, 0.1] and time.monotonic() - start < 3
