# -*- coding: utf-8 -*-
"""Auto-test : ``Manifestival --self-test RAPPORT.json``.

Lancé par ``packaging/build.py`` sur l'exécutable construit, avant de le
zipper : tout ce dont l'application a besoin y est-il ? Aucune requête
réseau. Le rapport JSON donne pour chaque vérification ``ok`` et un détail ;
celles qui ne sont pas ``required`` sont informatives. Code de sortie 0 si
toutes les vérifications requises réussissent.
"""
from __future__ import annotations

from pathlib import Path
import json
import os
import sys
import tempfile
import time
import traceback

from . import __version__

LANGS = ("fr", "en", "de", "it", "cs", "ru", "ja")

SAMPLE_V3 = {
    "@context": "http://iiif.io/api/presentation/3/context.json",
    "id": "https://example.org/manifest.json", "type": "Manifest",
    "label": {"fr": ["Manuscrit d'essai"]},
    "metadata": [{"label": {"fr": ["Cote"]}, "value": {"none": ["Ms. 1"]}}],
    "items": [{
        "id": "https://example.org/canvas/1", "type": "Canvas", "label": {"none": ["f. 1r"]},
        "width": 100, "height": 150,
        "items": [{"id": "https://example.org/p/1", "type": "AnnotationPage", "items": [{
            "id": "https://example.org/a/1", "type": "Annotation", "motivation": "painting",
            "target": "https://example.org/canvas/1",
            "body": {"id": "https://example.org/iiif/1/full/max/0/default.jpg", "type": "Image",
                     "service": [{"id": "https://example.org/iiif/1", "type": "ImageService3",
                                  "profile": "level1"}]}}]}],
    }],
}


def _check_translations():
    from . import i18n
    reference = set(i18n.load("fr"))
    if not reference:
        raise RuntimeError(f"traductions introuvables dans {i18n.DIRECTORY}")
    for lang in LANGS:
        keys = set(i18n.load(lang))
        if keys != reference:
            raise RuntimeError(f"{lang} : {len(reference ^ keys)} clés différentes")
    return f"{len(LANGS)} langues, {len(reference)} textes"


def _check_manifest():
    from .manifest import parse_manifest
    from .naming import NamingOptions, render
    from .images import image_urls, thumbnail_urls
    from .tiles import parse_info
    m = parse_manifest(SAMPLE_V3, "", "fr")
    name = render(m.pages[0], m, NamingOptions(prefix=m.meta("Cote")))
    assert name == "Ms. 1_001_f. 1r", name
    assert image_urls(m.pages[0])[0].endswith("/full/max/0/default.jpg")
    assert thumbnail_urls(m.pages[0])
    assert parse_info({"id": "https://example.org/iiif/1", "type": "ImageService3", "profile": "level1",
                       "width": 100, "height": 150}, "https://example.org/iiif/1")
    return name


def _check_tls():
    import ssl
    from . import net
    net.ssl_context()
    paths = ssl.get_default_verify_paths()
    cafile = os.environ.get("SSL_CERT_FILE") or paths.cafile
    if sys.platform == "win32":
        return "certificats du système (Windows)"
    if cafile and os.path.isfile(cafile):
        return cafile
    if paths.capath and os.path.isdir(paths.capath) and any(os.scandir(paths.capath)):
        return paths.capath
    raise RuntimeError("aucun certificat HTTPS trouvé")


def _check_images():
    from PySide6.QtGui import QImageReader
    formats = {bytes(f.data()).decode().lower() for f in QImageReader.supportedImageFormats()}
    missing = {"jpg", "jpeg", "png"} - formats
    if missing:
        raise RuntimeError("formats absents : " + ", ".join(sorted(missing)))
    return ", ".join(sorted(formats))


def _check_qt_translations():
    from PySide6.QtCore import QLibraryInfo, QTranslator
    path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
    translator = QTranslator()
    if not translator.load("qtbase_fr", path):
        raise RuntimeError(f"qtbase_fr introuvable dans {path}")
    return path


def _check_icon():
    from .app import icon_path
    from PySide6.QtGui import QIcon
    path = icon_path()
    if not path.is_file() or QIcon(str(path)).isNull():
        raise RuntimeError(f"icône introuvable : {path}")
    return str(path)


def _check_window():
    from PySide6.QtCore import QSettings
    from PySide6.QtWidgets import QApplication
    from .i18n import Translator
    from .ui.download_dialog import DownloadDialog
    from .ui.main_window import MainWindow
    app = QApplication.instance() or QApplication(["manifestival"])
    with tempfile.TemporaryDirectory() as folder:
        settings = QSettings(str(Path(folder) / "settings.ini"), QSettings.IniFormat)
        manifest_file = Path(folder) / "manifest.json"
        manifest_file.write_text(json.dumps(SAMPLE_V3), encoding="utf-8")
        window = MainWindow(Translator("fr"), settings)
        window.show()
        window.load(str(manifest_file))
        end = time.monotonic() + 30
        while (window.manifest is None or window.manifest_thread is not None) and time.monotonic() < end:
            app.processEvents()
            time.sleep(0.01)
        if window.manifest is None:
            raise RuntimeError("le manifest d'essai ne s'est pas ouvert")
        window.model.set_all(True)
        window.settings.setValue("download/folder", folder)
        dialog = DownloadDialog(window.tr_, settings, window.manifest, window.manifest.pages, window)
        preview = dialog.preview_label.text()
        dialog.reject()
        window.loader.shutdown()
        window.close()
        app.processEvents()
        settings.sync()
        del settings
    return f"{app.platformName()} ; {preview.splitlines()[0] if preview else '?'}"


CHECKS = (
    ("translations", _check_translations, True),
    ("manifest", _check_manifest, True),
    ("tls", _check_tls, True),
    ("images", _check_images, True),
    ("icon", _check_icon, True),
    ("window", _check_window, True),
    ("qt_translations", _check_qt_translations, False),
)


def run(report_path) -> int:
    from PySide6.QtWidgets import QApplication
    QApplication.instance() or QApplication(["manifestival"])
    checks = []
    for name, check, required in CHECKS:
        try:
            detail, ok = str(check()), True
        except Exception as exc:  # le rapport dit ce qui manque
            detail, ok = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}", False
        checks.append({"name": name, "ok": ok, "required": required, "detail": detail})
    ok = all(c["ok"] for c in checks if c["required"])
    report = {"ok": ok, "frozen": bool(getattr(sys, "frozen", False)), "version": __version__,
              "platform": sys.platform, "checks": checks}
    Path(report_path).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if ok else 1
