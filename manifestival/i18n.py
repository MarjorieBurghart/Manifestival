# -*- coding: utf-8 -*-
"""Traductions de l'interface.

Chaque langue a son fichier ``i18n/<code>.json`` : un objet ``{clé: texte}``.
Un texte peut contenir des variables ``{name}`` (``str.format``). Un texte
qui dépend d'un nombre est un objet ``{"one": …, "few": …, "many": …,
"other": …}`` selon les catégories de pluriel de la langue (règles CLDR) ;
la variable ``{n}`` y reçoit le nombre. Une clé absente retombe sur
l'anglais, puis sur la clé elle-même.
"""
from __future__ import annotations

from pathlib import Path
import json
import locale

DIRECTORY = Path(__file__).resolve().parent / "i18n"
LANGUAGES = {
    "fr": "Français", "en": "English", "de": "Deutsch", "it": "Italiano",
    "cs": "Čeština", "ru": "Русский", "ja": "日本語",
}
FALLBACK = "en"


def plural_category(lang: str, n: int) -> str:
    n = abs(int(n))
    if lang == "fr":
        return "one" if n in (0, 1) else "other"
    if lang in ("en", "de", "it"):
        return "one" if n == 1 else "other"
    if lang == "cs":
        return "one" if n == 1 else "few" if 2 <= n <= 4 else "other"
    if lang == "ru":
        if n % 10 == 1 and n % 100 != 11:
            return "one"
        if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
            return "few"
        return "many"
    return "other"


def load(lang: str) -> dict:
    path = DIRECTORY / f"{lang}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def system_language() -> str:
    """Langue du système si elle est traduite, sinon l'anglais."""
    try:
        from PySide6.QtCore import QLocale
        names = [QLocale.system().name()] + list(QLocale.system().uiLanguages())
    except Exception:
        names = [locale.getlocale()[0] or ""]
    for name in names:
        code = (name or "").replace("-", "_").split("_")[0].lower()
        if code in LANGUAGES:
            return code
    return FALLBACK


class Translator:
    def __init__(self, lang: str = FALLBACK):
        self.set_language(lang)

    def set_language(self, lang: str):
        self.lang = lang if lang in LANGUAGES else FALLBACK
        self.messages = load(self.lang)
        self.fallback = load(FALLBACK) if self.lang != FALLBACK else self.messages

    def __call__(self, key: str, **values) -> str:
        text = self.messages.get(key)
        if text is None:
            text = self.fallback.get(key, key)
        if isinstance(text, dict):
            n = values.get("n", 0)
            category = plural_category(self.lang, n)
            text = text.get(category) or text.get("other") or next(iter(text.values()), key)
        if values:
            try:
                return text.format(**values)
            except (KeyError, IndexError, ValueError):
                return text
        return text
