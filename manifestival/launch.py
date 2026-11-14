# -*- coding: utf-8 -*-
"""Lancement depuis un éditeur Python (Spyder, Jupyter, IPython…).

Dans la console IPython de Spyder, une application Qt se heurte à ce qui
tourne déjà dans la console : une autre bibliothèque Qt (PyQt5, que Spyder
emploie pour ses graphiques Matplotlib), une boucle d'événements déjà
active, une application Qt laissée par l'exécution précédente.
:func:`launch` évite ces écueils en ouvrant Manifestival dans un processus
à part, dont les messages (erreurs comprises) s'affichent dans la console ;
on peut relancer autant de fois qu'on veut. Hors console, ou à la demande,
l'application tourne dans le processus courant.
"""
from __future__ import annotations

from pathlib import Path
import os
import subprocess
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
OTHER_QT = ("PyQt5", "PyQt6", "PySide2")


def in_console() -> bool:
    """Est-on dans une console IPython (Spyder, Jupyter, qtconsole) ?"""
    if "ipykernel" in sys.modules or "spyder_kernels" in sys.modules:
        return True
    try:
        from IPython import get_ipython
    except ImportError:
        return False
    return get_ipython() is not None


def other_qt_loaded() -> list[str]:
    """Bibliothèques Qt autres que PySide6 déjà chargées : PySide6 ne peut
    pas cohabiter avec elles dans le même processus."""
    return [name for name in OTHER_QT if name in sys.modules]


def command(args) -> list[str]:
    return [sys.executable, "-m", "manifestival", *[a for a in args if a]]


def _forward(stream, out):
    for line in stream:
        print(line, end="", file=out, flush=True)
    stream.close()


def run_separately(args=(), *, wait: bool = False):
    """Manifestival dans un processus à part ; ses messages sont recopiés
    dans la console. Rend le processus."""
    env = dict(os.environ)
    env["PYTHONUTF8"] = "1"
    env["PYTHONUNBUFFERED"] = "1"
    for name in ("QT_API", "MPLBACKEND"):  # réglages de la console Spyder
        env.pop(name, None)
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(command(args), cwd=str(ROOT), env=env, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", **kwargs)
    thread = threading.Thread(target=_forward, args=(process.stdout, sys.stdout), daemon=True)
    thread.start()
    if wait:
        process.wait()
        thread.join(5)
    return process


def launch(args=(), *, separate: bool = True) -> int:
    """Lance Manifestival. Dans une console IPython, ``separate`` choisit
    un processus à part (par défaut) ou la console elle-même (pour suivre
    le code au débogueur) ; hors console, l'application tourne ici."""
    args = [a for a in args if a]
    if in_console():
        conflicts = other_qt_loaded()
        if separate or conflicts:
            if not separate:
                print(f"Manifestival : {', '.join(conflicts)} est déjà chargé dans cette console ; "
                      "lancement dans un processus à part.\n"
                      f"Manifestival: {', '.join(conflicts)} is already loaded in this console; "
                      "starting in a separate process.")
            run_separately(args)
            print("Manifestival s'ouvre dans une fenêtre à part. / Manifestival opens in its own window.")
            return 0
    from .app import main
    return main(["manifestival", *args])
