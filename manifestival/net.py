# -*- coding: utf-8 -*-
"""Accès au réseau (bibliothèque standard seulement).

:func:`fetch` lit une adresse http(s) avec une taille maximale, un délai et
une possibilité d'annulation. Les échecs lèvent :class:`FetchError`, dont
``kind`` permet d'afficher un message traduit : ``http`` (``status`` donne
le code), ``blocked`` (refus d'une protection anti-robots), ``timeout``,
``network``, ``too_large``, ``cancelled``, ``not_web``.
"""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import socket
import ssl
import threading
import urllib.error
import urllib.request

from . import __version__

# Un logiciel identifié, avec l'adresse du projet, comme le demandent les
# bonnes pratiques des serveurs publics.
USER_AGENT = f"Manifestival/{__version__} (+https://github.com/MarjorieBurghart/Manifestival)"
CHUNK = 64 * 1024


class FetchError(Exception):
    def __init__(self, kind: str, url: str = "", status: int | None = None,
                 detail: str = "", retry_after: float | None = None):
        super().__init__(f"{kind} {status or ''} {detail} {url}".strip())
        self.kind = kind
        self.url = url
        self.status = status
        self.detail = detail
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        """Vaut-il la peine de réessayer plus tard la même adresse ?"""
        if self.kind in ("timeout", "network"):
            return True
        if self.kind == "blocked":  # protection anti-robots : ne pas insister
            return False
        return self.kind == "http" and self.status in (408, 425, 429, 500, 502, 503, 504)


@dataclass(frozen=True)
class Response:
    data: bytes
    content_type: str
    url: str


_context = None


def ssl_context() -> ssl.SSLContext:
    """Contexte TLS : certificats de certifi s'il est installé (utile sous
    macOS), sinon ceux du système."""
    global _context
    if _context is None:
        try:
            import certifi  # type: ignore
            _context = ssl.create_default_context(cafile=certifi.where())
        except Exception:
            _context = ssl.create_default_context()
    return _context


def _retry_after(headers) -> float | None:
    try:
        return max(0.0, float(headers.get("Retry-After")))
    except (TypeError, ValueError, AttributeError):
        return None


def is_bot_block(status: int, headers) -> bool:
    """Refus d'une protection contre les robots (Cloudflare…) plutôt que du
    serveur IIIF lui-même : page « Attention Required », etc."""
    if status not in (403, 503):
        return False
    try:
        server = (headers.get("Server") or "").lower()
        return "cloudflare" in server or bool(headers.get("CF-RAY") or headers.get("cf-mitigated"))
    except AttributeError:
        return False


def fetch(url: str, *, timeout: float = 30, max_bytes: int = 50 * 1024 * 1024,
          accept: str = "*/*", cancel: threading.Event | None = None) -> Response:
    """Contenu de l'adresse ``url`` (http ou https seulement)."""
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        raise FetchError("not_web", str(url))
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as reply:
            length = reply.headers.get("Content-Length")
            if length and length.isdigit() and int(length) > max_bytes:
                raise FetchError("too_large", url)
            chunks, size = [], 0
            while True:
                if cancel is not None and cancel.is_set():
                    raise FetchError("cancelled", url)
                chunk = reply.read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError("too_large", url)
                chunks.append(chunk)
            content_type = (reply.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            return Response(b"".join(chunks), content_type, reply.geturl() or url)
    except FetchError:
        raise
    except urllib.error.HTTPError as exc:
        kind = "blocked" if is_bot_block(exc.code, exc.headers) else "http"
        raise FetchError(kind, url, exc.code, str(exc.reason or ""), _retry_after(exc.headers)) from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise FetchError("timeout", url) from None
        raise FetchError("network", url, detail=str(reason)) from None
    except (socket.timeout, TimeoutError):
        raise FetchError("timeout", url) from None
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise FetchError("network", url, detail=str(exc)) from None
