# -*- coding: utf-8 -*-
"""Journal des refus des serveurs (sans Qt).

Chaque fois qu'un serveur refuse une demande de Manifestival (code 401, 403
ou 429, protection anti-robots, serveur qui ne répond plus), une ligne est
notée : date, serveur, code, protection reconnue, contexte (manifest,
vignettes, téléchargement), adresse. Le journal sert à retrouver le serveur
en cause et à le signaler à la bibliothèque ; :meth:`RefusalLog.as_text`
le met sous une forme facile à copier dans un message.

Le journal est gardé dans un fichier JSON (les 200 derniers refus) ; sans
fichier, il ne vit que le temps de la session.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
import json

from . import __version__
from .net import FetchError
from .politeness import host_of

LIMIT = 200
CONTEXTS = ("manifest", "thumbnails", "download", "zoom", "browser")


@dataclass
class Refusal:
    time: str
    context: str
    host: str
    url: str
    status: int | None = None
    kind: str = ""
    protection: str = ""
    manifest: str = ""
    count: int = 1

    def same_event(self, other: "Refusal") -> bool:
        keys = ("context", "host", "status", "kind", "protection", "manifest")
        return all(getattr(self, k) == getattr(other, k) for k in keys) and self.time[:16] == other.time[:16]


def is_refusal(error) -> bool:
    """L'erreur est-elle un refus du serveur (et pas une panne locale, une
    adresse fausse, une annulation) ?"""
    if not isinstance(error, FetchError):
        return False
    if error.kind in ("blocked", "unreachable"):
        return True
    return error.kind == "http" and error.status in (401, 403, 429)


class RefusalLog:
    def __init__(self, path: str | Path | None = None, limit: int = LIMIT):
        self.path = Path(path) if path else None
        self.limit = limit
        self.items: list[Refusal] = self._load()

    def _load(self) -> list[Refusal]:
        if self.path is None or not self.path.is_file():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            fields = set(Refusal.__dataclass_fields__)
            return [Refusal(**{k: v for k, v in item.items() if k in fields})
                    for item in data.get("refusals", []) if isinstance(item, dict)]
        except (OSError, ValueError, TypeError):
            return []   # journal abîmé : on repart d'un journal vide

    def _save(self):
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"format": "manifestival-refusals", "refusals": [asdict(r) for r in self.items]}
            self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        except OSError:
            pass         # le journal ne doit jamais gêner l'application

    def record(self, error, context: str, manifest: str = "", now: datetime | None = None) -> Refusal | None:
        """Note le refus ``error`` (sinon rien) ; un même refus répété dans la
        même minute augmente le compte de la dernière ligne."""
        if not is_refusal(error):
            return None
        stamp = (now or datetime.now()).isoformat(timespec="seconds")
        item = Refusal(time=stamp, context=context, host=host_of(error.url) or error.url, url=error.url,
                       status=error.status, kind=error.kind, protection=getattr(error, "protection", ""),
                       manifest=manifest)
        if self.items and self.items[-1].same_event(item):
            self.items[-1].count += 1
            self.items[-1].url = item.url
        else:
            self.items.append(item)
            del self.items[:-self.limit]
        self._save()
        return self.items[-1]

    def clear(self):
        self.items = []
        self._save()

    def __len__(self):
        return len(self.items)

    def as_text(self) -> str:
        """Le journal en texte simple, à coller dans un message ou une issue."""
        lines = [f"Manifestival {__version__} — refus des serveurs ({len(self.items)})", ""]
        for r in self.items:
            what = f"{r.status}" if r.status else r.kind
            extra = f" ×{r.count}" if r.count > 1 else ""
            protection = f" [{r.protection}]" if r.protection else ""
            lines.append(f"{r.time.replace('T', ' ')}  {r.host}  {what}{protection}  {r.context}{extra}")
            lines.append(f"    {r.url}")
            if r.manifest and r.manifest != r.url:
                lines.append(f"    manifest : {r.manifest}")
        return "\n".join(lines) + "\n"
