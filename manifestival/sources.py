# -*- coding: utf-8 -*-
"""Lecture du JSON d'un manifest depuis une adresse web ou un fichier."""
from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote, urlparse
import json
import re

from . import gallica, net

MAX_MANIFEST_BYTES = 100 * 1024 * 1024


class SourceError(Exception):
    """``kind`` : ``empty``, ``not_found`` (fichier absent), ``not_json``."""

    def __init__(self, kind: str, detail: str = ""):
        super().__init__(f"{kind} {detail}".strip())
        self.kind = kind
        self.detail = detail


def normalize(source: str) -> str:
    """Adresse nettoyée : espaces retirés, « https:// » ajouté à une adresse
    web tapée sans (``gallica.bnf.fr/…``) ; la page d'un document de Gallica
    devient l'adresse de son manifest IIIF."""
    s = (source or "").strip().strip('"').strip("'").strip()
    if re.match(r"^[a-z][a-z0-9+.-]*://", s, re.I) or not s:
        return gallica.manifest_url(s) or s
    if Path(s).expanduser().exists() or re.match(r"^[A-Za-z]:[\\/]", s) or s.startswith(("/", "\\", "~", ".")):
        return s
    if re.match(r"^[\w-]+(\.[\w-]+)+(:\d+)?(/|$)", s):
        return gallica.manifest_url("https://" + s) or "https://" + s
    return s


def is_web(source: str) -> bool:
    return source.lower().startswith(("http://", "https://"))


def load_json(source: str, *, cancel=None, fetch=net.fetch):
    """JSON de ``source`` (adresse http(s), chemin, ou ``file://``)."""
    s = normalize(source)
    if not s:
        raise SourceError("empty")
    if is_web(s):
        reply = fetch(s, timeout=30, deadline=180, max_bytes=MAX_MANIFEST_BYTES,
                      accept="application/ld+json, application/json;q=0.9, */*;q=0.1",
                      cancel=cancel)
        data = reply.data
    else:
        parsed = urlparse(s)
        path = Path(unquote(parsed.path)) if parsed.scheme == "file" else Path(s).expanduser()
        if parsed.scheme == "file" and re.match(r"^/[A-Za-z]:/", parsed.path):
            path = Path(unquote(parsed.path[1:]))
        if not path.is_file():
            raise SourceError("not_found", str(path))
        data = path.read_bytes()
    try:
        return json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        # Une page de vérification anti-robots servie à la place du JSON.
        protection = net.protection_of(None, data) if is_web(s) else ""
        if protection:
            raise net.FetchError("blocked", s, 200, protection=protection) from None
        raise SourceError("not_json", str(exc)) from None
