# -*- coding: utf-8 -*-
"""Fenêtre d'aperçu : une image en plus grand, avec sa case à cocher."""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (QCheckBox, QDialog, QHBoxLayout, QLabel, QPushButton,
                               QSizePolicy, QVBoxLayout)

from ..images import preview_urls
from .loader import ImageTask, TaskSignals

PREVIEW_BOX = 1600


class PreviewDialog(QDialog):
    def __init__(self, tr, model, row: int, parent=None, fetch=None):
        super().__init__(parent)
        self.tr_ = tr
        self.model = model
        self.row = row
        self.fetch = fetch
        self.pixmap: QPixmap | None = None
        self.cancel = threading.Event()
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.signals = TaskSignals(self)
        self.signals.done.connect(self._loaded)
        self.resize(900, 1000)

        self.title = QLabel()
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setWordWrap(True)
        self.image = QLabel()
        self.image.setAlignment(Qt.AlignCenter)
        self.image.setMinimumSize(200, 200)
        self.image.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.check = QCheckBox()
        self.check.toggled.connect(self._toggled)
        self.previous = QPushButton()
        self.next = QPushButton()
        self.close_button = QPushButton()
        self.previous.clicked.connect(lambda: self.show_row(self.row - 1))
        self.next.clicked.connect(lambda: self.show_row(self.row + 1))
        self.close_button.clicked.connect(self.close)
        for button in (self.previous, self.next, self.close_button):
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
        layout.addWidget(self.image, 1)
        layout.addLayout(buttons)

        QShortcut(QKeySequence(Qt.Key_Left), self, activated=lambda: self.show_row(self.row - 1))
        QShortcut(QKeySequence(Qt.Key_Right), self, activated=lambda: self.show_row(self.row + 1))
        QShortcut(QKeySequence(Qt.Key_Space), self, activated=self.check.toggle)
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
        self._update_title()

    def _update_title(self):
        if not 0 <= self.row < self.model.rowCount():
            return
        page = self.model.pages[self.row]
        self.title.setText(self.tr_("preview.caption", label=page.label or "—",
                                    index=page.index, count=self.model.rowCount(), row=self.row + 1))

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
        # La vignette en attendant l'image en plus grand.
        self.pixmap = None
        thumb = self.model.thumbs.get(row)
        if thumb:
            pm = QPixmap()
            if pm.loadFromData(thumb):
                self.pixmap = pm
        self.image.setText("" if self.pixmap else self.tr_("preview.loading"))
        self._show_pixmap()
        page = self.model.pages[row]
        kwargs = {"fetch": self.fetch} if self.fetch else {}
        self.pool.start(ImageTask(row, preview_urls(page, PREVIEW_BOX), PREVIEW_BOX,
                                  self.cancel, self.signals, **kwargs))

    def _loaded(self, key, data, error):
        if key != self.row:
            return
        if data:
            pm = QPixmap()
            if pm.loadFromData(data):
                self.pixmap = pm
                self._show_pixmap()
                return
        if self.pixmap is None:
            self.image.setText(self.tr_("preview.failed"))

    def _show_pixmap(self):
        if self.pixmap is None:
            self.image.setPixmap(QPixmap())
            return
        ratio = self.devicePixelRatioF()
        size = self.image.size() * ratio
        pm = self.pixmap.scaled(size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        pm.setDevicePixelRatio(ratio)
        self.image.setPixmap(pm)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._show_pixmap()

    def _sync_check(self, *args):
        checked = self.model.is_checked(self.row)
        if self.check.isChecked() != checked:
            self.check.blockSignals(True)
            self.check.setChecked(checked)
            self.check.blockSignals(False)

    def _toggled(self, value):
        self.model.set_checked([self.row], value)

    def closeEvent(self, event):
        self.cancel.set()
        try:
            self.model.dataChanged.disconnect(self._sync_check)
        except (RuntimeError, TypeError):
            pass
        super().closeEvent(event)

    def done(self, result):
        self.cancel.set()
        super().done(result)
