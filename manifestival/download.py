# -*- coding: utf-8 -*-
"""Téléchargement des images choisies (sans Qt).

:func:`download` télécharge les images une à une — les serveurs IIIF
limitent souvent le nombre de requêtes — en réessayant après une erreur
passagère (serveur surchargé, délai dépassé). Chaque image est d'abord
écrite dans un fichier ``.part``, renommé une fois complet : une
interruption ne laisse pas d'image tronquée sous son nom définitif.

:func:`write_index` enregistre à côté des images la liste des fichiers
(CSV) et le manifest (JSON).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import csv
import json
import os
import threading
import time

from . import net
from .images import IMAGE_EXTENSIONS, extension_for, image_urls, sniff_image
from .manifest import Manifest, Page, plain_text
from .politeness import Pacer

MAX_IMAGE_BYTES = 500 * 1024 * 1024
RETRY_DELAYS = (3, 10, 30)
MAX_WAIT = 120

SKIP, REPLACE, KEEP_BOTH = "skip", "replace", "keep_both"


@dataclass
class Result:
    page: Page
    stem: str
    status: str = "pending"  # ok, skipped, failed, cancelled
    path: Path | None = None
    url: str = ""
    error: net.FetchError | Exception | None = None


def existing_file(folder: Path, stem: str) -> Path | None:
    """Image déjà présente sous ce nom, quelle que soit son extension."""
    for ext in IMAGE_EXTENSIONS:
        for candidate in (folder / (stem + ext), folder / (stem + ext.upper())):
            if candidate.is_file():
                return candidate
    return None


def _free_stem(folder: Path, stem: str) -> str:
    k = 2
    candidate = stem
    while existing_file(folder, candidate) is not None:
        candidate = f"{stem}_{k}"
        k += 1
    return candidate


def _wait(cancel: threading.Event | None, seconds: float, sleep) -> bool:
    """Attend ``seconds`` ; False si l'utilisateur a annulé entre-temps."""
    if cancel is not None:
        return not cancel.wait(seconds)
    sleep(seconds)
    return True


def fetch_image(page: Page, size="max", *, cancel=None, fetch=net.fetch,
                sleep=time.sleep, notify=None) -> net.Response:
    """Image d'une page, en essayant les adresses de :func:`image_urls`
    dans l'ordre ; une erreur passagère est retentée (``RETRY_DELAYS``).
    ``notify(seconds)`` est appelé avant chaque attente."""
    last_error: Exception | None = None
    urls = image_urls(page, size)
    if not urls:
        raise net.FetchError("not_web", "")
    for url in urls:
        attempt = 0
        while True:
            if cancel is not None and cancel.is_set():
                raise net.FetchError("cancelled", url)
            try:
                reply = fetch(url, timeout=60, max_bytes=MAX_IMAGE_BYTES,
                              accept="image/*", cancel=cancel)
            except net.FetchError as exc:
                if exc.kind in ("cancelled", "blocked"):
                    raise  # protection anti-robots : aucune autre adresse
                last_error = exc
                if exc.retryable and attempt < len(RETRY_DELAYS):
                    delay = min(MAX_WAIT, exc.retry_after if exc.retry_after is not None else RETRY_DELAYS[attempt])
                    attempt += 1
                    if notify:
                        notify(delay)
                    if not _wait(cancel, delay, sleep):
                        raise net.FetchError("cancelled", url) from None
                    continue
                break  # adresse suivante
            if sniff_image(reply.data) or reply.content_type.startswith("image/"):
                return reply
            last_error = net.FetchError("not_image", url, detail=reply.content_type)
            break
    raise last_error or net.FetchError("network", "")


def download(jobs, folder, *, size="max", if_exists=SKIP, progress=None,
             cancel: threading.Event | None = None, fetch=net.fetch,
             sleep=time.sleep, notify=None, pacer: Pacer | None = None) -> list[Result]:
    """Télécharge ``jobs`` (suite de ``(page, nom_sans_extension)``) dans
    ``folder``. ``progress(fait, total, résultat)`` est appelé après chaque
    image. ``if_exists`` : ``skip`` (garder le fichier présent, utile pour
    reprendre un téléchargement interrompu), ``replace`` ou ``keep_both``.
    Les requêtes sont espacées selon les limites du serveur (``pacer``) ;
    si une protection anti-robots refuse une image, les suivantes ne sont
    pas demandées (elles restent en échec, à réessayer plus tard)."""
    fetch = (pacer or Pacer(sleep=sleep)).wrap(fetch)
    blocked: net.FetchError | None = None
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    results = [Result(page, stem) for page, stem in jobs]
    total = len(results)
    for done, result in enumerate(results, 1):
        if cancel is not None and cancel.is_set():
            result.status = "cancelled"
            continue
        present = existing_file(folder, result.stem)
        if present is not None and if_exists == SKIP:
            result.status, result.path = "skipped", present
            if progress:
                progress(done, total, result)
            continue
        if blocked is not None:
            result.status, result.error = "failed", blocked
            if progress:
                progress(done, total, result)
            continue
        if present is not None and if_exists == KEEP_BOTH:
            result.stem = _free_stem(folder, result.stem)
        try:
            reply = fetch_image(result.page, size, cancel=cancel, fetch=fetch,
                                sleep=sleep, notify=notify)
            ext = extension_for(reply.content_type, reply.data, reply.url)
            target = folder / (result.stem + ext)
            if if_exists == REPLACE and present is not None and present != target:
                present.unlink(missing_ok=True)
            partial = target.with_name(target.name + ".part")
            partial.write_bytes(reply.data)
            os.replace(partial, target)
            result.status, result.path, result.url = "ok", target, reply.url
        except net.FetchError as exc:
            result.status = "cancelled" if exc.kind == "cancelled" else "failed"
            result.error = exc
            if exc.kind == "blocked":
                blocked = exc
        except OSError as exc:
            result.status, result.error = "failed", exc
        if progress:
            progress(done, total, result)
    return results


INDEX_COLUMNS = ("file", "n", "label", "status", "canvas", "image", "metadata")


def index_names(prefix_stem: str) -> tuple[str, str]:
    """Noms de la liste CSV et de la copie du manifest."""
    base = (prefix_stem + "_") if prefix_stem else ""
    return base + "images.csv", base + "manifest.json"


def write_index(folder, results, manifest: Manifest, prefix_stem: str = "",
                delimiter: str = ",") -> tuple[Path, Path]:
    """Écrit la liste des images (CSV en UTF-8 avec BOM, lisible par un
    tableur) et le JSON du manifest ; rend leurs chemins."""
    folder = Path(folder)
    csv_name, json_name = index_names(prefix_stem)
    csv_path, json_path = folder / csv_name, folder / json_name
    with csv_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=delimiter)
        writer.writerow(INDEX_COLUMNS)
        for r in results:
            meta = " | ".join(f"{k}: {plain_text(v)}" for k, v in r.page.metadata)
            writer.writerow((r.path.name if r.path else "", r.page.index, r.page.label,
                             r.status, r.page.canvas_id, r.url, meta))
    json_path.write_text(json.dumps(manifest.raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path, json_path
