# -*- coding: utf-8 -*-
"""Icône de Manifestival : des feuillets de manuscrit sur un carré bordeaux
aux coins arrondis, avec une pastille « téléchargement ».

    python packaging/make_icon.py     # réécrit manifestival/resources/manifestival.png

Il faut Pillow (packaging/requirements.txt). À relancer si le dessin change."""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SIZE = 512          # PyInstaller en tire les tailles des icônes Windows et macOS
WORK = 4 * SIZE     # dessin plus grand, puis réduction : bords lisses
BORDEAUX = (128, 32, 48, 255)
PARCHMENT = (244, 233, 208, 255)
SHADOW = (90, 20, 32, 255)
INK = (122, 92, 60, 255)
GOLD = (214, 160, 60, 255)


def make_icon(target=ROOT / "manifestival" / "resources" / "manifestival.png"):
    s = WORK
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((0, 0, s - 1, s - 1), radius=int(s * 0.2), fill=BORDEAUX)
    # Trois feuillets décalés, celui de devant réglé de lignes d'écriture.
    w, h = int(s * 0.46), int(s * 0.60)
    for i, (dx, dy) in enumerate(((0.30, 0.13), (0.24, 0.18), (0.18, 0.23))):
        x, y = int(s * dx), int(s * dy)
        draw.rounded_rectangle((x + s * 0.012, y + s * 0.012, x + w + s * 0.012, y + h + s * 0.012),
                               radius=int(s * 0.025), fill=SHADOW)
        draw.rounded_rectangle((x, y, x + w, y + h), radius=int(s * 0.025), fill=PARCHMENT)
    x, y = int(s * 0.18), int(s * 0.23)
    draw.rectangle((x + s * 0.05, y + s * 0.06, x + s * 0.13, y + s * 0.14), fill=BORDEAUX)  # lettrine
    for k in range(9):
        ly = y + s * 0.07 + k * s * 0.052
        left = x + (s * 0.16 if k < 2 else s * 0.05)
        draw.rounded_rectangle((left, ly, x + w - s * 0.05, ly + s * 0.014), radius=int(s * 0.007), fill=INK)
    # Pastille dorée avec une flèche vers le bas.
    cx, cy, r = s * 0.72, s * 0.74, s * 0.17
    draw.ellipse((cx - r - s * 0.015, cy - r - s * 0.015, cx + r + s * 0.015, cy + r + s * 0.015), fill=BORDEAUX)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=GOLD)
    shaft = s * 0.035
    draw.rectangle((cx - shaft, cy - r * 0.55, cx + shaft, cy + r * 0.12), fill=(255, 255, 255, 255))
    draw.polygon([(cx - r * 0.5, cy + r * 0.02), (cx + r * 0.5, cy + r * 0.02), (cx, cy + r * 0.58)],
                 fill=(255, 255, 255, 255))
    target.parent.mkdir(parents=True, exist_ok=True)
    image.resize((SIZE, SIZE), Image.LANCZOS).save(target, optimize=True)
    return target


if __name__ == "__main__":
    print(make_icon())
