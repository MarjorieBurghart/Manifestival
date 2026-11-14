# -*- coding: utf-8 -*-
"""Recette de la version prête à l'emploi et workflows GitHub Actions."""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import zipfile

import pytest

from iiif_test_server import ROOT

import manifestival
from manifestival import self_test

spec = importlib.util.spec_from_file_location("manifestival_build", ROOT / "packaging" / "build.py")
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)
WORKFLOWS = ROOT / ".github" / "workflows"


def test_version_is_read_without_importing():
    assert build.app_version() == manifestival.__version__


def test_platform_tags_and_names():
    assert build.platform_tag("win32", "AMD64") == "windows-x64"
    assert build.platform_tag("darwin", "arm64") == "macos-arm64"
    assert build.platform_tag("darwin", "x86_64") == "macos-x64"
    assert build.platform_tag("linux", "aarch64") == "linux-arm64"
    assert build.archive_name("0.1.0", "linux-x64") == "Manifestival-0.1.0-linux-x64.zip"
    assert build.executable_path("d", "win32").name == "Manifestival.exe"
    assert build.executable_path("d", "darwin").as_posix().endswith("Manifestival.app/Contents/MacOS/Manifestival")


def test_report_problems():
    good = {"ok": True, "frozen": True, "version": "0.1.0",
            "checks": [{"name": "window", "ok": True, "required": True},
                       {"name": "qt_translations", "ok": False, "required": False}]}
    assert build.report_problems(good, "0.1.0") == []        # l'informatif ne bloque pas
    assert build.report_problems(dict(good, frozen=False), "0.1.0")
    assert build.report_problems(dict(good, version="0.0.9"), "0.1.0")
    bad = dict(good, ok=False, checks=[{"name": "window", "ok": False, "required": True, "detail": "xcb"}])
    assert build.report_problems(bad, "0.1.0") == ["window : xcb"]
    assert build.report_problems(dict(good, checks=[]), "0.1.0")


@pytest.mark.skipif(sys.platform == "win32", reason="liens symboliques")
def test_zip_keeps_links_and_modes(tmp_path):
    folder = tmp_path / "Manifestival-0.1.0"
    (folder / "lib").mkdir(parents=True)
    (folder / "lib" / "libQt.so.6").write_bytes(b"x")
    (folder / "lib" / "libQt.so").symlink_to("libQt.so.6")
    (folder / "Manifestival").write_bytes(b"#!")
    os.chmod(folder / "Manifestival", 0o755)
    archive = tmp_path / "a.zip"
    build.zip_folder(folder, archive)
    with zipfile.ZipFile(archive) as zf:
        infos = {i.filename: i for i in zf.infolist()}
        link = infos["Manifestival-0.1.0/lib/libQt.so"]
        assert (link.external_attr >> 16) & 0o170000 == 0o120000
        assert zf.read(link) == b"libQt.so.6"
        assert (infos["Manifestival-0.1.0/Manifestival"].external_attr >> 16) & 0o777 == 0o755
    checksum = build.write_checksum(archive)
    assert checksum.read_text().endswith("  a.zip\n") and len(checksum.read_text().split()[0]) == 64
    assert build.broken_links(folder) == []
    (folder / "lib" / "dead").symlink_to("nowhere")
    assert build.broken_links(folder) == ["lib/dead"]


def test_documents_next_to_the_application(tmp_path):
    build.add_documents(tmp_path, "9.9.9", system="win32")
    readme = (tmp_path / "LISEZMOI.txt").read_bytes()
    assert readme.startswith(b"\xef\xbb\xbf") and b"\r\n" in readme and b"MANIFESTIVAL 9.9.9" in readme
    assert sorted(p.name for p in (tmp_path / "licences").iterdir()) == [
        "GPL-3.0.txt", "LGPL-3.0.txt", "MPL-2.0.txt", "Manifestival.txt", "Python.txt"]
    mit = (tmp_path / "licences" / "Manifestival.txt").read_text(encoding="utf-8-sig")
    assert "MIT License" in mit and "Copyright (c) 2026 Marjorie Burghart" in mit
    assert "licence MIT" in readme.decode("utf-8-sig")
    for name in ("Python.txt", "LGPL-3.0.txt", "MPL-2.0.txt"):
        assert name in readme.decode("utf-8-sig")


def test_data_files_of_the_recipe_exist():
    recipe = (ROOT / "packaging" / "manifestival.spec").read_text(encoding="utf-8")
    assert '"manifestival/i18n", "manifestival/resources"' in recipe
    assert (ROOT / "manifestival" / "resources" / "manifestival.png").is_file()
    assert len(list((ROOT / "manifestival" / "i18n").glob("*.json"))) == 7
    assert "entry.py" in recipe and "rthook_certificates.py" in recipe


def test_self_test_from_the_sources(qapp, tmp_path):
    report_path = tmp_path / "report.json"
    assert self_test.run(report_path) == 0
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["ok"] and report["frozen"] is False and report["version"] == manifestival.__version__
    assert {c["name"] for c in report["checks"]} >= {"translations", "manifest", "tls", "images", "icon", "window"}
    assert build.report_problems(dict(report, frozen=True), manifestival.__version__) == []


def test_tests_workflow():
    text = (WORKFLOWS / "tests.yml").read_text(encoding="utf-8")
    for needle in ("ubuntu-latest", "windows-latest", "macos-latest", '"3.10"', "QT_QPA_PLATFORM: offscreen",
                   "python -m ruff check .", "python -m pytest", "-c constraints.txt"):
        assert needle in text, needle
    assert text.index("ruff check") < text.index("python -m pytest")


def test_binaries_workflow():
    text = (WORKFLOWS / "binaries.yml").read_text(encoding="utf-8")
    for needle in ("windows-latest", "macos-latest", "macos-15-intel", "ubuntu-22.04", "xvfb-run -a python packaging/build.py",
                   "packaging/requirements.txt", "types: [published]", "gh release upload",
                   'pattern: Manifestival-*', '"Manifestival-$version-"*'):
        assert needle in text, needle


def test_constraints_pin_what_the_requirements_name():
    pins = {line.split("==")[0].lower() for line in (ROOT / "constraints.txt").read_text().splitlines()
            if "==" in line and not line.startswith("#")}
    assert {"pyside6", "pytest", "pytest-qt", "ruff", "pillow", "certifi"} <= pins
    assert "pyinstaller==" in (ROOT / "packaging" / "requirements.txt").read_text()
