# -*- coding: utf-8 -*-
"""Chargement des images hors du fil de l'interface.

:class:`ImageTask` télécharge une image (en essayant plusieurs adresses) et
la réduit au besoin ; :class:`ThumbnailLoader` distribue les vignettes à un
petit nombre de tâches simultanées, en servant d'abord celles qui sont à
l'écran.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QRunnable, Qt, QThreadPool, Signal
from PySide6.QtGui import QImage

from .. import net

THUMBNAIL_BYTES = 40 * 1024 * 1024


class TaskSignals(QObject):
    # clé, données JPEG/PNG (b"" en cas d'échec), erreur
    done = Signal(object, bytes, object)


def shrink(data: bytes, box: int) -> bytes | None:
    """Image ``data`` ramenée dans un carré de ``box`` pixels (JPEG), ou
    telle quelle si elle y tient déjà ; None si ce n'est pas une image."""
    image = QImage.fromData(data)
    if image.isNull():
        return None
    if max(image.width(), image.height()) <= box:
        return data
    image = image.scaled(box, box, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    array = QByteArray()
    buffer = QBuffer(array)
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, "JPG", 88)
    buffer.close()
    return bytes(array.data())


class ImageTask(QRunnable):
    def __init__(self, key, urls, box, cancel: threading.Event, signals: TaskSignals,
                 fetch=net.fetch, retries: int = 2):
        super().__init__()
        self.key, self.urls, self.box = key, list(urls), box
        self.cancel, self.signals, self.fetch, self.retries = cancel, signals, fetch, retries
        self.setAutoDelete(True)

    def run(self):
        error = None
        for url in self.urls:
            attempt = 0
            while not self.cancel.is_set():
                try:
                    reply = self.fetch(url, timeout=20, deadline=90, max_bytes=THUMBNAIL_BYTES,
                                       accept="image/*", cancel=self.cancel)
                except net.FetchError as exc:
                    error = exc
                    if exc.kind == "blocked":
                        self._emit(b"", error)  # aucune autre adresse
                        return
                    if exc.retryable and attempt < self.retries:
                        attempt += 1
                        delay = min(20.0, exc.retry_after if exc.retry_after is not None else 2.0 * attempt)
                        if self.cancel.wait(delay):
                            break
                        continue
                    break
                except Exception as exc:  # image ou serveur inattendus
                    error = exc
                    break
                data = shrink(reply.data, self.box)
                if data is not None:
                    self._emit(data, None)
                    return
                protection = net.protection_of(None, reply.data)
                if protection:  # page de vérification anti-robots
                    self._emit(b"", net.FetchError("blocked", url, 200, protection=protection))
                    return
                error = net.FetchError("not_image", url, detail=reply.content_type)
                break
            if self.cancel.is_set():
                break
        self._emit(b"", error)

    def _emit(self, data, error):
        if self.cancel.is_set():
            return
        try:
            self.signals.done.emit(self.key, data, error)
        except RuntimeError:  # fenêtre déjà fermée
            pass


class ThumbnailLoader(QObject):
    """Vignettes d'une liste de pages ; ``loaded(rang, données)`` à chaque
    vignette reçue (données vides en cas d'échec)."""
    loaded = Signal(int, bytes)
    blocked = Signal(object)   # FetchError : le serveur refuse les requêtes

    def __init__(self, box: int, concurrency: int = 4, parent=None, fetch=net.fetch):
        super().__init__(parent)
        self.box = box
        self.concurrency = concurrency
        self.fetch = fetch
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(concurrency + 2)
        self.signals = TaskSignals(self)
        self.signals.done.connect(self._done)
        self.generation = 0
        self.cancel = threading.Event()
        self.urls: list[list[str]] = []
        self.pending: set[int] = set()
        self.cursor = 0
        self.flying: set[int] = set()   # rangs demandés, réponse attendue
        self.visible = (0, -1)

    def start(self, url_lists):
        self.stop()
        self.cancel = threading.Event()
        self.generation += 1
        self.urls = [list(u) for u in url_lists]
        self.pending = set(range(len(self.urls)))
        self.cursor = 0
        self.flying = set()
        self._pump()

    def stop(self):
        self.cancel.set()
        self.pending.clear()
        self.flying = set()

    def shutdown(self, wait_ms: int = 3000):
        self.stop()
        self.pool.clear()
        self.pool.waitForDone(wait_ms)

    def set_visible(self, first: int, last: int):
        self.visible = (first, last)
        self._pump()

    def remaining(self) -> int:
        return len(self.pending) + len(self.flying)

    def _next(self):
        first, last = self.visible
        # Un peu au-delà de ce qui est visible, pour le défilement.
        for row in range(max(0, first), last + 1 + max(0, last - first + 1)):
            if row in self.pending:
                return row
        while self.cursor < len(self.urls):
            row = self.cursor
            self.cursor += 1
            if row in self.pending:
                return row
        return None

    def _pump(self):
        while len(self.flying) < self.concurrency and self.pending:
            row = self._next()
            if row is None:
                break
            self.pending.discard(row)
            self.flying.add(row)
            self.pool.start(ImageTask((self.generation, row), self.urls[row], self.box,
                                      self.cancel, self.signals, self.fetch))

    def _done(self, key, data, error):
        generation, row = key
        if generation != self.generation:
            return
        if isinstance(error, net.FetchError) and error.kind == "blocked":
            # Protection anti-robots : on arrête de solliciter le serveur.
            # Les requêtes en cours sont abandonnées : leurs vignettes aussi
            # sont en échec, comme celles qui attendaient leur tour.
            rows = sorted(self.pending | self.flying | {row})
            self.pending, self.flying = set(), set()
            self.cancel.set()
            for r in rows:
                self.loaded.emit(r, b"")
            self.blocked.emit(error)
            return
        self.flying.discard(row)
        self.loaded.emit(row, data)
        self._pump()
