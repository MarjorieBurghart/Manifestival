# -*- coding: utf-8 -*-
"""Fin de session propre : fenêtres fermées et objets Qt détruits avant
l'arrêt de Python. Sans cela, quand Qt WebEngine (navigateur intégré) a
servi, des restes détruits dans le désordre à la sortie font planter le
processus (vu avec Python 3.10), alors que tous les tests ont réussi."""
from __future__ import annotations

import gc
import os
import sys

import pytest

_exit_status = {}


@pytest.fixture(scope="session", autouse=True)
def qt_clean_exit():
    yield
    try:
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtWidgets import QApplication
    except ImportError:
        return
    app = QApplication.instance()
    if app is None:
        return
    for widget in app.topLevelWidgets():
        widget.close()
        widget.deleteLater()
    for _ in range(3):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        app.processEvents()
        gc.collect()


def pytest_sessionfinish(session, exitstatus):
    _exit_status["code"] = int(exitstatus)


def pytest_unconfigure(config):
    """Avec Python 3.10 et 3.11, la destruction finale de Python peut
    planter une fois Qt WebEngine utilisé (défaut de PySide6, voir
    manifestival.app.exit_now) : on quitte avec le code de pytest, résumé
    déjà affiché, sans cette phase. Un échec reste un échec."""
    if sys.version_info < (3, 12) and "PySide6.QtWebEngineCore" in sys.modules and "code" in _exit_status:
        for stream in (sys.stdout, sys.stderr):
            stream.flush()
        os._exit(_exit_status["code"])
