# -*- coding: utf-8 -*-
"""Lancement de l'application : ``python lancer_manifestival.py [adresse-du-manifest]``."""
from __future__ import annotations

import sys


def main(argv=None) -> int:
    argv = list(sys.argv if argv is None else argv)
    try:
        from PySide6.QtCore import QSettings, QTimer
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print("PySide6 n'est pas installé : pip install -r requirements.txt\n"
              "PySide6 is not installed: pip install -r requirements.txt", file=sys.stderr)
        return 1

    from . import APP_NAME, __version__
    from .i18n import Translator, system_language
    from .ui.main_window import MainWindow

    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName("Manifestival")
    settings = QSettings("Manifestival", "Manifestival")
    language = str(settings.value("language", "") or system_language())
    window = MainWindow(Translator(language), settings)
    window.show()
    if len(argv) > 1 and argv[1].strip():
        QTimer.singleShot(0, lambda: window.load(argv[1]))
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
