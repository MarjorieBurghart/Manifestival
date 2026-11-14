# -*- coding: utf-8 -*-
"""Lancement de l'application : ``python lancer_manifestival.py [adresse]``
ou ``python -m manifestival [adresse]``.

:func:`start` ouvre la fenêtre en réutilisant l'application Qt si elle
existe déjà (console IPython de Spyder, par exemple) ; :func:`main` y ajoute
la boucle d'événements, sauf si la console en fait déjà tourner une.
"""
from __future__ import annotations

from pathlib import Path
import sys

ICON = Path(__file__).resolve().parent / "resources" / "manifestival.png"


def icon_path() -> Path:
    return ICON


# Fenêtres ouvertes sans boucle d'événements propre (console IPython) : une
# référence les garde en vie après le retour de main().
_windows: list = []


def _pyside6_missing() -> bool:
    try:
        import PySide6  # noqa: F401
    except ImportError:
        print("PySide6 n'est pas installé : pip install -r requirements.txt\n"
              "PySide6 is not installed: pip install -r requirements.txt", file=sys.stderr)
        return True
    return False


def qt_loop_running() -> bool:
    """La console IPython (Spyder, Jupyter…) fait-elle déjà tourner une
    boucle d'événements Qt (``%gui qt``, graphiques en backend Qt) ?"""
    if "IPython" not in sys.modules:
        return False
    try:
        from IPython import get_ipython
    except ImportError:
        return False
    shell = get_ipython()
    if shell is None:
        return False
    loop = getattr(shell, "active_eventloop", None)
    if not loop:
        loop = getattr(getattr(shell, "kernel", None), "eventloop", None)
    name = loop if isinstance(loop, str) else getattr(loop, "__name__", "")
    return "qt" in str(name or "").lower()


def start(argv=None, settings=None):
    """Fenêtre principale ouverte, avec le manifest ``argv[1]`` s'il est
    donné ; rend ``(application, fenêtre)``. ``settings`` : réglages à
    employer (QSettings), ceux de l'utilisateur par défaut."""
    argv = list(sys.argv if argv is None else argv) or ["manifestival"]
    from PySide6.QtCore import QSettings, QTimer
    from PySide6.QtWidgets import QApplication

    from . import APP_NAME, __version__
    from .i18n import Translator, system_language
    from .ui.main_window import MainWindow

    from .ui import browser
    if QApplication.instance() is None and browser.available():
        # Qt WebEngine (navigateur intégré) le demande avant l'application.
        from PySide6.QtCore import QCoreApplication, Qt
        QCoreApplication.setAttribute(Qt.AA_ShareOpenGLContexts)
    app = QApplication.instance() or QApplication(argv)
    if ICON.is_file():
        from PySide6.QtGui import QIcon
        app.setWindowIcon(QIcon(str(ICON)))
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Manifestival")
    if settings is None:
        settings = QSettings("Manifestival", "Manifestival")
    language = str(settings.value("language", "") or system_language())
    from PySide6.QtCore import QStandardPaths
    from .refusals import RefusalLog
    data_dir = QStandardPaths.writableLocation(QStandardPaths.AppDataLocation)
    refusals = RefusalLog(Path(data_dir) / "refusals.json" if data_dir else None)
    window = MainWindow(Translator(language), settings, refusals=refusals,
                        browser_storage=Path(data_dir) / "browser" if data_dir else None)
    window.show()
    window.raise_()
    window.activateWindow()
    source = argv[1].strip() if len(argv) > 1 and argv[1] else ""
    if source:
        QTimer.singleShot(0, lambda: window.load(source))
    return app, window


def main(argv=None, settings=None) -> int:
    argv = list(sys.argv if argv is None else argv) or ["manifestival"]
    # macOS ancien : le Finder ajoute « -psn_… » aux arguments.
    argv = [argv[0]] + [a for a in argv[1:] if not a.startswith("-psn_")]
    if _pyside6_missing():
        return 1
    if len(argv) > 2 and argv[1] == "--self-test":
        from .self_test import run
        return run(argv[2])
    app, window = start(argv, settings)
    if qt_loop_running():
        # La console fait déjà tourner Qt : la fenêtre vit sans la bloquer.
        _windows[:] = [w for w in _windows if w.isVisible()] + [window]
        return 0
    status = app.exec()
    if window.browser is not None and sys.version_info < (3, 12):
        exit_now(status, window.settings)
    return status


def exit_now(status: int, settings=None):
    """Quitte sans la phase de destruction de Python. Avec Python 3.10 et
    3.11, quand Qt WebEngine (navigateur intégré) a servi, cette phase peut
    planter (défaut de PySide6/Qt WebEngine, sans conséquence sur le
    travail fait) ; à partir de Python 3.12, elle se passe bien. Les
    réglages et les sorties sont enregistrés d'abord."""
    import os
    if settings is not None:
        settings.sync()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(int(status))


if __name__ == "__main__":
    sys.exit(main())
