# -*- coding: utf-8 -*-
"""Traductions : mêmes clés et mêmes variables dans les sept langues, toutes
les clés employées par le code présentes."""
from __future__ import annotations

import re
import string

import pytest

from iiif_test_server import ROOT

from explorateur_iiif import i18n

PACKAGE = ROOT / "explorateur_iiif"
PLURAL_CATEGORIES = {"fr": {"one", "other"}, "en": {"one", "other"}, "de": {"one", "other"},
                     "it": {"one", "other"}, "cs": {"one", "few", "other"},
                     "ru": {"one", "few", "many", "other"}, "ja": {"other"}}


def _fields(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def _keys_used_in_code():
    keys = set()
    for path in PACKAGE.rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        keys |= set(re.findall(r"""tr_?\(\s*["']([a-z]+\.[a-z_]+)["']""", source))
    # Clés composées dans download_dialog.py ("download." + key).
    keys |= {f"download.{k}" for k in ("sep_underscore", "sep_hyphen", "sep_dot", "sep_space",
                                        "exists_skip", "exists_replace", "exists_keep")}
    return keys


def test_every_language_has_every_key():
    reference = i18n.load("fr")
    assert reference
    for lang in i18n.LANGUAGES:
        assert set(i18n.load(lang)) == set(reference), lang


def test_keys_used_by_the_code_exist():
    missing = _keys_used_in_code() - set(i18n.load("fr"))
    assert not missing


@pytest.mark.parametrize("lang", list(i18n.LANGUAGES))
def test_variables_and_plural_forms(lang):
    reference, messages = i18n.load("fr"), i18n.load(lang)
    # Les balises du modèle de nom ({prefix}…) sont du texte affiché.
    template_tags = {"prefix", "n", "label", "title"}
    for key, ref in reference.items():
        text = messages[key]
        if isinstance(ref, dict):
            assert isinstance(text, dict), (lang, key)
            assert set(text) == PLURAL_CATEGORIES[lang], (lang, key)
            for form in text.values():
                assert _fields(form) <= {"n"} | _fields(next(iter(ref.values()))), (lang, key)
        else:
            assert isinstance(text, str), (lang, key)
            if key == "download.template_help":
                assert template_tags <= _fields(text) | {"n"}
                assert "{meta:" in text
                continue
            assert _fields(text) == _fields(ref), (lang, key)


def test_plural_rules():
    tr = i18n.Translator("ru")
    assert tr("main.count", n=1) == "1 изображение"
    assert tr("main.count", n=3) == "3 изображения"
    assert tr("main.count", n=11) == "11 изображений"
    assert tr("main.count", n=21) == "21 изображение"
    assert i18n.Translator("fr")("main.count", n=0) == "0 image"
    assert i18n.Translator("cs")("main.count", n=4) == "4 obrázky"
    assert i18n.Translator("ja")("main.count", n=4) == "4 枚の画像"


def test_unknown_language_falls_back_to_english():
    tr = i18n.Translator("xx")
    assert tr.lang == "en" and tr("main.open") == "Show"
    assert tr("no.such.key") == "no.such.key"
