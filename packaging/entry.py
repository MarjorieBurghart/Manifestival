# -*- coding: utf-8 -*-
"""Point d'entrée de la version prête à l'emploi (PyInstaller) : les
arguments vont tels quels à l'application (adresse d'un manifest, ou
``--self-test RAPPORT.json`` pour la vérification de la construction)."""
import sys

from manifestival.app import main

if __name__ == "__main__":
    sys.exit(main())
