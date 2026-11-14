# -*- coding: utf-8 -*-
"""Adresses des images d'une page, selon l'API Image IIIF.

Chaque fonction rend une liste d'adresses à essayer dans l'ordre : un
serveur peut refuser une forme de requête (niveau de conformité, taille
maximale…) et en accepter une autre.
"""
from __future__ import annotations

from . import gallica
from .manifest import ImageService, Page

THUMBNAIL_BOX = 240

# Tailles de téléchargement proposées : "max" ou plus grand côté en pixels.
SIZES = ("max", 3000, 1500, 800)

_IMAGE_TYPES = {
    "image/jpeg": ".jpg", "image/jpg": ".jpg", "image/pjpeg": ".jpg",
    "image/png": ".png", "image/tiff": ".tif", "image/tif": ".tif",
    "image/jp2": ".jp2", "image/jpx": ".jp2", "image/webp": ".webp",
    "image/gif": ".gif",
}
IMAGE_EXTENSIONS = (".jpg", ".jpeg", ".png", ".tif", ".tiff", ".jp2", ".webp", ".gif")


def _quality(service: ImageService) -> str:
    return "native.jpg" if service.version == 1 else "default.jpg"


def _sized(service: ImageService, box: int) -> list[str]:
    q = _quality(service)
    return [f"{service.url}/full/!{box},{box}/0/{q}", f"{service.url}/full/{box},/0/{q}"]


def _unique(urls) -> list[str]:
    seen, out = set(), []
    for url in urls:
        if url and url not in seen:
            seen.add(url)
            out.append(url)
    return out


def thumbnail_urls(page: Page, box: int = THUMBNAIL_BOX) -> list[str]:
    """Vignette : celle du canvas, puis une image réduite demandée au
    service, puis l'image elle-même en dernier recours."""
    urls = []
    if page.thumbnail_service:
        urls += _sized(page.thumbnail_service, box)
    if page.thumbnail_url:
        urls.append(page.thumbnail_url)
    if page.service:
        urls += _sized(page.service, box)
    if page.image_url:
        urls.append(page.image_url)
    return _unique(urls)


def preview_urls(page: Page, box: int) -> list[str]:
    """Image en plus grand pour l'aperçu : réduite par le service, sinon
    l'image elle-même, sinon la vignette."""
    urls = []
    if page.service:
        urls += _sized(page.service, box)
    if page.image_url:
        urls.append(page.image_url)
    return _unique(urls + thumbnail_urls(page, box))


def _fits(page: Page, box: int) -> bool:
    return bool(page.width and page.height and max(page.width, page.height) <= box)


def image_urls(page: Page, size="max") -> list[str]:
    """Image à télécharger : ``size`` vaut ``"max"`` (la plus grande taille
    permise par le serveur) ou le plus grand côté souhaité en pixels."""
    urls = []
    service = page.service
    if size == "max":
        # Gallica : l'API image .highres, au rythme plus large (50/min).
        highres = gallica.highres_url(service.url if service else page.image_url)
        if highres:
            urls.append(highres)
    if service:
        q = _quality(service)
        full_v3 = f"{service.url}/full/max/0/{q}"
        full_v2 = f"{service.url}/full/full/0/{q}"
        limited = bool(service.max_width or service.max_height or service.max_area)
        if size != "max" and not _fits(page, int(size)):
            urls += _sized(service, int(size))
        if service.version >= 3:
            urls.append(full_v3)
        elif limited:
            urls += [full_v3, full_v2]
        else:
            urls += [full_v2, full_v3]
    if page.image_url:
        urls.append(page.image_url)
    return _unique(urls)


def extension_for(content_type: str, data: bytes = b"", url: str = "") -> str:
    """Extension de fichier d'une image : d'après son contenu, son type
    annoncé, puis son adresse ; ".jpg" par défaut."""
    sniffed = sniff_image(data)
    if sniffed:
        return sniffed
    if content_type in _IMAGE_TYPES:
        return _IMAGE_TYPES[content_type]
    tail = url.rsplit("?", 1)[0].rsplit("/", 1)[-1].lower()
    for ext in IMAGE_EXTENSIONS:
        if tail.endswith(ext):
            return ".jpg" if ext == ".jpeg" else (".tif" if ext == ".tiff" else ext)
    return ".jpg"


def sniff_image(data: bytes) -> str:
    """Extension d'après la signature des données, "" si ce n'est pas une
    image reconnue."""
    head = data[:16]
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith((b"II*\x00", b"MM\x00*")):
        return ".tif"
    if head.startswith(b"\x00\x00\x00\x0cjP  ") or head.startswith(b"\xffO\xffQ"):
        return ".jp2"
    if head.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return ".webp"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    return ""
