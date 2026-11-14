# -*- coding: utf-8 -*-
"""Fenêtre « Journal des refus des serveurs »."""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QApplication, QDialog, QHBoxLayout, QHeaderView,
                               QLabel, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

COLUMNS = ("col_date", "col_host", "col_status", "col_protection", "col_context", "col_url")


class RefusalsDialog(QDialog):
    def __init__(self, tr, log, parent=None):
        super().__init__(parent)
        self.tr_ = tr
        self.log = log
        self.resize(900, 420)
        self.intro = QLabel()
        self.intro.setWordWrap(True)
        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.empty = QLabel()
        self.empty.setAlignment(Qt.AlignCenter)
        self.status = QLabel()
        self.copy_button = QPushButton()
        self.clear_button = QPushButton()
        self.close_button = QPushButton()
        self.copy_button.clicked.connect(self.copy)
        self.clear_button.clicked.connect(self.clear)
        self.close_button.clicked.connect(self.accept)
        self.close_button.setDefault(True)
        buttons = QHBoxLayout()
        buttons.addWidget(self.copy_button)
        buttons.addWidget(self.clear_button)
        buttons.addWidget(self.status, 1)
        buttons.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.intro)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.empty)
        layout.addLayout(buttons)
        self.retranslate()

    def retranslate(self):
        tr = self.tr_
        self.setWindowTitle(tr("refusals.title"))
        self.intro.setText(tr("refusals.intro"))
        self.empty.setText(tr("refusals.empty"))
        self.copy_button.setText(tr("refusals.copy"))
        self.clear_button.setText(tr("refusals.clear"))
        self.close_button.setText(tr("button.close"))
        self.table.setHorizontalHeaderLabels([tr("refusals." + c) for c in COLUMNS])
        self.refresh()

    def refresh(self):
        tr = self.tr_
        items = list(reversed(self.log.items))   # le plus récent en haut
        self.table.setRowCount(len(items))
        for row, r in enumerate(items):
            status = str(r.status) if r.status else tr("refusals.no_answer")
            if r.count > 1:
                status += f" (×{r.count})"
            context = tr("refusals.context_" + r.context) if r.context else ""
            values = (r.time.replace("T", " "), r.host, status, r.protection or "—", context, r.url)
            for column, value in enumerate(values):
                cell = QTableWidgetItem(value)
                cell.setToolTip(r.manifest if column == 5 and r.manifest else value)
                self.table.setItem(row, column, cell)
        has_items = bool(items)
        self.table.setVisible(has_items)
        self.empty.setVisible(not has_items)
        self.copy_button.setEnabled(has_items)
        self.clear_button.setEnabled(has_items)

    def copy(self):
        QApplication.clipboard().setText(self.log.as_text())
        self.status.setText(self.tr_("refusals.copied"))

    def clear(self):
        self.log.clear()
        self.status.setText("")
        self.refresh()
