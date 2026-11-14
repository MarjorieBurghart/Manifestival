# -*- coding: utf-8 -*-
"""Respect des limites annoncées par les serveurs (sans Qt).

Certaines bibliothèques limitent le nombre de requêtes. La BnF annonce pour
Gallica, pendant la mise en place de son gestionnaire d'API, 5 appels par
minute à l'API Image IIIF en pleine taille (https://api.bnf.fr). Un serveur
qui se sent sollicité trop vite bloque l'adresse de l'utilisateur : mieux
vaut aller lentement que d'être bloqué.

:class:`Pacer` espace les requêtes vers un même serveur ; :func:`rule_for`
donne la règle d'un serveur (valeurs prudentes par défaut).
"""
from __future__ import annotations

from dataclasses import dataclass
import re
import threading
import time
from urllib.parse import urlparse


@dataclass(frozen=True)
class HostRule:
    full_interval: float = 0.0   # secondes entre deux images en pleine taille
    interval: float = 0.0        # secondes entre deux autres requêtes
    concurrency: int = 4         # vignettes ou tuiles demandées à la fois

    @property
    def full_per_minute(self) -> int:
        return int(60 // self.full_interval) if self.full_interval else 0


DEFAULT = HostRule()
RULES = {
    "gallica.bnf.fr": HostRule(full_interval=12.0, interval=1.0, concurrency=2),
}

# Requête de l'API Image : …/{région}/{taille}/{rotation}/{qualité}.{format}
_IIIF = re.compile(r"/([^/]+)/([^/]+)/!?\d+(?:\.\d+)?/[a-z]+\.[a-z0-9]+$")


def host_of(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower()
    except ValueError:
        return ""


def rule_for(url: str) -> HostRule:
    return RULES.get(host_of(url), DEFAULT)


def is_full_size(url: str) -> bool:
    """Requête d'une image en pleine taille (ou d'une image sans service,
    qui est en général l'image entière) ?"""
    match = _IIIF.search(url.split("?")[0])
    if not match:
        return True
    region, size = match.groups()
    return region == "full" and size in ("max", "full")


class Pacer:
    """Espace les requêtes vers chaque serveur selon sa règle."""

    def __init__(self, clock=time.monotonic, sleep=time.sleep):
        self.clock, self.sleep = clock, sleep
        self.last: dict[str, float] = {}
        self.lock = threading.Lock()

    def delay(self, url: str) -> float:
        rule = rule_for(url)
        gap = rule.full_interval if is_full_size(url) else rule.interval
        if not gap:
            return 0.0
        with self.lock:
            last = self.last.get(host_of(url))
        return 0.0 if last is None else max(0.0, last + gap - self.clock())

    def wait(self, url: str, cancel: threading.Event | None = None) -> bool:
        """Attend le moment de demander ``url`` ; False si annulé."""
        delay = self.delay(url)
        if delay:
            if cancel is not None:
                if cancel.wait(delay):
                    return False
            else:
                self.sleep(delay)
        with self.lock:
            self.last[host_of(url)] = self.clock()
        return True

    def wrap(self, fetch):
        """``fetch`` qui attend son tour avant chaque requête."""
        def paced(url, **kwargs):
            if not self.wait(url, kwargs.get("cancel")):
                from .net import FetchError
                raise FetchError("cancelled", url)
            return fetch(url, **kwargs)
        return paced
