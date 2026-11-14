#!/bin/sh
# macOS : double-cliquer sur ce fichier pour lancer Manifestival.
cd "$(dirname "$0")" || exit 1
exec python3 lancer_manifestival.py "$@"
