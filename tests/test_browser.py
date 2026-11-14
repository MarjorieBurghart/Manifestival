# -*- coding: utf-8 -*-
"""Navigateur intégré (Qt WebEngine) : téléchargements par le navigateur,
vérification à la Cloudflare (page en JavaScript qui pose un cookie),
parcours complet dans la fenêtre principale."""
from __future__ import annotations

import threading

import pytest

from iiif_test_server import IIIFServer, real_jpeg

from PySide6.QtCore import QCoreApplication, QSettings, Qt

from manifestival import net
from manifestival.ui import browser

# Qt WebEngine le demande avant la création de l'application Qt.
QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)

pytestmark = pytest.mark.skipif(not browser.available(), reason="Qt WebEngine absent")


@pytest.fixture
def settings(tmp_path):
    return QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)


@pytest.fixture
def session(qapp, settings):
    s = browser.BrowserSession(None, settings)
    yield s
    s.shutdown()


def fetch_in_background(qtbot, session, url, timeout=20, **kwargs):
    """Appelle session.fetch depuis un fil de fond, comme les tâches de
    Manifestival, pendant que le fil de l'interface tourne."""
    box = {}

    def work():
        try:
            box["reply"] = session.fetch(url, **kwargs)
        except Exception as exc:  # renvoyée au test
            box["error"] = exc
    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    qtbot.waitUntil(lambda: not thread.is_alive(), timeout=timeout * 1000)
    if "error" in box:
        raise box["error"]
    return box["reply"]


def test_downloads_through_the_browser(qtbot, session):
    with IIIFServer(real_jpeg()) as server:
        reply = fetch_in_background(qtbot, session, server.base + "/manifest3.json")
        assert reply.content_type == "application/json" and b'"Manifest"' in reply.data
        seen = []
        image = fetch_in_background(qtbot, session, server.base + "/iiif/img1/full/max/0/default.jpg",
                                    progress=lambda r, t: seen.append(r))
        assert image.data[:3] == b"\xff\xd8\xff" and seen
        with pytest.raises(net.FetchError) as err:
            fetch_in_background(qtbot, session, server.base + "/missing.json")
        assert err.value.kind == "http"
        with pytest.raises(net.FetchError) as err:
            fetch_in_background(qtbot, session, server.base + "/manifest3.json", max_bytes=10)
        assert err.value.kind == "too_large"


def test_cloudflare_refusal_through_the_browser_is_a_block(qtbot, session):
    with IIIFServer(real_jpeg()) as server:
        session.add_host(server.base, "Cloudflare")
        server.cloudflare = True
        with pytest.raises(net.FetchError) as err:
            fetch_in_background(qtbot, session, server.base + "/iiif/img1/full/max/0/default.jpg")
    assert err.value.kind == "blocked" and err.value.protection == "Cloudflare"


def test_cancel_and_ui_thread_guard(qtbot, session):
    from bad_server import BadServer
    with BadServer() as bad:
        cancel = threading.Event()
        threading.Timer(0.5, cancel.set).start()
        with pytest.raises(net.FetchError) as err:
            fetch_in_background(qtbot, session, bad.base + "/hang/x.jpg", cancel=cancel)
        assert err.value.kind == "cancelled"
    with pytest.raises(RuntimeError):
        session.fetch("http://127.0.0.1:9/x")   # jamais depuis le fil de l'interface


def test_sites_are_remembered_and_forgotten(qapp, settings):
    first = browser.BrowserSession(None, settings)
    first.add_host("https://gallica.bnf.fr/iiif/ark:/12148/x/manifest.json", "Cloudflare")
    assert first.handles("https://gallica.bnf.fr/ark:/12148/x/f1.highres")
    assert not first.handles("https://example.org/x")
    second = browser.BrowserSession(None, settings)          # relu des réglages
    assert second.hosts == {"gallica.bnf.fr": "Cloudflare"}
    second.forget()
    third = browser.BrowserSession(None, settings)
    assert third.hosts == {}
    for s in (first, second, third):
        s.shutdown()


def test_routing(qapp, settings):
    calls = []
    base = lambda url, **kw: calls.append(("net", url)) or "net"  # noqa: E731

    class FakeSession:
        def handles(self, url):
            return "gallica" in url

        def fetch(self, url, **kw):
            calls.append(("browser", url))
            return "browser"
    fetch = browser.routed_fetch(lambda: FakeSession(), base)
    assert fetch("https://gallica.bnf.fr/x") == "browser" and fetch("https://e.org/x") == "net"
    assert browser.routed_fetch(lambda: None, base)("https://gallica.bnf.fr/x") == "net"


def test_verification_dialog_passes_a_javascript_check(qtbot, session):
    with IIIFServer(real_jpeg()) as server:
        server.challenge = True
        url = server.base + "/manifest3.json"
        # Hors navigateur : la page de vérification, reconnue comme Cloudflare.
        from manifestival.sources import load_json
        with pytest.raises(net.FetchError) as err:
            load_json(url)
        assert err.value.kind == "blocked" and err.value.protection == "Cloudflare"
        dialog = browser.VerifyDialog(lambda key, **kw: key, session, url, "Cloudflare")
        with qtbot.waitSignal(dialog.verified, timeout=20000):
            dialog.start()
        assert session.handles(url) and server.challenged
        # Ensuite, par le navigateur, avec le cookie posé par la vérification.
        reply = fetch_in_background(qtbot, session, server.base + "/iiif/img1/full/max/0/default.jpg")
        assert reply.data[:3] == b"\xff\xd8\xff"


def test_whole_flow_in_the_main_window(qtbot, settings, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from manifestival.i18n import Translator
    from manifestival.ui.main_window import MainWindow
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    asked = []
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: asked.append(a[2]) or QMessageBox.Yes)
    with IIIFServer(real_jpeg()) as server:
        server.challenge = True
        win = MainWindow(Translator("fr"), settings)
        qtbot.addWidget(win)
        win.show()
        win.address_combo.setEditText(server.base + "/manifest3.json")
        win.open_address()
        # Page de vérification → proposition → vérification → manifest par le navigateur.
        qtbot.waitUntil(lambda: win.manifest is not None, timeout=30000)
        assert asked and "Cloudflare" in asked[0]
        assert win.browser.handles(server.base)
        qtbot.waitUntil(lambda: len(win.model.thumbs) == 5, timeout=30000)
        win.close()                     # ferme aussi le navigateur, dans l'ordre


def test_a_block_page_in_the_browser_is_recognised_and_logged(qtbot, settings, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    from manifestival.i18n import Translator
    from manifestival.ui.main_window import MainWindow
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    with IIIFServer(real_jpeg()) as server:
        server.challenge = True          # les logiciels : page de vérification
        server.block_browsers = True     # le navigateur intégré : refusé
        win = MainWindow(Translator("fr"), settings)
        qtbot.addWidget(win)
        win.show()
        win.address_combo.setEditText(server.base + "/manifest3.json")
        win.open_address()
        qtbot.waitUntil(lambda: win.verify_dialog is not None, timeout=10000)
        dialog = win.verify_dialog
        with qtbot.waitSignal(dialog.blocked, timeout=20000):
            pass
        assert "blocage" in dialog.status.text() and dialog.cancel_button.text() == "Fermer"
        assert not win.browser.handles(server.base)      # pas de faux succès
        last = win.refusals.items[-1]
        assert (last.context, last.status, last.protection) == ("browser", 403, "Cloudflare")
        win.close()
