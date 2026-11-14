# -*- coding: utf-8 -*-
"""Navigateur intégré, pour les sites qui exigent un vrai navigateur.

Certains serveurs (Gallica, derrière Cloudflare) répondent aux logiciels
par une page de vérification que seul un navigateur sait passer. Pour ces
sites, Manifestival emploie un vrai moteur de navigateur (Qt WebEngine,
Chromium), sans se déguiser :

- :class:`VerifyDialog` ouvre le document dans une fenêtre de navigateur ;
  la personne y voit la vérification se faire (parfois une case à cocher),
  et la fenêtre se ferme d'elle-même quand le document s'affiche ;
- :class:`BrowserSession` fait ensuite, pour ce site, les requêtes par ce
  navigateur (des téléchargements ordinaires, dans la même session : les
  cookies de la vérification sont gardés d'une séance à l'autre), au
  rythme habituel de Manifestival ;
- :func:`routed_fetch` envoie au navigateur les requêtes de ces sites, et
  les autres au moteur réseau ordinaire (:func:`manifestival.net.fetch`).

Les téléchargements sont lancés dans le fil de l'interface (Qt WebEngine
l'exige) ; les tâches d'arrière-plan attendent leur résultat sans bloquer
l'interface, avec annulation et délais comme pour :func:`net.fetch`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
import sys
import tempfile
import threading
import time
import uuid

import shiboken6
from PySide6.QtCore import QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .. import net
from ..politeness import host_of

# Un moteur Chromium lancé par root (conteneurs, intégration continue)
# refuse de démarrer avec son bac à sable.
if sys.platform.startswith("linux") and hasattr(os, "geteuid") and os.geteuid() == 0:
    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")

try:
    from PySide6.QtWebEngineCore import QWebEngineDownloadRequest, QWebEnginePage, QWebEngineProfile
    from PySide6.QtWebEngineWidgets import QWebEngineView
    AVAILABLE = True
except ImportError:  # PySide6 sans Qt WebEngine
    AVAILABLE = False

SETTINGS_KEY = "browser/hosts"
DEFAULT_DEADLINE = 600.0


def available() -> bool:
    return AVAILABLE


@dataclass(eq=False)
class _Ticket:
    url: str
    max_bytes: int
    progress: object = None
    done: threading.Event = field(default_factory=threading.Event)
    last_activity: float = field(default_factory=time.monotonic)
    request: object = None
    result: net.Response | None = None
    error: Exception | None = None
    cancelled: bool = False


# Raisons d'interruption d'un téléchargement → erreurs de Manifestival.
def _error_for(reason, url, protection) -> net.FetchError:
    R = QWebEngineDownloadRequest.DownloadInterruptReason
    if reason == R.ServerForbidden:
        if protection:
            return net.FetchError("blocked", url, 403, protection=protection)
        return net.FetchError("http", url, 403)
    if reason == R.ServerUnauthorized:
        return net.FetchError("http", url, 401)
    if reason in (R.ServerBadContent,):
        return net.FetchError("http", url, 404)
    if reason in (R.ServerFailed, R.ServerNoRange, R.ServerCertProblem):
        return net.FetchError("http", url, 503 if reason == R.ServerFailed else 400)
    if reason == R.NetworkTimeout:
        return net.FetchError("timeout", url)
    if reason == R.UserCanceled:
        return net.FetchError("cancelled", url)
    return net.FetchError("network", url, detail=str(getattr(reason, "name", reason)))


class BrowserSession(QObject):
    """Le navigateur de Manifestival : profil persistant (cookies gardés),
    sites qui passent par lui, téléchargements pour les tâches de fond."""
    _start_requested = Signal(object)
    _cancel_requested = Signal(object)
    hostsChanged = Signal()

    def __init__(self, storage: str | Path | None, settings=None, parent=None):
        super().__init__(parent)
        self.settings = settings
        self.hosts: dict[str, str] = {}      # site → protection rencontrée
        if settings is not None:
            stored = settings.value(SETTINGS_KEY, [])
            stored = [stored] if isinstance(stored, str) else list(stored or [])
            for item in stored:             # « site|protection »
                host, _, protection = str(item).partition("|")
                if host:
                    self.hosts[host] = protection
        self._tmp = tempfile.TemporaryDirectory(prefix="manifestival-browser-")
        if storage:
            Path(storage).mkdir(parents=True, exist_ok=True)
            self.profile = QWebEngineProfile("Manifestival", self)
            self.profile.setPersistentStoragePath(str(Path(storage) / "profile"))
            self.profile.setCachePath(str(Path(storage) / "cache"))
            self.profile.setPersistentCookiesPolicy(QWebEngineProfile.ForcePersistentCookies)
        else:
            self.profile = QWebEngineProfile(self)   # sans disque (tests)
        self.page = QWebEnginePage(self.profile, self)
        self.pending: dict[str, list[_Ticket]] = {}
        self.profile.downloadRequested.connect(self._on_download)
        self._start_requested.connect(self._start, Qt.QueuedConnection)
        self._cancel_requested.connect(self._cancel, Qt.QueuedConnection)

    # ------------------------------------------------------------ sites
    def handles(self, url: str) -> bool:
        return host_of(url) in self.hosts

    def add_host(self, url_or_host: str, protection: str = ""):
        host = host_of(url_or_host) or url_or_host
        self.hosts[host] = protection or self.hosts.get(host, "")
        self._save()

    def shutdown(self):
        """Ferme le navigateur dans l'ordre que Qt WebEngine exige : les
        pages avant le profil (sinon, plantage à la sortie)."""
        if getattr(self, "_closed", False):
            return
        self._closed = True
        for queue in self.pending.values():
            for ticket in queue:
                ticket.error = net.FetchError("cancelled", ticket.url)
                ticket.done.set()
        self.pending.clear()
        try:
            self.profile.downloadRequested.disconnect(self._on_download)
        except (RuntimeError, TypeError):
            pass
        for page in [self.page] + [p for p in self.profile.findChildren(QWebEnginePage)]:
            if shiboken6.isValid(page):
                shiboken6.delete(page)
        if shiboken6.isValid(self.profile):
            shiboken6.delete(self.profile)
        self._tmp.cleanup()

    def forget(self):
        """Oublie les sites et efface cookies et cache du navigateur."""
        self.hosts.clear()
        self._save()
        self.profile.cookieStore().deleteAllCookies()
        self.profile.clearHttpCache()

    def _save(self):
        if self.settings is not None:
            self.settings.setValue(SETTINGS_KEY, [f"{h}|{p}" for h, p in sorted(self.hosts.items())])
        self.hostsChanged.emit()

    # ------------------------------------------------------------ requêtes (fil de fond)
    def fetch(self, url: str, *, timeout: float = 30, max_bytes: int = 50 * 1024 * 1024,
              accept: str = "*/*", cancel: threading.Event | None = None,
              deadline: float | None = None, progress=None) -> net.Response:
        """Comme :func:`net.fetch`, mais par le navigateur. À appeler depuis
        une tâche de fond : le fil de l'interface fait le téléchargement."""
        if QThread.currentThread() is self.thread():
            raise RuntimeError("BrowserSession.fetch ne doit pas bloquer le fil de l'interface")
        ticket = _Ticket(url, max_bytes, progress)
        self._start_requested.emit(ticket)
        end = time.monotonic() + (deadline or DEFAULT_DEADLINE)
        while not ticket.done.wait(net.POLL):
            now = time.monotonic()
            if cancel is not None and cancel.is_set():
                self._cancel_requested.emit(ticket)
                raise net.FetchError("cancelled", url)
            if now > end or now - ticket.last_activity > timeout:
                self._cancel_requested.emit(ticket)
                raise net.FetchError("timeout", url, detail="browser")
        if ticket.error is not None:
            raise ticket.error
        return ticket.result

    # ------------------------------------------------------------ fil de l'interface
    def _start(self, ticket: _Ticket):
        if ticket.cancelled:
            return
        self.pending.setdefault(ticket.url, []).append(ticket)
        ticket.last_activity = time.monotonic()
        self.page.download(QUrl(ticket.url), "")

    def _take_ticket(self, url: str) -> _Ticket | None:
        queue = self.pending.get(url)
        if queue:
            ticket = queue.pop(0)
            if not queue:
                del self.pending[url]
            return ticket
        # Adresse changée par une redirection : le plus ancien en attente.
        for key, queue in list(self.pending.items()):
            if queue:
                ticket = queue.pop(0)
                if not queue:
                    del self.pending[key]
                return ticket
        return None

    def _on_download(self, request):
        ticket = self._take_ticket(request.url().toString())
        if ticket is None or ticket.cancelled:
            request.cancel()    # téléchargement que Manifestival n'a pas demandé
            return
        ticket.request = request
        request.setDownloadDirectory(self._tmp.name)
        request.setDownloadFileName(uuid.uuid4().hex + ".part")

        def bytes_received(t=ticket, r=request):
            t.last_activity = time.monotonic()
            received, total = r.receivedBytes(), r.totalBytes()
            if received > t.max_bytes:
                t.error = net.FetchError("too_large", t.url)
                r.cancel()
            elif t.progress is not None:
                t.progress(received, total if total > 0 else None)

        request.receivedBytesChanged.connect(bytes_received)
        request.isFinishedChanged.connect(lambda t=ticket, r=request: self._finished(t, r))
        request.accept()

    def _finished(self, ticket: _Ticket, request):
        if not request.isFinished() or ticket.done.is_set():
            return
        path = Path(request.downloadDirectory()) / request.downloadFileName()
        try:
            if request.state() == QWebEngineDownloadRequest.DownloadCompleted and ticket.error is None:
                data = path.read_bytes()
                ticket.result = net.Response(data, (request.mimeType() or "").split(";")[0].strip().lower(),
                                             request.url().toString())
            elif ticket.error is None:
                ticket.error = _error_for(request.interruptReason(), ticket.url,
                                          self.hosts.get(host_of(ticket.url), ""))
        except OSError as exc:
            ticket.error = net.FetchError("network", ticket.url, detail=str(exc))
        finally:
            path.unlink(missing_ok=True)
            ticket.done.set()

    def _cancel(self, ticket: _Ticket):
        ticket.cancelled = True
        queue = self.pending.get(ticket.url)
        if queue and ticket in queue:
            queue.remove(ticket)
        if ticket.request is not None and not ticket.request.isFinished():
            ticket.request.cancel()


def routed_fetch(session_getter, base=net.fetch):
    """Fonction de requête de l'application : par le navigateur pour les
    sites qui l'exigent (``session_getter()`` rend la session ou None),
    par ``base`` (le moteur réseau ordinaire) sinon."""
    def fetch(url, **kwargs):
        session = session_getter()
        if session is not None and session.handles(url):
            return session.fetch(url, **kwargs)
        return base(url, **kwargs)
    return fetch


# Signes d'une page de vérification encore affichée.
_CHALLENGE_SIGNS = ("just a moment", "verify you are human", "checking your browser", "attention required",
                    "making sure you're not a bot", "vérification", "un instant")


class VerifyDialog(QDialog):
    """Fenêtre de navigateur sur le document : la vérification s'y fait ;
    ``verified`` est émis, et la fenêtre se ferme, quand le document
    (manifest JSON) s'affiche."""
    verified = Signal(str)          # site

    def __init__(self, tr, session: BrowserSession, url: str, protection: str = "", parent=None):
        super().__init__(parent)
        self.tr_ = tr
        self.session = session
        self.url = url
        self.protection = protection
        self.done_ = False
        # Fermée, la fenêtre est détruite avec sa page, avant le profil.
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.resize(900, 700)
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        self.view = QWebEngineView(self)
        self.view.setPage(QWebEnginePage(session.profile, self.view))
        self.status = QLabel()
        self.cancel_button = QPushButton()
        self.cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addWidget(self.status, 1)
        buttons.addWidget(self.cancel_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.intro)
        layout.addWidget(self.view, 1)
        layout.addLayout(buttons)
        self.view.loadFinished.connect(self._loaded)
        self.poll = QTimer(self, interval=1000, timeout=self._check)
        self.retranslate()

    def retranslate(self):
        tr = self.tr_
        self.setWindowTitle(tr("browser.title", host=host_of(self.url)))
        self.intro.setText(tr("browser.intro", host=host_of(self.url)))
        self.cancel_button.setText(tr("button.cancel"))
        self.status.setText(tr("browser.waiting"))

    def start(self):
        self.view.load(QUrl(self.url))
        self.poll.start()
        self.show()

    def _loaded(self, ok):
        self._check()

    def _check(self):
        if not self.done_:
            self.view.page().toPlainText(self._text)

    def _text(self, text):
        if self.done_:
            return
        sample = (text or "").lstrip()
        if sample.startswith(("{", "[")) and not any(s in sample[:2000].lower() for s in _CHALLENGE_SIGNS):
            self.done_ = True
            self.poll.stop()
            self.session.add_host(self.url, self.protection)
            self.status.setText(self.tr_("browser.ok"))
            QTimer.singleShot(400, self._finish)

    def _finish(self):
        self.verified.emit(host_of(self.url))
        self.accept()

    def done(self, result):
        self.poll.stop()
        self.view.stop()
        super().done(result)
