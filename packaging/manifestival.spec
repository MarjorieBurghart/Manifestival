# -*- mode: python ; coding: utf-8 -*-
"""Recette PyInstaller de la version prête à l'emploi de Manifestival.

Lancée par ``packaging/build.py`` (qui construit, vérifie avec
``--self-test`` et zippe) ; à la main :
``python -m PyInstaller --noconfirm packaging/manifestival.spec``.

Un dossier, pas un exécutable unique : démarrage rapide, rien n'est
décompressé à chaque lancement, et les bibliothèques Qt restent des fichiers
distincts, remplaçables (LGPL). Les traductions et l'icône sont lues à côté
des modules (``Path(__file__).parent``) : elles sont placées sous
``manifestival/`` dans ``_internal/`` (``Contents/Resources`` d'un paquet
macOS)."""
import re
import sys
from pathlib import Path

from PyInstaller.utils.hooks import copy_metadata

ROOT = Path(SPECPATH).resolve().parent
NAME = "Manifestival"
VERSION = re.search(r'^__version__\s*=\s*"([^"]+)"',
                    (ROOT / "manifestival" / "__init__.py").read_text(encoding="utf-8"), re.MULTILINE).group(1)
ICON = ROOT / "manifestival" / "resources" / "manifestival.png"
DATA_FOLDERS = ("manifestival/i18n", "manifestival/resources")


def _data_files():
    files = []
    for folder in DATA_FOLDERS:
        for path in sorted((ROOT / folder).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                files.append((str(path), str(path.parent.relative_to(ROOT))))
    return files


datas = _data_files() + copy_metadata("certifi")

a = Analysis(
    [str(Path(SPECPATH) / "entry.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=["certifi"],
    runtime_hooks=[str(Path(SPECPATH) / "rthook_certificates.py")],
    excludes=[
        "tkinter", "_tkinter", "pytest", "_pytest", "pytestqt", "IPython", "ipykernel", "jupyter_client",
        "spyder_kernels", "matplotlib", "numpy", "PIL",
        # Qt WebEngine (navigateur intégré) est gardé, avec Qt Quick et QML
        # dont il dépend.
        "PySide6.QtTest", "PySide6.Qt3DCore", "PySide6.QtMultimedia",
        "PySide6.QtPdf", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtBluetooth",
        "PySide6.QtSql", "PySide6.QtXml",
    ],
    noarchive=False,
)

# Greffons Qt inutiles à une application de bureau, et les bibliothèques
# qu'eux seuls emportent : clavier virtuel, images PDF, modules Qt inutilisés,
# plateformes embarquées (eglfs, linuxfb, vnc…), thème GTK et la pile GTK de
# la machine de construction (sous Linux : conflits de versions avec celle
# du système ; Qt garde ses propres boîtes de dialogue). Gardés : windows,
# cocoa, xcb, wayland, offscreen (vérification), formats d'images (JPEG,
# PNG, TIFF, WebP…), méthodes de saisie, portails.
UNUSED_QT = re.compile(
    r"plugins/(platforminputcontexts/[^/]*virtualkeyboard|imageformats/[^/]*qpdf|platformthemes/[^/]*gtk3"
    r"|platforms/[^/]*(eglfs|linuxfb|vnc|minimal|vkkhrdisplay)|egldeviceintegrations/|generic/)"
    r"|Qt6?(VirtualKeyboard|Pdf|EglFS|EglFsKms|Quick3D|QuickTimeline|QuickParticles|Charts|DataVisualization"
    r"|3D|Multimedia|Sensors|SerialPort|Bluetooth|Nfc|RemoteObjects|Scxml|StateMachine|TextToSpeech"
    r"|Location|Graphs|SpatialAudio|HttpServer|WebSockets|WebView|ShaderTools)")
UNUSED_LINUX = re.compile(
    r"^lib(gtk-3|gdk-3|atk-1\.0|atk-bridge-2\.0|atspi|gdk_pixbuf-2\.0|cairo-gobject|cairo|pangocairo-1\.0"
    r"|pangoft2-1\.0|pango-1\.0|epoxy|pixman-1|thai|datrie|fribidi|Xcomposite|Xdamage|Xinerama|Xi|Xrandr"
    r"|Xcursor)\.so")


# Le navigateur intégré est Qt WebEngine « widgets » : ni modules QML, ni
# styles Qt Quick Controls (Qt Quick et QML eux-mêmes restent : Qt WebEngine
# en dépend).
UNUSED_QUICK = re.compile(
    r"(^|/)PySide6/Qt/qml/|Qt6?(QuickControls2|QuickDialogs2|QuickTemplates2|QuickLayouts|QuickShapes"
    r"|QuickEffects|QuickVectorImage|Labs[A-Za-z]*)")
# Traductions : celles des sept langues de Manifestival seulement (Qt, et les
# fichiers de langue de Chromium ; en-US est celui par défaut).
LANGS = ("fr", "en", "de", "it", "cs", "ru", "ja")
TRANSLATION = re.compile(r"PySide6/Qt/translations/(.+)$")
WEBENGINE_LOCALE = re.compile(r"^qtwebengine_locales/([A-Za-z-]+)\.pak$")
QT_CATALOGUE = re.compile(r"^(qt|qtbase|qtwebengine)_([a-z]{2})(_[A-Za-z]+)?\.qm$")


def _wanted_translation(path):
    locale = WEBENGINE_LOCALE.match(path)
    if locale:
        return locale.group(1) == "en-US" or locale.group(1).split("-")[0] in LANGS
    catalogue = QT_CATALOGUE.match(path)
    return bool(catalogue and catalogue.group(2) in LANGS)


def _wanted(entry):
    name = entry[0].replace("\\", "/")
    if UNUSED_QT.search(name) or UNUSED_QUICK.search(name):
        return False
    translation = TRANSLATION.search(name)
    if translation and not _wanted_translation(translation.group(1)):
        return False
    return not (sys.platform.startswith("linux") and "/" not in name and UNUSED_LINUX.match(name))


a.binaries = [entry for entry in a.binaries if _wanted(entry)]
a.datas = [entry for entry in a.datas if _wanted(entry)]
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    console=False,              # application fenêtrée (pas de console sous Windows)
    upx=False,                  # UPX : fausses alertes des antivirus
    strip=False,
    icon=str(ICON),
    disable_windowed_traceback=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False, strip=False)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=str(ICON),
        bundle_identifier="io.github.marjorieburghart.manifestival",
        version=VERSION,
        info_plist={
            "CFBundleName": NAME,
            "CFBundleDisplayName": NAME,
            "CFBundleShortVersionString": VERSION,
            "CFBundleVersion": VERSION,
            "NSHighResolutionCapable": True,
            "NSHumanReadableCopyright": NAME,
        },
    )
