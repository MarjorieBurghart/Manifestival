# -*- coding: utf-8 -*-
"""Tuiles IIIF pour le zoom (sans Qt, sans réseau).

Un service d'images IIIF décrit dans son ``info.json`` la taille de l'image
en pleine résolution et les tuiles qu'il sert : largeur et hauteur d'une
tuile, et facteurs de réduction (1 = pleine résolution, 2 = moitié…).
Pour afficher une zone à une échelle donnée, on choisit le niveau de
réduction suffisant (:meth:`TileSource.level_for`) puis on demande les
seules tuiles visibles (:meth:`TileSource.visible_tiles`), dont l'adresse
suit la spécification de l'API Image (:meth:`TileSource.tile_url`).
"""
from __future__ import annotations

from dataclasses import dataclass
import math

from .manifest import image_service

DEFAULT_TILE = 512


@dataclass(frozen=True)
class Tile:
    scale: int      # facteur de réduction
    col: int
    row: int
    x: int          # zone de l'image en pleine résolution
    y: int
    w: int
    h: int


@dataclass(frozen=True)
class TileSource:
    url: str                     # service, sans /info.json
    version: int                 # 1, 2 ou 3
    width: int                   # pleine résolution
    height: int
    tile_width: int = DEFAULT_TILE
    tile_height: int = DEFAULT_TILE
    scale_factors: tuple[int, ...] = (1,)

    def _quality(self) -> str:
        return "native.jpg" if self.version == 1 else "default.jpg"

    def level_for(self, zoom: float) -> int:
        """Facteur de réduction à employer quand un pixel de l'image en pleine
        résolution occupe ``zoom`` pixels de l'écran : le plus grand qui
        garde au moins un pixel d'image par pixel d'écran."""
        if zoom <= 0:
            return max(self.scale_factors)
        ideal = 1.0 / zoom
        usable = [s for s in self.scale_factors if s <= ideal + 1e-9]
        return max(usable) if usable else min(self.scale_factors)

    def tile(self, scale: int, col: int, row: int) -> Tile | None:
        span_w, span_h = self.tile_width * scale, self.tile_height * scale
        x, y = col * span_w, row * span_h
        if col < 0 or row < 0 or x >= self.width or y >= self.height:
            return None
        return Tile(scale, col, row, x, y, min(span_w, self.width - x), min(span_h, self.height - y))

    def visible_tiles(self, scale: int, left: float, top: float, right: float, bottom: float) -> list[Tile]:
        """Tuiles du niveau ``scale`` qui recouvrent la zone donnée (en
        pixels de pleine résolution), du centre vers les bords."""
        left, top = max(0.0, left), max(0.0, top)
        right, bottom = min(float(self.width), right), min(float(self.height), bottom)
        if right <= left or bottom <= top:
            return []
        span_w, span_h = self.tile_width * scale, self.tile_height * scale
        cols = range(int(left // span_w), int(math.ceil(right / span_w)))
        rows = range(int(top // span_h), int(math.ceil(bottom / span_h)))
        cx, cy = (left + right) / 2, (top + bottom) / 2
        tiles = [t for t in (self.tile(scale, c, r) for r in rows for c in cols) if t]
        tiles.sort(key=lambda t: (t.x + t.w / 2 - cx) ** 2 + (t.y + t.h / 2 - cy) ** 2)
        return tiles

    def tile_url(self, tile: Tile) -> str:
        """Adresse d'une tuile, sous la forme canonique de l'API Image."""
        w = math.ceil(tile.w / tile.scale)
        h = math.ceil(tile.h / tile.scale)
        if tile.x == 0 and tile.y == 0 and tile.w == self.width and tile.h == self.height:
            region = "full"
        else:
            region = f"{tile.x},{tile.y},{tile.w},{tile.h}"
        size = f"{w},{h}" if self.version >= 3 else f"{w},"
        return f"{self.url}/{region}/{size}/0/{self._quality()}"


def parse_info(info, fallback_url: str = "") -> TileSource | None:
    """Tuiles décrites par un ``info.json`` ; None si le service ne permet
    pas le zoom par tuiles (niveau 0 sans tuiles annoncées, ou JSON
    inexploitable). Un service de niveau 1 ou 2 qui n'annonce pas ses tuiles
    sait découper n'importe quelle zone : des tuiles de 512 pixels sont
    alors employées."""
    if not isinstance(info, dict):
        return None
    service = image_service(info) or (image_service(fallback_url) if fallback_url else None)
    if service is None:
        return None
    try:
        width, height = int(info.get("width")), int(info.get("height"))
    except (TypeError, ValueError):
        return None
    if width <= 0 or height <= 0:
        return None
    tiles = info.get("tiles")
    tile = tiles[0] if isinstance(tiles, list) and tiles and isinstance(tiles[0], dict) else None
    if tile:
        try:
            tw = int(tile.get("width"))
            th = int(tile.get("height") or tw)
            factors = tuple(sorted({int(f) for f in tile.get("scaleFactors") or [1] if int(f) > 0}))
        except (TypeError, ValueError):
            return None
        if tw <= 0 or th <= 0 or not factors:
            return None
    else:
        profile = info.get("profile")
        profile = " ".join(str(p) for p in profile if isinstance(p, str)) if isinstance(profile, list) else str(profile or "")
        if "level0" in profile:
            return None
        tw = th = DEFAULT_TILE
        factors, f = [], 1
        while True:
            factors.append(f)
            if max(width, height) / f <= DEFAULT_TILE:
                break
            f *= 2
        factors = tuple(factors)
    # L'adresse d'où vient l'info.json est sûre ; celle qu'il annonce est
    # parfois fausse (http au lieu de https derrière un proxy…).
    url = fallback_url.rstrip("/") if fallback_url else service.url
    return TileSource(url, service.version, width, height, tw, th, factors)
