# -*- coding: utf-8 -*-
"""Grille des vignettes, avec une case à cocher par image.

Souris : un clic coche ou décoche l'image ; Maj+clic (ou Ctrl+Maj+clic)
donne à toutes les images entre la dernière cliquée et celle-ci l'état de
la dernière cliquée ; un double-clic agrandit l'image (sans changer la
case). Clavier : flèches, Espace (Maj+Espace pour une plage), Entrée.
"""
from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QAbstractListModel, QModelIndex, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QListView, QStyle, QStyledItemDelegate, QStyleOptionButton

PIXMAP_CACHE = 400


class ThumbnailModel(QAbstractListModel):
    checkedCountChanged = Signal(int)
    PageRole = Qt.UserRole + 1
    StateRole = Qt.UserRole + 2  # "loading", "ready", "failed"

    def __init__(self, parent=None):
        super().__init__(parent)
        self.pages = []
        self.checked: list[bool] = []
        self.thumbs: dict[int, bytes] = {}
        self.failed: set[int] = set()
        self._cache: OrderedDict = OrderedDict()

    # --- contenu
    def set_pages(self, pages):
        self.beginResetModel()
        self.pages = list(pages)
        self.checked = [False] * len(self.pages)
        self.thumbs.clear()
        self.failed.clear()
        self._cache.clear()
        self.endResetModel()
        self.checkedCountChanged.emit(0)

    def replace_pages(self, pages):
        """Mêmes pages, textes changés (autre langue) : cases et vignettes
        gardées."""
        if len(pages) != len(self.pages):
            return self.set_pages(pages)
        self.pages = list(pages)
        if self.pages:
            self.dataChanged.emit(self.index(0), self.index(len(self.pages) - 1))

    def set_thumbnail(self, row: int, data: bytes):
        if not 0 <= row < len(self.pages):
            return
        if data:
            self.thumbs[row] = data
            self.failed.discard(row)
        else:
            self.failed.add(row)
        for key in [k for k in self._cache if k[0] == row]:
            del self._cache[key]
        idx = self.index(row)
        self.dataChanged.emit(idx, idx)

    def pixmap(self, row: int, box: int, ratio: float = 1.0) -> QPixmap | None:
        key = (row, box, ratio)
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        data = self.thumbs.get(row)
        if not data:
            return None
        pm = QPixmap()
        if not pm.loadFromData(data):
            return None
        side = int(box * ratio)
        pm = pm.scaled(side, side, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(ratio)
        self._cache[key] = pm
        while len(self._cache) > PIXMAP_CACHE:
            self._cache.popitem(last=False)
        return pm

    # --- modèle Qt
    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self.pages)

    def flags(self, index):
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable | Qt.ItemIsUserCheckable

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid() or index.row() >= len(self.pages):
            return None
        row = index.row()
        page = self.pages[row]
        if role in (Qt.DisplayRole, Qt.ToolTipRole, Qt.AccessibleTextRole):
            return f"{page.index} · {page.label}" if page.label else str(page.index)
        if role == Qt.CheckStateRole:
            return Qt.Checked if self.checked[row] else Qt.Unchecked
        if role == self.PageRole:
            return page
        if role == self.StateRole:
            return "ready" if row in self.thumbs else "failed" if row in self.failed else "loading"
        return None

    def setData(self, index, value, role=Qt.EditRole):
        if role == Qt.CheckStateRole and index.isValid():
            state = Qt.CheckState(value) if not isinstance(value, Qt.CheckState) else value
            self.set_checked([index.row()], state == Qt.Checked)
            return True
        return False

    # --- cases à cocher
    def is_checked(self, row: int) -> bool:
        return 0 <= row < len(self.checked) and self.checked[row]

    def checked_rows(self) -> list[int]:
        return [i for i, c in enumerate(self.checked) if c]

    def checked_count(self) -> int:
        return sum(self.checked)

    def set_checked(self, rows, value: bool):
        rows = [r for r in rows if 0 <= r < len(self.checked) and self.checked[r] != value]
        if not rows:
            return
        for r in rows:
            self.checked[r] = value
        self.dataChanged.emit(self.index(min(rows)), self.index(max(rows)), [Qt.CheckStateRole])
        self.checkedCountChanged.emit(self.checked_count())

    def toggle(self, row: int):
        if 0 <= row < len(self.checked):
            self.set_checked([row], not self.checked[row])

    def set_range(self, first: int, last: int, value: bool):
        lo, hi = sorted((first, last))
        self.set_checked(range(lo, hi + 1), value)

    def set_all(self, value: bool):
        self.set_checked(range(len(self.checked)), value)

    def invert(self):
        if not self.checked:
            return
        self.checked = [not c for c in self.checked]
        self.dataChanged.emit(self.index(0), self.index(len(self.checked) - 1), [Qt.CheckStateRole])
        self.checkedCountChanged.emit(self.checked_count())


class ThumbnailDelegate(QStyledItemDelegate):
    MARGIN = 6

    def __init__(self, parent=None, box: int = 160):
        super().__init__(parent)
        self.box = box
        self.failed_text = "✕"

    def editorEvent(self, event, model, option, index):
        # Les cases sont gérées par ThumbnailView (clic, Maj+clic) : le
        # délégué ne doit pas les inverser une seconde fois.
        return False

    def sizeHint(self, option, index):
        line = option.fontMetrics.height()
        return QSize(self.box + 2 * self.MARGIN + 6, self.box + 2 * self.MARGIN + 2 * line + 12)

    def paint(self, painter: QPainter, option, index):
        model = index.model()
        row = index.row()
        page = model.pages[row]
        checked = model.is_checked(row)
        palette = option.palette
        widget = option.widget
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        tile = option.rect.adjusted(3, 3, -3, -3)

        highlight = palette.color(palette.ColorRole.Highlight)
        if checked:
            fill = QColor(highlight)
            fill.setAlpha(55)
            painter.setBrush(fill)
            painter.setPen(QPen(highlight, 2))
        else:
            painter.setBrush(palette.color(palette.ColorRole.Base))
            painter.setPen(QPen(palette.color(palette.ColorRole.Mid), 1))
        painter.drawRoundedRect(tile, 6, 6)
        if option.state & QStyle.State_HasFocus:
            pen = QPen(highlight, 1, Qt.DashLine)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(tile.adjusted(2, 2, -2, -2), 5, 5)

        image_rect = QRect(tile.left() + self.MARGIN, tile.top() + self.MARGIN,
                           tile.width() - 2 * self.MARGIN, self.box)
        ratio = widget.devicePixelRatioF() if widget is not None else 1.0
        pm = model.pixmap(row, self.box, ratio)
        if pm is not None:
            w = pm.width() / pm.devicePixelRatio()
            h = pm.height() / pm.devicePixelRatio()
            x = image_rect.left() + (image_rect.width() - w) / 2
            y = image_rect.top() + (image_rect.height() - h) / 2
            painter.drawPixmap(int(x), int(y), pm)
        else:
            painter.setPen(palette.color(palette.ColorRole.PlaceholderText))
            big = QFont(option.font)
            big.setPointSizeF(max(8.0, option.font.pointSizeF() * 1.6))
            painter.setFont(big)
            failed = row in model.failed
            painter.drawText(image_rect, Qt.AlignCenter, self.failed_text if failed else "…")
            painter.setFont(option.font)

        # Case à cocher, sur un fond clair pour rester lisible sur l'image.
        box_side = 22
        box_rect = QRect(tile.left() + 4, tile.top() + 4, box_side, box_side)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 215))
        painter.drawRoundedRect(box_rect, 4, 4)
        check = QStyleOptionButton()
        check.rect = box_rect.adjusted(2, 2, -2, -2)
        check.state = QStyle.State_Enabled | (QStyle.State_On if checked else QStyle.State_Off)
        style = widget.style() if widget is not None else None
        if style is not None:
            style.drawPrimitive(QStyle.PE_IndicatorCheckBox, check, painter, widget)

        # Libellé et position.
        metrics = option.fontMetrics
        text_top = image_rect.bottom() + 6
        width = tile.width() - 2 * self.MARGIN
        painter.setPen(palette.color(palette.ColorRole.Text))
        label = page.label or "—"
        painter.drawText(QRect(image_rect.left(), text_top, width, metrics.height()),
                         Qt.AlignHCenter | Qt.AlignVCenter,
                         metrics.elidedText(label, Qt.ElideMiddle, width))
        painter.setPen(palette.color(palette.ColorRole.PlaceholderText))
        painter.drawText(QRect(image_rect.left(), text_top + metrics.height(), width, metrics.height()),
                         Qt.AlignHCenter | Qt.AlignVCenter, f"#{page.index}")
        painter.restore()


class ThumbnailView(QListView):
    previewRequested = Signal(int)
    visibleRowsChanged = Signal(int, int)

    def __init__(self, parent=None, box: int = 160):
        super().__init__(parent)
        self.setViewMode(QListView.IconMode)
        self.setResizeMode(QListView.Adjust)
        self.setMovement(QListView.Static)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setSpacing(4)
        self.setSelectionMode(QListView.NoSelection)
        self.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.verticalScrollBar().setSingleStep(24)
        self.thumb_delegate = ThumbnailDelegate(self, box)
        self.setItemDelegate(self.thumb_delegate)
        self.anchor: int | None = None
        self._visible_timer = QTimer(self)
        self._visible_timer.setSingleShot(True)
        self._visible_timer.setInterval(60)
        self._visible_timer.timeout.connect(self._emit_visible)
        self.verticalScrollBar().valueChanged.connect(lambda _v: self._visible_timer.start())

    def setModel(self, model):
        super().setModel(model)
        model.modelReset.connect(self._reset)

    def _reset(self):
        self.anchor = None
        self._visible_timer.start()

    def set_box(self, box: int):
        self.thumb_delegate.box = box
        self.scheduleDelayedItemsLayout()
        self.viewport().update()
        self._visible_timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._visible_timer.start()

    def showEvent(self, event):
        super().showEvent(event)
        self._visible_timer.start()

    # --- rangées visibles (recherche par dichotomie : les rangées
    # suivent l'ordre vertical)
    def visible_rows(self) -> tuple[int, int]:
        model = self.model()
        count = model.rowCount() if model else 0
        if not count:
            return (0, -1)
        top, bottom = 0, self.viewport().height()
        lo, hi = 0, count - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self.visualRect(model.index(mid, 0)).bottom() < top:
                lo = mid + 1
            else:
                hi = mid
        first = lo
        last = first
        while last + 1 < count and self.visualRect(model.index(last + 1, 0)).top() <= bottom:
            last += 1
        return first, last

    def _emit_visible(self):
        self.visibleRowsChanged.emit(*self.visible_rows())

    # --- souris et clavier
    def click_row(self, row: int, modifiers):
        model = self.model()
        if model is None or not 0 <= row < model.rowCount():
            return
        if modifiers & Qt.ShiftModifier and self.anchor is not None and self.anchor < model.rowCount():
            model.set_range(self.anchor, row, model.is_checked(self.anchor))
        else:
            model.toggle(row)
            self.anchor = row

    def mousePressEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if event.button() != Qt.LeftButton or not index.isValid():
            return super().mousePressEvent(event)
        self.setFocus(Qt.MouseFocusReason)
        self.setCurrentIndex(index)
        self.click_row(index.row(), event.modifiers())
        event.accept()

    def mouseMoveEvent(self, event):
        event.accept()  # pas de glisser-déposer ni de sélection au lasso

    def mouseDoubleClickEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if event.button() != Qt.LeftButton or not index.isValid():
            return super().mouseDoubleClickEvent(event)
        # Le premier clic a changé la case : le double-clic la rétablit.
        if not event.modifiers() & Qt.ShiftModifier:
            self.model().toggle(index.row())
        self.previewRequested.emit(index.row())
        event.accept()

    def keyPressEvent(self, event):
        current = self.currentIndex()
        if event.key() == Qt.Key_Space and current.isValid():
            self.click_row(current.row(), event.modifiers())
            event.accept()
            return
        if event.key() in (Qt.Key_Return, Qt.Key_Enter) and current.isValid():
            self.previewRequested.emit(current.row())
            event.accept()
            return
        super().keyPressEvent(event)
