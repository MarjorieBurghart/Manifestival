# -*- coding: utf-8 -*-
"""Tuiles IIIF : lecture de l'info.json, choix du niveau, adresses."""
from __future__ import annotations

from iiif_test_server import ROOT  # noqa: F401  (chemin du paquet)

from manifestival.tiles import TileSource, parse_info

S = "https://e.org/iiif/img1"


def _v3(**kw):
    info = {"id": S, "type": "ImageService3", "profile": "level1", "width": 5000, "height": 7000,
            "tiles": [{"width": 512, "scaleFactors": [1, 2, 4, 8, 16]}]}
    info.update(kw)
    return info


def test_parse_v3_and_v2_info():
    src = parse_info(_v3(), S)
    assert (src.version, src.width, src.height) == (3, 5000, 7000)
    assert (src.tile_width, src.tile_height, src.scale_factors) == (512, 512, (1, 2, 4, 8, 16))
    v2 = parse_info({"@context": "http://iiif.io/api/image/2/context.json", "@id": S,
                     "profile": ["http://iiif.io/api/image/2/level2.json"], "width": 1000, "height": 800,
                     "tiles": [{"width": 256, "height": 128, "scaleFactors": [4, 1, 2]}]}, S)
    assert (v2.version, v2.tile_height, v2.scale_factors) == (2, 128, (1, 2, 4))


def test_the_address_the_info_came_from_wins():
    src = parse_info(_v3(id="http://internal:8080/iiif/img1"), S)
    assert src.url == S


def test_level1_without_tiles_gets_512_tiles_and_level0_without_tiles_none():
    src = parse_info(_v3(tiles=None), S)
    assert src.tile_width == 512 and src.scale_factors == (1, 2, 4, 8, 16)
    assert parse_info(_v3(tiles=None, profile="level0"), S) is None
    assert parse_info({"id": S, "width": "x"}, S) is None
    assert parse_info(None, S) is None


def test_level_choice():
    src = TileSource(S, 3, 5000, 7000, 512, 512, (1, 2, 4, 8, 16))
    assert src.level_for(1.0) == 1      # 100 % : pleine résolution
    assert src.level_for(3.0) == 1      # au-delà : pleine résolution aussi
    assert src.level_for(0.5) == 2
    assert src.level_for(0.3) == 2      # 1/0.3 = 3,3 : le niveau 2 suffit
    assert src.level_for(0.01) == 16


def test_tile_urls_follow_the_image_api():
    v3 = TileSource(S, 3, 5000, 7000, 512, 512, (1, 2, 4))
    first = v3.tile(1, 0, 0)
    assert v3.tile_url(first) == f"{S}/0,0,512,512/512,512/0/default.jpg"
    edge = v3.tile(2, 4, 0)   # x = 4096, reste 904 pixels → 452 à l'échelle 1/2
    assert (edge.x, edge.w) == (4096, 904)
    assert v3.tile_url(edge) == f"{S}/4096,0,904,1024/452,512/0/default.jpg"
    v2 = TileSource(S, 2, 5000, 7000, 512, 512, (1, 2))
    assert v2.tile_url(v2.tile(1, 1, 1)) == f"{S}/512,512,512,512/512,/0/default.jpg"
    v1 = TileSource(S, 1, 300, 200, 512, 512, (1,))
    assert v1.tile_url(v1.tile(1, 0, 0)) == f"{S}/full/300,/0/native.jpg"
    assert v3.tile(1, 10, 0) is None and v3.tile(1, -1, 0) is None


def test_visible_tiles_cover_the_area_from_the_centre():
    src = TileSource(S, 3, 5000, 7000, 512, 512, (1, 2, 4))
    tiles = src.visible_tiles(1, 600, 600, 1600, 1100)
    assert {(t.col, t.row) for t in tiles} == {(c, r) for c in (1, 2, 3) for r in (1, 2)}
    assert (tiles[0].col, tiles[0].row) == (2, 1) or (tiles[0].col, tiles[0].row) == (2, 2)
    assert src.visible_tiles(1, -100, -100, 10, 10)[0].col == 0
    assert src.visible_tiles(1, 6000, 0, 7000, 100) == []
