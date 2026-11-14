# -*- coding: utf-8 -*-
"""Construction de la version prête à l'emploi de Manifestival, sur le
système où il est lancé (Windows, macOS ou Linux) :

1. PyInstaller, d'après ``packaging/manifestival.spec`` (un dossier) ;
2. le dossier est placé sous ``Manifestival-<version>/`` ;
3. vérification de l'exécutable construit : ``Manifestival --self-test``
   (traductions, manifest, certificats HTTPS, formats d'images, icône,
   fenêtre) ; la construction échoue si une vérification requise échoue, ou
   si l'exécutable a laissé des fichiers dans le dossier ;
4. à côté de l'application, ``LISEZMOI.txt`` (français et anglais) et les
   textes des licences (``licences/``) ;
5. archive ``Manifestival-<version>-<plateforme>.zip`` (liens symboliques et
   droits gardés) et son empreinte SHA-256.

    python packaging/build.py [--output DOSSIER]

Il faut, en plus de requirements.txt : ``packaging/requirements.txt``
(PyInstaller, Pillow pour l'icône, certifi). Sans écran, la vérification se
fait avec ``QT_QPA_PLATFORM=offscreen`` ; une autre valeur (``xcb``,
``windows``, ``cocoa``) vérifie aussi le module d'affichage du système."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = "Manifestival"
README = ROOT / "packaging" / "LISEZMOI.txt"
LICENCES = ROOT / "packaging" / "licences"


def app_version(root=ROOT) -> str:
    """Version, lue dans ``__version__`` de ``manifestival/__init__.py``
    (sans importer le paquet)."""
    text = (Path(root) / "manifestival" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'^__version__\s*=\s*"([^"]+)"', text, re.MULTILINE).group(1)


def platform_tag(system=sys.platform, machine=None) -> str:
    """Plateforme dans le nom de l'archive : ``windows-x64``, ``macos-arm64``,
    ``linux-x64``… (une architecture inconnue garde son nom, en minuscules)."""
    machine = (machine or platform.machine()).lower()
    arch = {"amd64": "x64", "x86_64": "x64", "arm64": "arm64", "aarch64": "arm64"}.get(machine, machine)
    if system == "win32":
        return f"windows-{arch}"
    if system == "darwin":
        return f"macos-{arch}"
    return f"linux-{arch}"


def archive_name(version, tag) -> str:
    return f"{NAME}-{version}-{tag}.zip"


def executable_path(folder, system=sys.platform) -> Path:
    """``Manifestival.exe`` sous Windows, ``Manifestival.app/Contents/MacOS/
    Manifestival`` sous macOS, ``Manifestival`` ailleurs."""
    folder = Path(folder)
    if system == "win32":
        return folder / f"{NAME}.exe"
    if system == "darwin":
        return folder / f"{NAME}.app" / "Contents" / "MacOS" / NAME
    return folder / NAME


def report_problems(report, version) -> list:
    """Ce qui ne va pas dans le rapport de ``--self-test`` (vide si tout va
    bien) : version, version construite, vérifications requises."""
    problems = []
    if report.get("version") != version:
        problems.append(f"version {report.get('version')} au lieu de {version}")
    if report.get("frozen") is not True:
        problems.append("ce n'est pas la version construite qui a été vérifiée")
    for check in report.get("checks", []):
        if check.get("required") and not check.get("ok"):
            problems.append(f"{check.get('name')} : {check.get('detail', '')}")
    if not report.get("checks"):
        problems.append("aucune vérification")
    if report.get("ok") is not True and not problems:
        problems.append("rapport en échec")
    return problems


def zip_folder(folder, archive):
    """Zippe ``folder`` sous son propre nom. Les liens symboliques restent
    des liens (jamais suivis) ; les droits Unix sont gardés."""
    folder = Path(folder)
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        def add(path):
            name = path.relative_to(folder.parent).as_posix()
            info = os.lstat(path)
            if stat.S_ISLNK(info.st_mode):
                entry = zipfile.ZipInfo(name)
                entry.create_system = 3
                entry.external_attr = (stat.S_IFLNK | 0o777) << 16
                zf.writestr(entry, os.readlink(path))
            elif stat.S_ISDIR(info.st_mode):
                entry = zipfile.ZipInfo(name + "/")
                entry.create_system = 3
                entry.external_attr = ((stat.S_IFDIR | stat.S_IMODE(info.st_mode)) << 16) | 0x10
                zf.writestr(entry, b"")
                for child in sorted(path.iterdir()):
                    add(child)
            else:
                zf.write(path, name)
        add(folder)


def write_checksum(archive) -> Path:
    """``<archive>.sha256``, au format de ``sha256sum``."""
    archive = Path(archive)
    digest = hashlib.sha256()
    with archive.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    checksum = archive.with_name(archive.name + ".sha256")
    checksum.write_text(f"{digest.hexdigest()}  {archive.name}\n", encoding="ascii")
    return checksum


def broken_links(folder) -> list:
    folder = Path(folder)
    return sorted(p.relative_to(folder).as_posix() for p in folder.rglob("*") if p.is_symlink() and not p.exists())


def add_documents(folder, version, system=sys.platform):
    """LISEZMOI (à la version construite) et dossier ``licences``. Sous
    Windows : fins de ligne CRLF et marque UTF-8, pour le Bloc-notes."""
    folder = Path(folder)
    encoding, newline = ("utf-8-sig", "\r\n") if system == "win32" else ("utf-8", "\n")

    def write(path, text):
        path.write_text(text.replace("\r\n", "\n"), encoding=encoding, newline=newline)

    write(folder / README.name, README.read_text(encoding="utf-8").replace("{version}", version))
    licences = folder / "licences"
    licences.mkdir(exist_ok=True)
    write(licences / "Manifestival.txt", (ROOT / "LICENSE").read_text(encoding="utf-8"))
    for text in sorted(LICENCES.glob("*.txt")):
        write(licences / text.name, text.read_text(encoding="utf-8"))


def _files(folder):
    return sorted(p.relative_to(folder).as_posix() for p in Path(folder).rglob("*"))


def self_test(executable, report_path) -> list:
    """``Manifestival --self-test`` sur l'exécutable construit ; rend les
    problèmes (liste vide si tout va bien)."""
    environment = dict(os.environ)
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")
    completed = subprocess.run([str(executable), "--self-test", str(report_path)], env=environment,
                               capture_output=True, timeout=600)
    if not Path(report_path).is_file():
        output = (completed.stdout + completed.stderr).decode("utf-8", "replace")[-3000:]
        return [f"pas de rapport (code {completed.returncode}) : {output}"]
    report = json.loads(Path(report_path).read_text(encoding="utf-8"))
    problems = report_problems(report, app_version())
    if completed.returncode != 0 and not problems:
        problems.append(f"code de sortie {completed.returncode}")
    return problems


def build(output) -> Path:
    """Toutes les étapes, dans ``output`` ; rend le chemin de l'archive.
    S'arrête (``SystemExit``) si la version construite ne passe pas la
    vérification : aucune archive n'est alors faite. Sur GitHub Actions, les
    chemins vont dans ``GITHUB_OUTPUT``."""
    output = Path(output).resolve()
    version, tag = app_version(), platform_tag()
    work, dist, stage = output / "work", output / "dist", output / "stage"
    for folder in (dist, stage):
        if folder.exists():
            shutil.rmtree(folder)
    subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--log-level", "WARN",
                    "--workpath", str(work), "--distpath", str(dist),
                    str(ROOT / "packaging" / "manifestival.spec")], check=True, cwd=ROOT)
    folder = stage / f"{NAME}-{version}"
    folder.mkdir(parents=True)
    if sys.platform == "darwin":
        (dist / f"{NAME}.app").rename(folder / f"{NAME}.app")
    else:
        for entry in (dist / NAME).iterdir():
            entry.rename(folder / entry.name)
    add_documents(folder, version)
    problems = [f"lien cassé : {link}" for link in broken_links(folder)]
    before = _files(folder)
    report = output / f"self-test-{tag}.json"
    problems += self_test(executable_path(folder), report)
    added = sorted(set(_files(folder)) - set(before))
    if added:
        problems.append("fichiers laissés dans le dossier : " + ", ".join(added[:20]))
    if problems:
        raise SystemExit("Vérification de la version construite en échec :\n- " + "\n- ".join(problems))
    archive = output / archive_name(version, tag)
    if archive.exists():
        archive.unlink()
    if sys.platform == "darwin":
        # ditto : l'outil d'Apple, qui garde tout ce qu'un paquet .app contient.
        subprocess.run(["ditto", "-c", "-k", "--keepParent", str(folder), str(archive)], check=True)
    else:
        zip_folder(folder, archive)
    write_checksum(archive)
    if os.environ.get("GITHUB_OUTPUT"):
        with open(os.environ["GITHUB_OUTPUT"], "a", encoding="utf-8") as stream:
            stream.write(f"archive={archive}\nchecksum={archive}.sha256\nreport={report}\n")
    return archive


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Construit la version prête à l'emploi de Manifestival.")
    parser.add_argument("--output", default=str(ROOT / "build" / "binaries"),
                        help="dossier de travail et de l'archive (build/binaries par défaut)")
    args = parser.parse_args(argv)
    archive = build(args.output)
    print(f"{archive} ({archive.stat().st_size // (1 << 20)} Mo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
