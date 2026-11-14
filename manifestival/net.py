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
import time
import urllib.error
import urllib.request

from . import __version__

# Un logiciel identifié, avec l'adresse du projet, comme le demandent les
# bonnes pratiques des serveurs publics.
USER_AGENT = f"Manifestival/{__version__} (+https://github.com/MarjorieBurghart/Manifestival)"
CHUNK = 64 * 1024
POLL = 0.1   # secondes entre deux regards sur l'annulation


class FetchError(Exception):
    def __init__(self, kind: str, url: str = "", status: int | None = None,
                 detail: str = "", retry_after: float | None = None, protection: str = ""):
        super().__init__(f"{kind} {status or ''} {detail} {url}".strip())
        self.kind = kind
        self.url = url
        self.status = status
        self.detail = detail
        self.retry_after = retry_after
        self.protection = protection   # protection anti-robots reconnue, ou ""

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


# Protections contre les robots, reconnues à leurs en-têtes ou à leur page.
_HEADER_SIGNS = (
    ("Cloudflare", lambda h, server: "cloudflare" in server or h("CF-RAY") or h("cf-mitigated")),
    ("Akamai", lambda h, server: "akamai" in server or h("Akamai-GRN")),
    ("Imperva", lambda h, server: h("X-Iinfo") or "imperva" in (h("X-CDN") or "").lower()),
    ("DataDome", lambda h, server: "datadome" in server or h("X-DataDome") or h("X-DD-B")),
    ("Sucuri", lambda h, server: "sucuri" in server or h("X-Sucuri-ID")),
    ("AWS WAF", lambda h, server: h("x-amzn-waf-action")),
)
_BODY_SIGNS = (
    ("Anubis", (b"anubis",)),
    ("Cloudflare", (b"attention required! | cloudflare", b"challenge-platform", b"cf-chl-",
                    b"<title>just a moment")),
    ("DataDome", (b"captcha-delivery.com",)),
    ("Imperva", (b"incapsula incident",)),
)


def protection_of(headers=None, body: bytes = b"") -> str:
    """Nom de la protection anti-robots qui a répondu (« Cloudflare »,
    « Anubis »…), d'après les en-têtes et le début de la page ; "" sinon."""
    def header(name):
        try:
            return headers.get(name) if headers is not None else None
        except AttributeError:
            return None
    server = (header("Server") or "").lower()
    for name, sign in _HEADER_SIGNS:
        if sign(header, server):
            return name
    sample = (body or b"")[:65536].lower()
    for name, needles in _BODY_SIGNS:
        if any(needle in sample for needle in needles):
            return name
    return ""


def is_bot_block(status: int, headers, body: bytes = b"") -> bool:
    """Refus d'une protection contre les robots (Cloudflare, Anubis…) plutôt
    que du serveur IIIF lui-même."""
    return status in (401, 403, 503) and bool(protection_of(headers, body))


def fetch(url: str, *, timeout: float = 30, max_bytes: int = 50 * 1024 * 1024,
          accept: str = "*/*", cancel: threading.Event | None = None,
          deadline: float | None = None, progress=None) -> Response:
    """Contenu de l'adresse ``url`` (http ou https seulement).

    ``timeout`` : silence toléré du serveur (connexion, ou entre deux
    paquets), en secondes. ``deadline`` : durée totale permise, pour un
    serveur qui répond au compte-gouttes. ``cancel`` : l'annulation prend
    effet aussitôt, même si le serveur ne répond pas (la requête est alors
    abandonnée à un fil d'arrière-plan, qui se termine de lui-même).
    ``progress(reçus, total ou None)`` suit la réception."""
    if not isinstance(url, str) or not url.lower().startswith(("http://", "https://")):
        raise FetchError("not_web", str(url))
    if cancel is None and deadline is None:
        return _fetch(url, timeout, max_bytes, accept, None, progress)
    box: dict = {}
    done = threading.Event()
    abandon = threading.Event()

    def work():
        try:
            box["reply"] = _fetch(url, timeout, max_bytes, accept, abandon, progress)
        except BaseException as exc:  # renvoyée au fil appelant
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=work, daemon=True, name="manifestival-fetch").start()
    end = None if deadline is None else time.monotonic() + deadline
    while not done.wait(POLL):
        if cancel is not None and cancel.is_set():
            abandon.set()
            raise FetchError("cancelled", url)
        if end is not None and time.monotonic() > end:
            abandon.set()
            raise FetchError("timeout", url, detail="deadline")
    if "error" in box:
        raise box["error"]
    return box["reply"]


def _fetch(url, timeout, max_bytes, accept, abandon, progress) -> Response:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context()) as reply:
            length = reply.headers.get("Content-Length")
            total = int(length) if length and length.isdigit() else None
            if total is not None and total > max_bytes:
                raise FetchError("too_large", url)
            chunks, size = [], 0
            read = getattr(reply, "read1", reply.read)  # rend ce qui est arrivé
            while True:
                if abandon is not None and abandon.is_set():
                    raise FetchError("cancelled", url)
                chunk = read(CHUNK)
                if not chunk:
                    break
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError("too_large", url)
                chunks.append(chunk)
                if progress is not None and not (abandon is not None and abandon.is_set()):
                    progress(size, total)
            if total is not None and size < total:
                raise FetchError("network", url, detail="incomplete")
            content_type = (reply.headers.get("Content-Type") or "").split(";")[0].strip().lower()
            return Response(b"".join(chunks), content_type, reply.geturl() or url)
    except FetchError:
        raise
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read(65536) or b""
        except Exception:
            body = b""
        protection = protection_of(exc.headers, body)
        kind = "blocked" if is_bot_block(exc.code, exc.headers, body) else "http"
        raise FetchError(kind, url, exc.code, str(exc.reason or ""), _retry_after(exc.headers),
                         protection=protection) from None
    except urllib.error.URLError as exc:
        reason = exc.reason
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise FetchError("timeout", url) from None
        raise FetchError("network", url, detail=str(reason)) from None
    except (socket.timeout, TimeoutError):
        raise FetchError("timeout", url) from None
    except (OSError, ValueError, http.client.HTTPException) as exc:
        raise FetchError("network", url, detail=str(exc)) from None
