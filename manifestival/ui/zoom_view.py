# -*- coding: utf-8 -*-
"""Vue zoomable d'une image IIIF, jusqu'à sa pleine résolution.

La scène est à l'échelle de l'image en pleine résolution. Une image réduite
(vignette, puis aperçu) la couvre aussitôt ; quand on zoome au-delà de sa
finesse, les tuiles du service IIIF (``info.json``) sont demandées pour la
seule zone visible, au niveau de réduction suffisant, et posées par-dessus.
Sans tuiles (serveur de niveau 0, image sans service), l'image entière est
téléchargée une fois, en pleine taille.

Molette : zoomer autour du pointeur ; glisser : se déplacer ; double-clic :
zoomer à cet endroit.
"""
from __future__ import annotations

from collections import OrderedDict
import json
import threading

from PySide6.QtCore import QObject, QRectF, QRunnable, Qt, QThreadPool, QTimer, Signal
from PySide6.QtGui import QPainter, QPixmap, QTransform
from PySide6.QtWidgets import QFrame, QGraphicsPixmapItem, QGraphicsScene, QGraphicsView

from .. import net
from ..images import image_urls
from ..politeness import rule_for
from ..tiles import TileSource, parse_info
from .loader import ImageTask, TaskSignals

MAX_ZOOM = 4.0          # 4 pixels d'écran par pixel de l'image, au plus
ZOOM_STEP = 1.25
CONCURRENT_TILES = 4
TILE_PIXELS = 64_000_000   # pixels de tuiles gardés en mémoire (~250 Mo)
INFO_BYTES = 2 * 1024 * 1024
NO_SHRINK = 10 ** 6     # les tuiles et l'image entière gardent leur taille


class _InfoSignals(QObject):
    done = Signal(object, object)   # clé, info.json (dict) ou None


class InfoTask(QRunnable):
    def __init__(self, key, url, signals, cancel, fetch=net.fetch):
        super().__init__()
        self.key, self.url, self.signals, self.cancel, self.fetch = key, url, signals, cancel, fetch

    def run(self):
        info = None
        try:
            reply = self.fetch(self.url, timeout=20, deadline=60, max_bytes=INFO_BYTES,
                               accept="application/ld+json, application/json;q=0.9, */*;q=0.1",
                               cancel=self.cancel)
            info = json.loads(reply.data.decode("utf-8-sig"))
        except Exception:
            info = None
        if not self.cancel.is_set():
            try:
                self.signals.done.emit(self.key, info)
            except RuntimeError:
                pass


class ZoomView(QGraphicsView):
    zoomChanged = Signal(float)      # pixels d'écran par pixel de l'image
    loadingChanged = Signal(bool)    # tuiles ou image entière en cours

    def __init__(self, parent=None, fetch=net.fetch):
        super().__init__(parent)
        self.fetch = fetch
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(QPainter.SmoothPixmapTransform | QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorViewCenter)
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self.setFrameShape(QFrame.NoFrame)
        self.setBackgroundBrush(self.palette().dark())
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(CONCURRENT_TILES + 1)
        self.tile_signals = TaskSignals(self)
        self.tile_signals.done.connect(self._tile_loaded)
        self.info_signals = _InfoSignals(self)
        self.info_signals.done.connect(self._info_loaded)
        self.timer = QTimer(self, singleShot=True, interval=60, timeout=self.update_tiles)
        self.generation = 0
        self.cancel = threading.Event()
        self._reset_state()

    def _reset_state(self):
        self.page = None
        self.source: TileSource | None = None
        self.full_width = self.full_height = 0
        self.base_item: QGraphicsPixmapItem | None = None
        self.base_width = 0           # finesse de l'image réduite (pixels)
        self.tiles: OrderedDict = OrderedDict()   # (s, col, row) -> item
        self.tile_pixels = 0
        self.visible_keys: set = set()
        self.requested: set = set()
        self.queue: list = []
        self.in_flight = 0
        self.info_state = "none"      # none, loading, tiles, no_tiles
        self.full_state = "none"      # none, loading, done, failed
        self.fitted = True
        self.concurrency = CONCURRENT_TILES

    # ------------------------------------------------------------ page
    def set_page(self, page, base: QPixmap | None = None):
        """Nouvelle image : la scène est vidée et les tâches en cours
        oubliées. ``base`` : image réduite à montrer tout de suite."""
        self.cancel.set()
        self.cancel = threading.Event()
        self.generation += 1
        self.scene().clear()
        self._reset_state()
        self.page = page
        self.concurrency = min(CONCURRENT_TILES, rule_for(
            page.service.url if page.service else page.image_url).concurrency)
        self.full_width = page.width or 0
        self.full_height = page.height or 0
        if base is not None and not base.isNull():
            self.set_base(base)
        if page.service:
            self.info_state = "loading"
            self.pool.start(InfoTask((self.generation, "info"), page.service.url + "/info.json",
                                     self.info_signals, self.cancel, self.fetch))
        else:
            self.info_state = "no_tiles"
        self._emit_loading()

    def set_base(self, pixmap: QPixmap, *, full: bool = False):
        """Image réduite (ou entière si ``full``) qui couvre toute la scène."""
        if pixmap.isNull():
            return
        if not self.full_width or not self.full_height:
            self.full_width, self.full_height = pixmap.width(), pixmap.height()
        if self.base_item is not None and pixmap.width() <= self.base_width and not full:
            return
        if self.base_item is not None:
            self.scene().removeItem(self.base_item)
        item = QGraphicsPixmapItem(pixmap)
        item.setTransformationMode(Qt.SmoothTransformation)
        item.setScale(self.full_width / pixmap.width())
        item.setZValue(0)
        self.scene().addItem(item)
        self.base_item = item
        self.base_width = pixmap.width()
        self._set_scene_rect()
        self.timer.start()

    def _set_scene_rect(self):
        if not self.full_width or not self.full_height:
            return
        rect = QRectF(0, 0, self.full_width, self.full_height)
        if self.sceneRect() != rect:
            self.setSceneRect(rect)
            if self.fitted:
                self.fit()

    def _info_loaded(self, key, info):
        if key[0] != self.generation:
            return
        source = parse_info(info, self.page.service.url if self.page and self.page.service else "")
        if source is None:
            self.info_state = "no_tiles"
            try:  # la taille annoncée reste la plus sûre
                self._resize_scene(int(info["width"]), int(info["height"]))
            except (TypeError, ValueError, KeyError):
                pass
        else:
            self.info_state = "tiles"
            self.source = source
            self._resize_scene(source.width, source.height)
        self._emit_loading()
        self.timer.start()

    def _resize_scene(self, width: int, height: int):
        """Taille réelle de l'image, si elle diffère de celle du manifest :
        la scène change d'échelle sans que la vue ne bouge à l'écran."""
        if width <= 0 or height <= 0 or (width, height) == (self.full_width, self.full_height):
            return
        ratio = width / self.full_width if self.full_width else 1.0
        center = self.mapToScene(self.viewport().rect().center())
        self.full_width, self.full_height = width, height
        if self.base_item is not None:
            self.base_item.setScale(self.full_width / self.base_item.pixmap().width())
        for key in [k for k, item in self.tiles.items() if item is not None]:
            self.scene().removeItem(self.tiles.pop(key))
        self.tile_pixels = 0
        self.setSceneRect(QRectF(0, 0, width, height))
        if self.fitted:
            self.fit()
        else:
            self.scale(1 / ratio, 1 / ratio)
            self.centerOn(center * ratio)
            self._zoom_changed()

    # ------------------------------------------------------------ zoom
    def zoom(self) -> float:
        """Pixels d'écran (logiques) par pixel de l'image en pleine résolution."""
        return self.transform().m11()

    def fit_zoom(self) -> float:
        if not self.full_width or not self.full_height:
            return 1.0
        view = self.viewport().rect()
        return min(view.width() / self.full_width, view.height() / self.full_height)

    def fit(self):
        self.fitted = True
        self.setTransform(QTransform())
        if self.full_width and self.full_height:
            self.fitInView(self.sceneRect(), Qt.KeepAspectRatio)
        self._zoom_changed()

    def set_zoom(self, value: float, anchor_under_mouse: bool = False):
        low = min(self.fit_zoom(), 1.0)
        value = max(low, min(MAX_ZOOM, value))
        current = self.zoom() or 1.0
        if abs(value - current) < 1e-9:
            return
        if not anchor_under_mouse:
            self.setTransformationAnchor(QGraphicsView.AnchorViewCenter)
        self.scale(value / current, value / current)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.fitted = abs(value - self.fit_zoom()) < 1e-6
        self._zoom_changed()

    def zoom_in(self):
        self.set_zoom(self.zoom() * ZOOM_STEP)

    def zoom_out(self):
        self.set_zoom(self.zoom() / ZOOM_STEP)

    def actual_size(self):
        """100 % : un pixel de l'image par pixel de l'écran."""
        self.set_zoom(1.0 / self.devicePixelRatioF())

    def _zoom_changed(self):
        self.zoomChanged.emit(self.zoom())
        self.timer.start()

    def wheelEvent(self, event):
        steps = event.angleDelta().y() / 120.0
        if not steps:
            return super().wheelEvent(event)
        self.set_zoom(self.zoom() * (ZOOM_STEP ** steps), anchor_under_mouse=True)
        event.accept()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.centerOn(self.mapToScene(event.position().toPoint()))
            self.set_zoom(self.zoom() * 2)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.fitted:
            self.fit()
        else:
            self.timer.start()

    def scrollContentsBy(self, dx, dy):
        super().scrollContentsBy(dx, dy)
        self.timer.start()

    # ------------------------------------------------------------ détails
    def device_zoom(self) -> float:
        return self.zoom() * self.devicePixelRatioF()

    def needs_detail(self) -> bool:
        """L'image réduite est-elle trop grossière pour le zoom actuel ?"""
        if not self.full_width or not self.base_width:
            return bool(self.full_width)
        return self.device_zoom() > self.base_width / self.full_width * 1.05

    def update_tiles(self):
        if self.page is None or not self.full_width:
            return
        if not self.needs_detail():
            self.queue = []
            self._emit_loading()
            return
        if self.info_state == "tiles" and self.source is not None:
            self._queue_visible_tiles()
        elif self.info_state == "no_tiles" and self.full_state == "none":
            self._load_full_image()
        self._emit_loading()

    def _queue_visible_tiles(self):
        source = self.source
        scale = source.level_for(self.device_zoom())
        # Un niveau pas plus fin que l'image réduite n'apporte rien.
        if source.width / scale <= self.base_width:
            self.queue = []
            return
        area = self.mapToScene(self.viewport().rect()).boundingRect()
        wanted = source.visible_tiles(scale, area.left(), area.top(), area.right(), area.bottom())
        self.queue = [t for t in wanted if (t.scale, t.col, t.row) not in self.tiles
                      and (t.scale, t.col, t.row) not in self.requested]
        self.visible_keys = {(t.scale, t.col, t.row) for t in wanted}
        for key in self.visible_keys & set(self.tiles):
            self.tiles.move_to_end(key)
        self._pump()

    def _pump(self):
        while self.in_flight < self.concurrency and self.queue:
            tile = self.queue.pop(0)
            key = (tile.scale, tile.col, tile.row)
            self.requested.add(key)
            self.in_flight += 1
            self.pool.start(ImageTask((self.generation, tile), [self.source.tile_url(tile)],
                                      NO_SHRINK, self.cancel, self.tile_signals, self.fetch))

    def _tile_loaded(self, key, data, error):
        generation, tile = key
        if generation != self.generation:
            return
        if tile == "full":
            self._full_loaded(data)
            return
        self.in_flight = max(0, self.in_flight - 1)
        tkey = (tile.scale, tile.col, tile.row)
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            item = QGraphicsPixmapItem(pixmap)
            item.setTransformationMode(Qt.SmoothTransformation)
            item.setPos(tile.x, tile.y)
            # La tuile reçue peut différer d'un pixel de la taille demandée.
            item.setTransform(QTransform.fromScale(tile.w / pixmap.width(), tile.h / pixmap.height()))
            item.setZValue(1000 - tile.scale)   # les plus fines au-dessus
            self.scene().addItem(item)
            self.tiles[tkey] = item
            self.tile_pixels += pixmap.width() * pixmap.height()
            self._evict()
        else:
            # Tuile refusée : on n'insiste pas, l'image réduite reste visible.
            self.tiles[tkey] = None
        self.requested.discard(tkey)
        self._pump()
        self._emit_loading()

    def _evict(self):
        """Tuiles les plus anciennement vues retirées au-delà du budget
        (celles à l'écran viennent d'être remises en fin de liste)."""
        for key in list(self.tiles):
            if self.tile_pixels <= TILE_PIXELS:
                break
            if key in self.visible_keys:
                continue  # jamais une tuile à l'écran : elle serait redemandée
            item = self.tiles.pop(key)
            if item is not None:
                pixmap = item.pixmap()
                self.tile_pixels -= pixmap.width() * pixmap.height()
                self.scene().removeItem(item)

    def _load_full_image(self):
        urls = image_urls(self.page, "max")
        if not urls:
            self.full_state = "failed"
            return
        self.full_state = "loading"
        self.pool.start(ImageTask((self.generation, "full"), urls, NO_SHRINK, self.cancel,
                                  self.tile_signals, self.fetch))

    def _full_loaded(self, data):
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            self.full_state = "done"
            self._resize_scene(pixmap.width(), pixmap.height())
            self.set_base(pixmap, full=True)
        else:
            self.full_state = "failed"
        self._emit_loading()

    # ------------------------------------------------------------ état
    def loading(self) -> bool:
        return bool(self.in_flight or self.queue or self.full_state == "loading"
                    or (self.info_state == "loading" and self.needs_detail()))

    def _emit_loading(self):
        self.loadingChanged.emit(self.loading())

    def tile_count(self) -> int:
        return sum(1 for item in self.tiles.values() if item is not None)

    def shutdown(self):
        self.cancel.set()
        self.pool.clear()
