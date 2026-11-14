# -*- coding: utf-8 -*-
"""L'application pilotée (Qt sans écran) contre un serveur IIIF local."""
from __future__ import annotations

import json
import time

import pytest

from iiif_test_server import IIIFServer, page_image, real_jpeg

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtWidgets import QApplication, QInputDialog, QMessageBox

from manifestival.i18n import Translator
from manifestival.ui.download_dialog import DownloadDialog
from manifestival.ui.main_window import MainWindow


@pytest.fixture
def server(qapp):
    with IIIFServer(real_jpeg()) as srv:
        yield srv


@pytest.fixture
def settings(tmp_path):
    s = QSettings(str(tmp_path / "settings.ini"), QSettings.IniFormat)
    yield s
    s.sync()


@pytest.fixture
def window(qtbot, settings, monkeypatch):
    # Pas de boîte modale bloquante pendant les tests.
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    win = MainWindow(Translator("fr"), settings)
    qtbot.addWidget(win)
    win.resize(1000, 700)
    win.show()
    yield win
    win.loader.shutdown()


def _open(qtbot, win, url):
    win.address_combo.setEditText(url)
    win.open_address()
    qtbot.waitUntil(lambda: win.manifest is not None and win.manifest_thread is None, timeout=10000)


def _click(qtbot, view, row, modifiers=Qt.NoModifier):
    rect = view.visualRect(view.model().index(row))
    qtbot.mouseClick(view.viewport(), Qt.LeftButton, modifiers, rect.center())


def test_open_manifest_show_thumbnails_and_metadata(qtbot, window, server):
    _open(qtbot, window, server.base + "/manifest3.json")
    assert window.model.rowCount() == 5
    assert window.stack.currentIndex() == 1
    assert "Psautier de test" in window.windowTitle()
    qtbot.waitUntil(lambda: len(window.model.thumbs) == 5, timeout=10000)
    assert any("/full/!240,240/0/default.jpg" in r for r in server.requests)
    html = window.manifest_info.toHtml()
    assert "Ms. lat. 1234" in html and "catalogue" in html and "alert(1)" not in html
    assert "f. 1r" in window.page_info.toHtml()
    window.view.setCurrentIndex(window.model.index(2))
    assert "f. 2r" in window.page_info.toHtml()
    # Adresse retenue dans l'historique.
    assert window.settings.value("recent")[0] == server.base + "/manifest3.json"


def test_click_shift_click_and_select_all(qtbot, window, server):
    _open(qtbot, window, server.base + "/manifest3.json")
    view, model = window.view, window.model
    _click(qtbot, view, 1)
    assert model.checked_rows() == [1]
    _click(qtbot, view, 3, Qt.ControlModifier | Qt.ShiftModifier)
    assert model.checked_rows() == [1, 2, 3]
    _click(qtbot, view, 2)
    assert model.checked_rows() == [1, 3]
    # Plage depuis une image décochée : la plage est décochée.
    _click(qtbot, view, 0, Qt.ShiftModifier)
    assert model.checked_rows() == [3]
    assert "1 sélectionnée" in window.count_label.text()
    window.select_all_button.click()
    assert model.checked_count() == 5 and window.download_button.isEnabled()
    window.invert_button.click()
    assert model.checked_count() == 0 and not window.download_button.isEnabled()
    # Un clic sur la case elle-même ne l'inverse qu'une fois.
    window.model.set_all(False)
    corner = view.visualRect(model.index(2)).topLeft()
    qtbot.mouseClick(view.viewport(), Qt.LeftButton, Qt.NoModifier, corner + QPoint(15, 15))
    assert model.checked_rows() == [2]
    window.model.set_all(False)
    # Clavier : Espace coche l'image courante.
    view.setCurrentIndex(model.index(4))
    qtbot.keyClick(view, Qt.Key_Space)
    assert model.checked_rows() == [4]


def test_double_click_previews_without_changing_the_box(qtbot, window, server):
    _open(qtbot, window, server.base + "/manifest3.json")
    rect = window.view.visualRect(window.model.index(2))
    qtbot.mouseDClick(window.view.viewport(), Qt.LeftButton, Qt.NoModifier, rect.center())
    # mouseDClick n'envoie que le double-clic : le premier clic le précède
    # en vrai ; l'état revient donc à son contraire ici.
    preview = window.preview_dialog
    assert preview is not None and preview.row == 2
    preview.check.setChecked(True)
    assert window.model.is_checked(2)
    preview.next.click()
    assert preview.row == 3 and not preview.check.isChecked()
    preview.close()


def test_download_dialog_names_and_files(qtbot, window, server, tmp_path):
    _open(qtbot, window, server.base + "/manifest3.json")
    window.model.set_checked([0, 1, 4], True)
    window.settings.setValue("download/folder", str(tmp_path))
    window.open_download()
    dialog: DownloadDialog = window.download_dialog
    assert dialog.prefix_combo.itemText(0) == "Ms. lat. 1234"
    dialog.prefix_combo.setEditText("Ms. lat. 1234")
    # Par défaut, les espaces sont gardés et le séparateur n'est pas entouré.
    assert not dialog.replace_spaces_check.isChecked() and not dialog.pad_check.isChecked()
    assert dialog.preview_label.text().splitlines()[0] == "Ms. lat. 1234_001_f. 1r.jpg"
    dialog.separator_combo.setCurrentIndex(1)  # tiret
    dialog.pad_check.setChecked(True)
    assert dialog.template_edit.text() == "{prefix} - {n} - {label}"
    assert dialog.preview_label.text().splitlines()[0] == "Ms. lat. 1234 - 001 - f. 1r.jpg"
    dialog.separator_combo.setCurrentIndex(3)  # espace : l'option ne s'applique pas
    assert not dialog.pad_check.isEnabled() and not dialog.replace_spaces_check.isEnabled()
    assert dialog.preview_label.text().splitlines()[0] == "Ms. lat. 1234 001 f. 1r.jpg"
    dialog.separator_combo.setCurrentIndex(0)
    dialog.pad_check.setChecked(False)
    dialog.replace_spaces_check.setChecked(True)
    preview = dialog.preview_label.text().splitlines()
    assert preview == ["Ms_lat_1234_001_f_1r.jpg", "Ms_lat_1234_002_f_1v.jpg",
                       "Ms_lat_1234_005_Reliure-dos.jpg"]
    dialog.part_checks["n"].setChecked(False)
    assert dialog.preview_label.text().splitlines()[0] == "Ms_lat_1234_f_1r.jpg"
    dialog.part_checks["n"].setChecked(True)
    dialog.separator_combo.setCurrentIndex(1)  # tiret
    assert dialog.preview_label.text().splitlines()[0] == "Ms-lat-1234-001-f-1r.jpg"
    dialog.separator_combo.setCurrentIndex(0)
    dialog.part_checks["meta"].setChecked(True)
    assert dialog.preview_label.text().splitlines()[0] == "Ms_lat_1234_001_f_1r_1.jpg"
    dialog.part_checks["meta"].setChecked(False)
    # Modèle personnalisé.
    dialog.custom_check.setChecked(True)
    dialog.template_edit.setText("{label}--{prefix}")
    assert dialog.preview_label.text().splitlines()[0] == "f_1r--Ms_lat_1234.jpg"  # tapé tel quel
    dialog.custom_check.setChecked(False)

    dialog.start()
    qtbot.waitUntil(lambda: dialog.thread is None, timeout=15000)
    folder = tmp_path / "Ms_lat_1234"
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["Ms_lat_1234_001_f_1r.jpg", "Ms_lat_1234_002_f_1v.jpg",
                     "Ms_lat_1234_005_Reliure-dos.jpg", "Ms_lat_1234_images.csv",
                     "Ms_lat_1234_manifest.json"]
    assert json.loads((folder / "Ms_lat_1234_manifest.json").read_text(encoding="utf-8"))["type"] == "Manifest"
    assert "3 images téléchargées" in dialog.status.text()
    assert dialog.open_button.isVisible()
    # Réglages retenus pour la fois suivante.
    assert window.settings.value("naming/parts") == ["prefix", "n", "label"]
    assert window.settings.value("naming/spaces_to_separator") in (True, "true")
    # Une seconde fois : les fichiers présents sont gardés.
    dialog.start()
    qtbot.waitUntil(lambda: dialog.thread is None, timeout=15000)
    assert "3 déjà présentes" in dialog.status.text()
    dialog.reject()


def test_download_failures_can_be_retried(qtbot, window, server, tmp_path):
    _open(qtbot, window, server.base + "/manifest3.json")
    window.model.set_checked([0, 1], True)
    target = tmp_path / "images"
    window.settings.setValue("download/folder", str(target))
    window.settings.setValue("download/subfolder", False)
    window.settings.setValue("download/index", False)
    window.open_download()
    dialog = window.download_dialog
    # Vignettes chargées d'abord : sinon le refus programmé pourrait servir
    # à une vignette plutôt qu'au téléchargement.
    qtbot.waitUntil(lambda: window.loader.remaining() == 0, timeout=10000)
    server.failures["img2"] = [403]
    dialog.start()
    qtbot.waitUntil(lambda: dialog.thread is None, timeout=15000)
    assert "1 échec" in dialog.status.text()
    assert "Accès refusé" in dialog.details.toPlainText()
    refusal = window.refusals.items[-1]     # noté au journal des refus
    assert (refusal.context, refusal.status, refusal.host) == ("download", 403, "127.0.0.1")
    assert dialog.retry_button.isVisible()
    dialog.retry_button.click()
    qtbot.waitUntil(lambda: dialog.thread is None, timeout=15000)
    assert "2 images téléchargées" in dialog.status.text()
    assert not dialog.retry_button.isVisible()
    assert sorted(p.name for p in target.iterdir()) == ["001_f. 1r.jpg", "002_f. 1v.jpg"]
    dialog.reject()


def test_v2_manifest_language_switch_and_errors(qtbot, window, server, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a: warnings.append(text))
    _open(qtbot, window, server.base + "/manifest2.json")
    assert window.model.rowCount() == 3 and "Livre d'heures" in window.windowTitle()
    window.model.set_checked([1], True)
    window.set_language("en")
    assert window.open_button.text() == "Show"
    assert "Book of hours" in window.windowTitle()
    assert window.model.checked_rows() == [1]  # la sélection survit
    window.set_language("ja")
    assert window.open_button.text() == "表示"
    window.set_language("fr")

    window.address_combo.setEditText(server.base + "/page.html")
    window.open_address()
    qtbot.waitUntil(lambda: window.manifest_thread is None and bool(warnings), timeout=10000)
    assert "JSON" in warnings[-1]
    window.address_combo.setEditText(server.base + "/missing.json")
    window.open_address()
    qtbot.waitUntil(lambda: window.manifest_thread is None and len(warnings) == 2, timeout=10000)
    assert "404" in warnings[-1]


def test_collection_offers_its_manifests(qtbot, window, server, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getItem",
                        lambda parent, title, label, items, *a: (items[1], True))
    window.address_combo.setEditText(server.base + "/collection.json")
    window.open_address()
    qtbot.waitUntil(lambda: window.manifest is not None and window.manifest_thread is None,
                    timeout=10000)
    assert window.manifest.version == 2


def test_tile_size_slider(qtbot, window, server):
    _open(qtbot, window, server.base + "/manifest3.json")
    small = window.view.visualRect(window.model.index(0)).width()
    window.size_slider.setValue(240)
    QApplication.processEvents()
    qtbot.waitUntil(lambda: window.view.visualRect(window.model.index(0)).width() > small)


@pytest.mark.parametrize("tiles", [True, False])
def test_preview_zooms_to_full_resolution(qtbot, settings, monkeypatch, tiles):
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: QMessageBox.Ok)
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    with IIIFServer(source=page_image(800, 1200), tiles=tiles) as server:
        win = MainWindow(Translator("fr"), settings)
        qtbot.addWidget(win)
        win.show()
        _open(qtbot, win, server.base + "/manifest3.json")
        win.show_preview(0)
        preview = win.preview_dialog
        view = preview.view
        # Aperçu reçu (au moins la largeur de l'image) et info.json lu.
        qtbot.waitUntil(lambda: view.base_width >= 800 and view.info_state in ("tiles", "no_tiles"),
                        timeout=10000)
        assert view.fitted and view.full_width == 800 and view.full_height == 1200
        # L'aperçu (!1600,1600) d'une image de 800 × 1200 est déjà entier :
        # on le force plus petit pour que le zoom demande des détails.
        view.base_width = 200
        view.centerOn(400, 600)
        preview.actual_button.click()
        assert abs(view.device_zoom() - 1.0) < 1e-6
        if tiles:
            qtbot.waitUntil(lambda: view.tile_count() > 0 and not view.loading(), timeout=10000)
            tile_requests = [r for r in server.requests if r.endswith("/256,256/0/default.jpg")]
            assert tile_requests and all(",256,256/" in r for r in tile_requests)
            # Seules les tuiles visibles sont demandées.
            assert len(tile_requests) <= 20
            # Budget de mémoire minuscule : les tuiles à l'écran restent.
            from manifestival.ui import zoom_view
            monkeypatch.setattr(zoom_view, "TILE_PIXELS", 1)
            shown = view.tile_count()
            view._evict()
            assert view.tile_count() == shown
            view.centerOn(0, 0)
            qtbot.waitUntil(lambda: not view.loading(), timeout=10000)
            assert view.tile_count() <= len(view.visible_keys) + 1
        else:
            qtbot.waitUntil(lambda: view.full_state == "done", timeout=10000)
            assert any("/full/max/0/default.jpg" in r for r in server.requests)
        assert "100 %" in preview.status.text()
        preview.zoom_in_button.click()
        assert view.zoom() > 1.0
        view.set_zoom(100)
        assert view.zoom() == 4.0 and not preview.zoom_in_button.isEnabled()
        preview.fit_button.click()
        assert view.fitted
        # Image suivante : la vue repart de zéro.
        preview.next.click()
        assert view.tile_count() == 0 and view.fitted
        preview.close()
        win.loader.shutdown()


def test_blocked_server_stops_thumbnails_and_says_why(qtbot, window):
    with IIIFServer(real_jpeg()) as server:
        server.cloudflare = True
        server.delay = 0.3   # plusieurs vignettes en cours au moment du blocage
        _open(qtbot, window, server.base + "/manifest3.json")
        qtbot.waitUntil(lambda: bool(window.blocked_message), timeout=10000)
        qtbot.waitUntil(lambda: len(window.model.failed) == 5, timeout=10000)
        image_requests = [r for r in server.requests if r.startswith("/iiif/")]
        assert len(image_requests) <= 4   # pas une requête par vignette
        assert "127.0.0.1" in window.statusBar().currentMessage()
        assert "403" in window.statusBar().currentMessage()


def test_download_dialog_warns_about_slow_servers(qtbot, settings):
    from manifestival.manifest import ImageService, Manifest, Page
    pages = tuple(Page(index=i, label=f"f{i}",
                       service=ImageService(f"https://gallica.bnf.fr/iiif/ark:/12148/x/f{i}", 1))
                  for i in range(1, 31))
    manifest = Manifest(source="", id="", label="Ms", version=2, pages=pages, canvas_count=30)
    dialog = DownloadDialog(Translator("fr"), settings, manifest, pages)
    qtbot.addWidget(dialog)
    # Gallica : l'API image .highres (50 par minute) passe en premier.
    assert not dialog.host_note.isHidden()
    assert "50 par minute" in dialog.host_note.text() and "1 min" in dialog.host_note.text()
    dialog.size_combo.setCurrentIndex(2)   # 1500 pixels : pas la pleine taille
    assert dialog.host_note.isHidden()


def test_closing_during_a_stuck_download_is_quick(qtbot, settings, monkeypatch, tmp_path):
    from bad_server import BadServer
    from manifestival.manifest import Manifest, Page
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.Yes)
    with BadServer() as bad:
        pages = (Page(index=1, label="f1", image_url=bad.base + "/hang/1.jpg"),)
        manifest = Manifest(source="", id="", label="Ms", version=3, pages=pages, canvas_count=1)
        settings.setValue("download/folder", str(tmp_path))
        dialog = DownloadDialog(Translator("fr"), settings, manifest, pages)
        qtbot.addWidget(dialog)
        dialog.show()
        dialog.start()
        qtbot.waitUntil(lambda: bad.hits.get("/hang/1.jpg"), timeout=5000)
        start = time.monotonic()
        dialog.reject()           # « Arrêter ? » → Oui
        assert time.monotonic() - start < 3
        assert dialog.thread is None and not dialog.isVisible()


def test_loading_a_silent_manifest_can_be_cancelled(qtbot, window):
    from bad_server import BadServer
    with BadServer() as bad:
        window.address_combo.setEditText(bad.base + "/hang/manifest.json")
        window.open_address()
        assert window.open_button.text() == "Annuler"
        qtbot.waitUntil(lambda: bad.hits.get("/hang/manifest.json"), timeout=5000)
        window.open_button.click()
        qtbot.waitUntil(lambda: window.manifest_thread is None, timeout=3000)
        assert window.open_button.text() == "Afficher"
        assert "annulé" in window.statusBar().currentMessage()


def test_download_shows_megabytes_while_receiving(qtbot, settings, tmp_path):
    from bad_server import BadServer
    from manifestival.manifest import Manifest, Page
    with BadServer(trickle_delay=0.05, trickle_chunk=200) as bad:
        pages = (Page(index=1, label="f1", image_url=bad.base + "/trickle/1.jpg"),)
        manifest = Manifest(source="", id="", label="Ms", version=3, pages=pages, canvas_count=1)
        settings.setValue("download/folder", str(tmp_path))
        dialog = DownloadDialog(Translator("fr"), settings, manifest, pages)
        qtbot.addWidget(dialog)
        texts = []
        dialog.status.setText = lambda text, _orig=dialog.status.setText: (texts.append(text), _orig(text))
        dialog.start()
        qtbot.waitUntil(lambda: dialog.thread is None, timeout=15000)
        assert any("Mo sur" in t and "f1" in t for t in texts)


def test_refusals_are_logged_and_shown(qtbot, settings, monkeypatch, tmp_path):
    from manifestival.refusals import RefusalLog
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, text, *a: warnings.append(text))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.No)
    log = RefusalLog(tmp_path / "refusals.json")
    win = MainWindow(Translator("fr"), settings, refusals=log)
    qtbot.addWidget(win)
    win.show()
    with IIIFServer(real_jpeg()) as server:
        server.cloudflare = True
        _open(qtbot, win, server.base + "/manifest3.json")   # manifest servi, images refusées
        qtbot.waitUntil(lambda: bool(win.blocked_message), timeout=10000)
    assert "Cloudflare" in win.blocked_message
    assert [(r.context, r.status, r.protection) for r in log.items] == [("thumbnails", 403, "Cloudflare")]
    win.show_refusals()
    dialog = win.refusals_dialog
    assert dialog.table.rowCount() == 1 and dialog.table.item(0, 3).text() == "Cloudflare"
    assert dialog.table.item(0, 4).text() == "vignettes"
    dialog.copy_button.click()
    assert "403 [Cloudflare]" in QApplication.clipboard().text()
    dialog.clear_button.click()
    assert dialog.table.isHidden() and not dialog.empty.isHidden()
    win.loader.shutdown()
