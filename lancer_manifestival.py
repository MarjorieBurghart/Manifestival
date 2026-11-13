# -*- coding: utf-8 -*-
"""Lancer Manifestival.

En ligne de commande :
    python lancer_manifestival.py [adresse-du-manifest]

Depuis Spyder : ouvrir ce fichier et l'exécuter (F5). Manifestival s'ouvre
dans sa propre fenêtre, sans bloquer la console ; ses messages d'erreur
s'affichent dans la console. On peut relancer autant de fois qu'on veut.
"""
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Réglages pour un lancement depuis Spyder (ou un autre éditeur)

# Adresse ou fichier d'un manifest IIIF à ouvrir dès le démarrage ("" : aucun).
MANIFEST = ""

# True (recommandé) : Manifestival tourne dans un processus à part.
# False : dans la console elle-même, pour suivre le code au débogueur de
# Spyder (Déboguer, Ctrl+F5). Régler alors, dans Spyder, Outils >
# Préférences > Console IPython > Graphiques > Backend sur « Intégré »
# (Inline), puis redémarrer la console.
SEPARATE_PROCESS = True
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from manifestival.launch import in_console, launch  # noqa: E402

if __name__ == "__main__":
    # Les arguments ne valent que s'ils ont été donnés à ce fichier : dans
    # une console Jupyter, sys.argv est celui du noyau.
    own = bool(sys.argv) and Path(sys.argv[0]).name == Path(__file__).name
    arguments = (sys.argv[1:] if own else []) or ([MANIFEST] if MANIFEST else [])
    status = launch(arguments, separate=SEPARATE_PROCESS)
    if not in_console():
        sys.exit(status)
