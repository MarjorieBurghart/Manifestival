# -*- coding: utf-8 -*-
"""Noms des fichiers téléchargés.

Un nom est produit par un modèle où figurent des balises entre accolades :

``{prefix}``  le préfixe choisi (par exemple la cote du manuscrit) ;
``{n}``       la position de l'image dans le manifest, complétée de zéros
              (001, 002… ; autant de chiffres que pour la dernière page,
              trois au moins) ;
``{label}``   le libellé de la page dans le manifest (« f. 1r », « p. 12 ») ;
``{title}``   le titre du manifest ;
``{meta:Nom}`` la valeur du champ de métadonnées « Nom » de la page (ou, à
              défaut, du manifest).

Le texte de chaque balise est nettoyé (caractères interdits sous Windows,
macOS ou Linux), puis les séparateurs en double ou en bout de nom, laissés
par une balise vide, sont retirés. L'extension (.jpg…) est ajoutée au
téléchargement.
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from .manifest import Manifest, Page, plain_text

SEPARATORS = ("_", "-", ".", " ")
_SEP_CHARS = "_-. "
MAX_STEM = 150
_TOKEN = re.compile(r"\{(prefix|n|label|title|meta:[^{}]*)\}")
_FORBIDDEN = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)),
             *(f"LPT{i}" for i in range(1, 10))}


@dataclass(frozen=True)
class NamingOptions:
    """Réglages du nom des fichiers. ``template`` est le modèle ;
    ``separator`` remplace les espaces si ``replace_spaces`` ;
    ``strip_accents`` retire les accents (é → e) sans toucher aux
    autres écritures."""
    template: str = "{prefix}_{n}_{label}"
    prefix: str = ""
    separator: str = "_"
    replace_spaces: bool = True
    strip_accents: bool = False


def build_template(parts, separator: str = "_", meta_field: str = "") -> str:
    """Modèle formé des éléments ``parts`` (``prefix``, ``n``, ``label``,
    ``title``, ``meta``) dans cet ordre, joints par ``separator``."""
    tokens = []
    for part in parts:
        if part == "meta":
            if meta_field:
                tokens.append("{meta:" + meta_field.replace("{", "").replace("}", "") + "}")
        elif part in ("prefix", "n", "label", "title"):
            tokens.append("{" + part + "}")
    return separator.join(tokens)


def number_width(total: int) -> int:
    return max(3, len(str(max(total, 1))))


def clean(text: str, options: NamingOptions) -> str:
    """Texte rendu sûr pour un nom de fichier."""
    text = unicodedata.normalize("NFC", text or "")
    if options.strip_accents:
        text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
        text = unicodedata.normalize("NFC", text)
    text = _FORBIDDEN.sub("-", text)
    text = re.sub(r"\s+", " ", text).strip()
    # « 3v / 4r » → « 3v-4r » : pas d'espaces autour d'un tiret ou d'un
    # soulignement, pas de suite de tirets.
    text = re.sub(r" ?([_-]) ?", r"\1", text)
    text = re.sub(r"([_-])[_-]+", r"\1", text)
    if options.replace_spaces and options.separator != " ":
        # « f. 1r » → « f_1r » plutôt que « f._1r ».
        text = re.sub(r"\.(?= )", "", text)
        text = text.replace(" ", options.separator)
    return text


def render(page: Page, manifest: Manifest | None, options: NamingOptions,
           total: int | None = None) -> str:
    """Nom (sans extension) de l'image de ``page``."""
    total = total if total is not None else (manifest.canvas_count if manifest else page.index)
    width = number_width(total)

    def value(token: str) -> str:
        if token == "prefix":
            raw = options.prefix
        elif token == "n":
            raw = str(page.index).zfill(width)
        elif token == "label":
            raw = page.label
        elif token == "title":
            raw = manifest.label if manifest else ""
        else:
            field = token[5:]
            raw = plain_text(page.meta(field) or (manifest.meta(field) if manifest else ""))
        return clean(raw, options).strip(_SEP_CHARS)

    # Suite de segments (est_une_valeur, texte) ; une balise vide disparaît
    # avec l'un des séparateurs qui l'entourent.
    segments, pos = [], 0
    template = options.template or ""
    for match in _TOKEN.finditer(template):
        if match.start() > pos:
            segments.append((False, clean(template[pos:match.start()], options)))
        segments.append((True, value(match.group(1))))
        pos = match.end()
    if pos < len(template):
        segments.append((False, clean(template[pos:], options)))
    kept = []
    for is_value, text in segments:
        if not text:
            continue
        if not is_value and kept and not kept[-1][0] and not kept[-1][1].strip(_SEP_CHARS):
            continue  # deux séparateurs de suite : le premier suffit
        kept.append((is_value, text))
    stem = "".join(text for _, text in kept).strip(_SEP_CHARS)
    stem = stem[:MAX_STEM].rstrip(_SEP_CHARS)
    if not stem:
        stem = "image_" + str(page.index).zfill(width)
    if stem.split(".")[0].upper() in _RESERVED:
        stem += "_"
    return stem


def unique_stems(stems) -> list[str]:
    """Noms rendus distincts (casse ignorée, comme sous Windows et macOS) :
    le deuxième « f1r » devient « f1r_2 », etc."""
    used, out = set(), []
    for stem in stems:
        candidate, k = stem, 2
        while candidate.casefold() in used:
            candidate = f"{stem}_{k}"
            k += 1
        used.add(candidate.casefold())
        out.append(candidate)
    return out


def file_stems(pages, manifest: Manifest | None, options: NamingOptions) -> list[str]:
    total = manifest.canvas_count if manifest else max((p.index for p in pages), default=1)
    return unique_stems(render(p, manifest, options, total) for p in pages)


def prefix_suggestions(manifest: Manifest) -> list[str]:
    """Valeurs du manifest qui peuvent servir de préfixe : d'abord celles
    des champs qui ressemblent à une cote, puis les autres valeurs courtes,
    enfin le titre."""
    keys = ("shelfmark", "cote", "call number", "signatur", "segnatura", "signatura",
            "shelf mark", "identifier", "identifiant", "шифр", "請求記号", "所蔵番号")
    first, others = [], []
    for label, value in manifest.metadata:
        text = plain_text(value).strip()
        if not text or len(text) > 80 or "\n" in text:
            continue
        if any(k in label.casefold() for k in keys):
            first.append(text)
        else:
            others.append(text)
    if manifest.label and len(manifest.label) <= 80:
        others.append(manifest.label)
    seen, out = set(), []
    for text in first + others:
        if text not in seen:
            seen.add(text)
            out.append(text)
    return out
