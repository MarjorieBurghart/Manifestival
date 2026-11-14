# -*- coding: utf-8 -*-
"""Lancement : ligne de commande, console IPython (Spyder, Jupyter)."""
from __future__ import annotations

import sys
import types

from iiif_test_server import ROOT

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication

from manifestival import app as app_module
from manifestival import launch as launch_module

LAUNCHER = ROOT / "lancer_manifestival.py"


def test_console_and_other_qt_detection(monkeypatch):
    for name in ("ipykernel", "spyder_kernels", "PyQt5", "PyQt6", "PySide2"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setitem(sys.modules, "IPython", None)  # IPython absent
    assert not launch_module.in_console()
    assert launch_module.other_qt_loaded() == []
    monkeypatch.setitem(sys.modules, "spyder_kernels", types.ModuleType("spyder_kernels"))
    monkeypatch.setitem(sys.modules, "PyQt5", types.ModuleType("PyQt5"))
    assert launch_module.in_console()
    assert launch_module.other_qt_loaded() == ["PyQt5"]


def test_command_runs_the_package_with_this_python():
    assert launch_module.command(["", "https://x/m.json"]) == [
        sys.executable, "-m", "manifestival", "https://x/m.json"]


def _record(monkeypatch, console: bool, qt=()):
    calls = []
    monkeypatch.setattr(launch_module, "in_console", lambda: console)
    monkeypatch.setattr(launch_module, "other_qt_loaded", lambda: list(qt))
    monkeypatch.setattr(launch_module, "run_separately", lambda args: calls.append(("separate", args)))
    monkeypatch.setattr(app_module, "main", lambda argv: calls.append(("here", argv)) or 0)
    return calls


def test_in_a_console_the_app_runs_in_its_own_process(monkeypatch, capsys):
    calls = _record(monkeypatch, console=True)
    assert launch_module.launch(["m.json"]) == 0
    assert calls == [("separate", ["m.json"])]
    assert "Manifestival" in capsys.readouterr().out


def test_in_process_on_request_unless_another_qt_is_loaded(monkeypatch, capsys):
    calls = _record(monkeypatch, console=True)
    launch_module.launch([], separate=False)
    assert calls == [("here", ["manifestival"])]
    calls = _record(monkeypatch, console=True, qt=["PyQt5"])
    launch_module.launch([], separate=False)
    assert calls == [("separate", [])]
    assert "PyQt5" in capsys.readouterr().out


def test_outside_a_console_the_app_runs_here(monkeypatch):
    calls = _record(monkeypatch, console=False)
    launch_module.launch(["m.json"])
    assert calls == [("here", ["manifestival", "m.json"])]


def test_child_messages_reach_the_console(monkeypatch, capsys):
    monkeypatch.setattr(launch_module, "command",
                        lambda args: [sys.executable, "-c", "import sys; print('bonjour', sys.argv[1:])", *args])
    process = launch_module.run_separately(["a"], wait=True)
    assert process.returncode == 0
    assert "bonjour ['a']" in capsys.readouterr().out


def _run_launcher(monkeypatch, argv, console):
    seen = []
    monkeypatch.setattr(launch_module, "launch", lambda args, separate: seen.append((args, separate)) or 0)
    monkeypatch.setattr(launch_module, "in_console", lambda: console)
    monkeypatch.setattr(sys, "argv", argv)
    code = compile(LAUNCHER.read_text(encoding="utf-8"), str(LAUNCHER), "exec")
    try:  # comme runfile() de Spyder ; runpy changerait sys.argv[0]
        exec(code, {"__name__": "__main__", "__file__": str(LAUNCHER)})
    except SystemExit as exc:
        seen.append(("exit", exc.code))
    return seen


def test_launcher_arguments(monkeypatch):
    # Ligne de commande, ou Spyder avec des options de ligne de commande.
    assert _run_launcher(monkeypatch, [str(LAUNCHER), "m.json"], False) == [(["m.json"], True), ("exit", 0)]
    # Console Jupyter : sys.argv est celui du noyau, il ne compte pas.
    assert _run_launcher(monkeypatch, ["ipykernel_launcher.py", "-f", "k.json"], True) == [([], True)]


def test_start_reuses_the_running_qt_application(qtbot, qapp, tmp_path, monkeypatch):
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.IniFormat)
    app, window = app_module.start(["manifestival"], settings)
    qtbot.addWidget(window)
    assert app is QApplication.instance() is qapp
    assert window.isVisible() and window.windowTitle() == "Manifestival"
    # La console fait tourner Qt : main() rend la main sans bloquer.
    monkeypatch.setattr(app_module, "qt_loop_running", lambda: True)
    assert app_module.main(["manifestival"], settings) == 0
    assert app_module._windows and app_module._windows[-1].isVisible()
    for w in list(app_module._windows):
        w.loader.shutdown()
        w.close()
    window.loader.shutdown()
