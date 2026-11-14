# -*- coding: utf-8 -*-
"""Fenêtre d'aperçu : une image que l'on peut zoomer jusqu'à sa pleine
résolution (tuiles IIIF), avec sa case à cocher."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton,
                               QToolButton, QVBoxLayout)

from .. import net
from ..images import preview_urls
from .loader import ImageTask, TaskSignals
from .zoom_view import ZoomView

PREVIEW_BOX = 1600


class PreviewDialog(QDialog):
    def __init__(self, tr, model, row: int, parent=None, fetch=None):
        super().__init__(parent)
        self.tr_ = tr
        self.model = model
        self.row = row
        self.fetch = fetch or net.fetch
        self.cancel = threading.Event()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.signals = TaskSignals(self)
        self.signals.done.connect(self._loaded)
        self.setWindowFlag(Qt.WindowMaximizeButtonHint, True)
        self.setWindowFlag(Qt.WindowMinimizeButtonHint, True)
        self.resize(1000, 1050)

        self.title = QLabel()
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setWordWrap(True)
        self.view = ZoomView(self, fetch=self.fetch)
        self.view.setMinimumSize(200, 200)
        self.view.zoomChanged.connect(self._update_status)
        self.view.loadingChanged.connect(self._update_status)

        # Barre du zoom
        self.zoom_out_button = QToolButton()
        self.zoom_in_button = QToolButton()
        self.fit_button = QPushButton()
        self.actual_button = QPushButton()
        self.zoom_out_button.setText("−")
        self.zoom_in_button.setText("+")
        for button in (self.zoom_out_button, self.zoom_in_button):
            button.setAutoRaise(False)
            button.setMinimumWidth(32)
        self.zoom_out_button.clicked.connect(self.view.zoom_out)
        self.zoom_in_button.clicked.connect(self.view.zoom_in)
        self.fit_button.clicked.connect(self.view.fit)
        self.actual_button.clicked.connect(self.view.actual_size)
        self.status = QLabel()
        self.hint = QLabel()
        self.hint.setStyleSheet("color: palette(placeholder-text);")
        zoom_bar = QHBoxLayout()
        for widget in (self.zoom_out_button, self.zoom_in_button, self.fit_button, self.actual_button):
            zoom_bar.addWidget(widget)
        zoom_bar.addWidget(self.status)
        zoom_bar.addStretch(1)
        zoom_bar.addWidget(self.hint)

        self.check = QCheckBox()
        self.check.toggled.connect(self._toggled)
        self.previous = QPushButton()
        self.next = QPushButton()
        self.close_button = QPushButton()
        self.previous.clicked.connect(lambda: self.show_row(self.row - 1))
        self.next.clicked.connect(lambda: self.show_row(self.row + 1))
        self.close_button.clicked.connect(self.close)
        for button in (self.previous, self.next, self.close_button, self.fit_button, self.actual_button):
            button.setAutoDefault(False)

        buttons = QHBoxLayout()
        buttons.addWidget(self.previous)
        buttons.addWidget(self.next)
        buttons.addStretch(1)
        buttons.addWidget(self.check)
        buttons.addStretch(1)
        buttons.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.title)
        layout.addLayout(zoom_bar)
        layout.addWidget(self.view, 1)
        layout.addLayout(buttons)

        QShortcut(QKeySequence(Qt.Key_Left), self, activated=lambda: self.show_row(self.row - 1))
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=lambda: self.show_row(self.row + 1))
        QShortcut(QKeySequence(Qt.Key_PageUp), self, activated=lambda: self.show_row(self.row - 1))
        QShortcut(QKeySequence(Qt.Key_PageDown), self, activated=lambda: self.show_row(self.row + 1))
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self.check.toggle)
        for keys, slot in ((QKeySequence.ZoomIn, self.view.zoom_in), ("+", self.view.zoom_in),
                           ("=", self.view.zoom_in), (QKeySequence.ZoomOut, self.view.zoom_out),
                           ("-", self.view.zoom_out), ("0", self.view.fit), ("1", self.view.actual_size)):
            QShortcut(QKeySequence(keys), self, activated=slot)
        self.model.dataChanged.connect(self._sync_check)
        self.retranslate()
        self.show_row(row)

    def retranslate(self):
        tr = self.tr_
        self.setWindowTitle(tr("preview.title"))
        self.previous.setText("◀ " + tr("preview.previous"))
        self.next.setText(tr("preview.next") + " ▶")
        self.close_button.setText(tr("button.close"))
        self.check.setText(tr("preview.selected"))
        self.zoom_out_button.setToolTip(tr("preview.zoom_out") + " (−)")
        self.zoom_in_button.setToolTip(tr("preview.zoom_in") + " (+)")
        self.fit_button.setText(tr("preview.fit"))
        self.fit_button.setToolTip(tr("preview.fit") + " (0)")
        self.actual_button.setText(tr("preview.actual"))
        self.actual_button.setToolTip(tr("preview.actual_tip") + " (1)")
        self.hint.setText(tr("preview.hint"))
        self._update_title()
        self._update_status()

    def _update_title(self):
        if not 0 <= self.row < self.model.rowCount():
            return
        page = self.model.pages[self.row]
        self.title.setText(self.tr_("preview.caption", label=page.label or "—",
                                    index=page.index, count=self.model.rowCount(), row=self.row + 1))

    def _update_status(self, *args):
        view = self.view
        if not view.full_width:
            self.status.setText(self.tr_("preview.loading"))
            return
        percent = round(view.device_zoom() * 100)
        text = self.tr_("preview.zoom_status", percent=percent,
                        width=view.full_width, height=view.full_height)
        if view.loading():
            text += " · " + self.tr_("preview.loading_detail")
        self.status.setText(text)
        self.zoom_in_button.setEnabled(view.zoom() < 4.0 - 1e-6)

    def show_row(self, row: int):
        if not 0 <= row < self.model.rowCount():
            return
        self.row = row
        self.cancel.set()
        self.cancel = threading.Event()
        self.previous.setEnabled(row > 0)
        self.next.setEnabled(row < self.model.rowCount() - 1)
        self._update_title()
        self._sync_check()
        page = self.model.pages[row]
        # La vignette tout de suite, l'aperçu ensuite, les détails au zoom.
        thumb = QPixmap()
        thumb_data = self.model.thumbs.get(row)
        self.view.set_page(page, thumb if thumb_data and thumb.loadFromData(thumb_data) else None)
        self.view.fit()
        self.pool.start(ImageTask(row, preview_urls(page, PREVIEW_BOX), PREVIEW_BOX,
                                  self.cancel, self.signals, self.fetch))
        self._update_status()

    def _loaded(self, key, data, error):
        if key != self.row or not data:
            return
        pm = QPixmap()
        if pm.loadFromData(data):
            self.view.set_base(pm)
            self._update_status()

    def _sync_check(self, *args):
        checked = self.model.is_checked(self.row)
        if self.check.isChecked() != checked:
            self.check.blockSignals(True)
            self.check.setChecked(checked)
            self.check.blockSignals(False)

    def _toggled(self, value):
        self.model.set_checked([self.row], value)

    def _stop(self):
        self.cancel.set()
        self.view.shutdown()

    def closeEvent(self, event):
        self._stop()
        try:
            self.model.dataChanged.disconnect(self._sync_check)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(event)

    def done(self, result):
        self._stop()
        super().done(result)
