#!/bin/sh
# macOS : double-cliquer sur ce fichier pour lancer l'Explorateur IIIF.
cd "$(dirname "$0")" || exit 1
exec python3 explorateur.py "$@"
