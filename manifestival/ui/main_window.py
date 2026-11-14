# -*- coding: utf-8 -*-
"""Fenêtre principale."""
from __future__ import annotations

from pathlib import Path
import threading

from PySide6.QtCore import QLibraryInfo, Qt, QThread, QTranslator, QUrl, Signal
from PySide6.QtGui import QAction, QActionGroup, QKeySequence
from PySide6.QtWidgets import (QApplication, QComboBox, QFileDialog, QHBoxLayout, QInputDialog,
                               QLabel, QMainWindow, QMessageBox, QPushButton, QSlider,
                               QSplitter, QStackedWidget, QTabWidget, QTextBrowser,
                               QVBoxLayout, QWidget)

from .. import APP_NAME, __version__, net
from ..i18n import LANGUAGES
from ..images import thumbnail_urls
from ..manifest import collection_entries, is_collection, parse_manifest
from ..politeness import rule_for
from ..sources import load_json, normalize
from ..refusals import RefusalLog
from .download_dialog import DownloadDialog
from .refusals_dialog import RefusalsDialog
from .loader import ThumbnailLoader
from .preview import PreviewDialog
from .texts import error_text, manifest_html, page_html
from .thumbnails import ThumbnailModel, ThumbnailView

MAX_RECENT = 15
THUMB_SIZES = (100, 240)
LOAD_BOX = 240


class ManifestThread(QThread):
    loaded = Signal(object, str)        # JSON, adresse
    failed = Signal(object)

    def __init__(self, source, parent=None, fetch=net.fetch):
        super().__init__(parent)
        self.source = source
        self.fetch = fetch
        self.cancel = threading.Event()

    def run(self):
        try:
            data = load_json(self.source, fetch=self.fetch, cancel=self.cancel)
            if not self.cancel.is_set():
                self.loaded.emit(data, normalize(self.source))
        except Exception as exc:
            self.failed.emit(exc)


class MainWindow(QMainWindow):
    def __init__(self, tr, settings, fetch=net.fetch, refusals: RefusalLog | None = None):
        super().__init__()
        self.tr_ = tr
        self.settings = settings
        self.fetch = fetch
        # Journal des refus des serveurs ; sans fichier, le temps de la session.
        self.refusals = refusals if refusals is not None else RefusalLog()
        self.refusals_dialog: RefusalsDialog | None = None
        self.manifest = None
        self.manifest_thread: ManifestThread | None = None
        self.pending_source: str | None = None
        self.download_dialog: DownloadDialog | None = None
        self.preview_dialog: PreviewDialog | None = None
        self.qt_translator: QTranslator | None = None
        self.setAcceptDrops(True)
        self._build()
        self._restore()
        self.install_qt_translation()
        self.retranslate()

    # ------------------------------------------------------------ interface
    def _build(self):
        # Barre d'adresse
        self.address_label = QLabel()
        self.address_combo = QComboBox()
        self.address_combo.setEditable(True)
        self.address_combo.setInsertPolicy(QComboBox.NoInsert)
        self.address_combo.setMinimumContentsLength(40)
        self.address_combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        self.address_combo.lineEdit().returnPressed.connect(self.open_address)
        self.address_combo.activated.connect(lambda _i: self.open_address())
        self.open_button = QPushButton()
        self.open_button.setDefault(True)
        self.open_button.clicked.connect(self._open_clicked)
        self.file_button = QPushButton()
        self.file_button.clicked.connect(self.open_file)
        top = QHBoxLayout()
        top.addWidget(self.address_label)
        top.addWidget(self.address_combo, 1)
        top.addWidget(self.open_button)
        top.addWidget(self.file_button)

        # Grille et sélection
        self.model = ThumbnailModel(self)
        self.view = ThumbnailView(self, box=int(self.settings.value("view/thumb", 160)))
        self.view.setModel(self.model)
        self.view.previewRequested.connect(self.show_preview)
        self.view.visibleRowsChanged.connect(lambda a, b: self.loader.set_visible(a, b))
        self.view.selectionModel().currentChanged.connect(self._current_changed)
        self.loader = ThumbnailLoader(LOAD_BOX, parent=self, fetch=self.fetch)
        self.loader.loaded.connect(self._thumbnail_loaded)
        self.loader.blocked.connect(self._thumbnails_blocked)
        self.blocked_message = ""
        self.model.checkedCountChanged.connect(self._update_counts)

        self.select_all_button = QPushButton()
        self.select_none_button = QPushButton()
        self.invert_button = QPushButton()
        self.select_all_button.clicked.connect(lambda: self.model.set_all(True))
        self.select_none_button.clicked.connect(lambda: self.model.set_all(False))
        self.invert_button.clicked.connect(self.model.invert)
        self.size_label = QLabel()
        self.size_slider = QSlider(Qt.Horizontal)
        self.size_slider.setRange(*THUMB_SIZES)
        self.size_slider.setValue(self.view.thumb_delegate.box)
        self.size_slider.setMaximumWidth(160)
        self.size_slider.valueChanged.connect(self.view.set_box)
        selection_bar = QHBoxLayout()
        for button in (self.select_all_button, self.select_none_button, self.invert_button):
            selection_bar.addWidget(button)
        selection_bar.addStretch(1)
        selection_bar.addWidget(self.size_label)
        selection_bar.addWidget(self.size_slider)

        self.hint_label = QLabel()
        self.hint_label.setWordWrap(True)
        self.count_label = QLabel()
        self.download_button = QPushButton()
        self.download_button.setEnabled(False)
        self.download_button.setMinimumHeight(36)
        self.download_button.clicked.connect(self.open_download)
        bottom = QHBoxLayout()
        bottom.addWidget(self.count_label, 1)
        bottom.addWidget(self.download_button)

        grid_page = QWidget()
        grid_layout = QVBoxLayout(grid_page)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.addLayout(selection_bar)
        grid_layout.addWidget(self.view, 1)
        grid_layout.addWidget(self.hint_label)
        grid_layout.addLayout(bottom)

        self.welcome = QLabel()
        self.welcome.setWordWrap(True)
        self.welcome.setAlignment(Qt.AlignCenter)
        self.welcome.setTextFormat(Qt.RichText)
        self.welcome.setMargin(40)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.welcome)
        self.stack.addWidget(grid_page)

        # Métadonnées
        self.tabs = QTabWidget()
        self.manifest_info = QTextBrowser()
        self.page_info = QTextBrowser()
        for browser in (self.manifest_info, self.page_info):
            browser.setOpenExternalLinks(True)
            browser.setOpenLinks(True)
        self.tabs.addTab(self.manifest_info, "")
        self.tabs.addTab(self.page_info, "")

        self.splitter = QSplitter(Qt.Horizontal)
        self.splitter.addWidget(self.stack)
        self.splitter.addWidget(self.tabs)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.addLayout(top)
        layout.addWidget(self.splitter, 1)
        self.setCentralWidget(central)
        self._build_menus()

    def _build_menus(self):
        bar = self.menuBar()
        self.file_menu = bar.addMenu("")
        self.action_open_file = QAction(self, triggered=self.open_file)
        self.action_open_file.setShortcut(QKeySequence.Open)
        self.action_focus_address = QAction(self, triggered=self._focus_address)
        self.action_focus_address.setShortcut(QKeySequence("Ctrl+L"))
        self.action_download = QAction(self, triggered=self.open_download)
        self.action_download.setShortcut(QKeySequence("Ctrl+D"))
        self.action_download.setEnabled(False)
        self.action_quit = QAction(self, triggered=self.close)
        self.action_quit.setShortcut(QKeySequence.Quit)
        self.action_quit.setMenuRole(QAction.QuitRole)
        for action in (self.action_focus_address, self.action_open_file, None,
                       self.action_download, None, self.action_quit):
            if action is None:
                self.file_menu.addSeparator()
            else:
                self.file_menu.addAction(action)

        self.selection_menu = bar.addMenu("")
        self.action_all = QAction(self, triggered=lambda: self.model.set_all(True))
        self.action_all.setShortcut(QKeySequence.SelectAll)
        self.action_none = QAction(self, triggered=lambda: self.model.set_all(False))
        self.action_none.setShortcut(QKeySequence("Ctrl+Shift+A"))
        self.action_invert = QAction(self, triggered=self.model.invert)
        self.action_invert.setShortcut(QKeySequence("Ctrl+I"))
        for action in (self.action_all, self.action_none, self.action_invert):
            self.selection_menu.addAction(action)

        self.language_menu = bar.addMenu("")
        self.language_group = QActionGroup(self)
        self.language_actions = {}
        for code, name in LANGUAGES.items():
            action = QAction(name, self, checkable=True)
            action.triggered.connect(lambda _c=False, c=code: self.set_language(c))
            self.language_group.addAction(action)
            self.language_menu.addAction(action)
            self.language_actions[code] = action

        self.help_menu = bar.addMenu("")
        self.action_help = QAction(self, triggered=self.show_help)
        self.action_help.setShortcut(QKeySequence.HelpContents)
        self.action_about = QAction(self, triggered=self.show_about)
        self.action_about.setMenuRole(QAction.AboutRole)
        self.action_refusals = QAction(self, triggered=self.show_refusals)
        self.help_menu.addAction(self.action_help)
        self.help_menu.addAction(self.action_refusals)
        self.help_menu.addSeparator()
        self.help_menu.addAction(self.action_about)

    def retranslate(self):
        tr = self.tr_
        self._update_title()
        self.address_label.setText(tr("main.address"))
        self.address_combo.lineEdit().setPlaceholderText(tr("main.address_placeholder"))
        self.open_button.setText(tr("button.cancel") if self.manifest_thread is not None else tr("main.open"))
        self.file_button.setText(tr("main.open_file"))
        self.select_all_button.setText(tr("main.select_all"))
        self.select_none_button.setText(tr("main.select_none"))
        self.invert_button.setText(tr("main.invert"))
        self.size_label.setText(tr("main.thumb_size"))
        self.hint_label.setText(tr("main.hint"))
        self.welcome.setText(tr("main.welcome"))
        self.tabs.setTabText(0, tr("main.tab_manifest"))
        self.tabs.setTabText(1, tr("main.tab_page"))
        self.file_menu.setTitle(tr("menu.file"))
        self.action_focus_address.setText(tr("menu.address"))
        self.action_open_file.setText(tr("menu.open_file"))
        self.action_download.setText(tr("menu.download"))
        self.action_quit.setText(tr("menu.quit"))
        self.selection_menu.setTitle(tr("menu.selection"))
        self.action_all.setText(tr("main.select_all"))
        self.action_none.setText(tr("main.select_none"))
        self.action_invert.setText(tr("main.invert"))
        self.language_menu.setTitle(tr("menu.language"))
        self.language_actions[tr.lang].setChecked(True)
        self.help_menu.setTitle(tr("menu.help"))
        self.action_help.setText(tr("menu.guide"))
        self.action_refusals.setText(tr("menu.refusals"))
        if self.refusals_dialog is not None:
            self.refusals_dialog.retranslate()
        self.action_about.setText(tr("menu.about", app=APP_NAME))
        self.view.thumb_delegate.failed_text = "✕"
        self._update_counts()
        self._show_metadata()
        if self.download_dialog is not None and self.download_dialog.isVisible():
            self.download_dialog.retranslate()
        if self.preview_dialog is not None and self.preview_dialog.isVisible():
            self.preview_dialog.retranslate()

    def _update_title(self):
        if self.manifest is not None and self.manifest.label:
            self.setWindowTitle(f"{self.manifest.label} — {APP_NAME}")
        else:
            self.setWindowTitle(APP_NAME)

    # ------------------------------------------------------------ langue
    def install_qt_translation(self):
        app = QApplication.instance()
        if self.qt_translator is not None:
            app.removeTranslator(self.qt_translator)
            self.qt_translator = None
        translator = QTranslator(self)
        path = QLibraryInfo.path(QLibraryInfo.TranslationsPath)
        if translator.load(f"qtbase_{self.tr_.lang}", path):
            app.installTranslator(translator)
            self.qt_translator = translator

    def set_language(self, code: str):
        self.tr_.set_language(code)
        self.settings.setValue("language", code)
        self.install_qt_translation()
        if self.manifest is not None:
            # Les textes multilingues du manifest suivent la langue choisie.
            self.manifest = parse_manifest(self.manifest.raw, self.manifest.source, code)
            self.model.replace_pages(self.manifest.pages)
        self.retranslate()

    # ------------------------------------------------------------ réglages
    def _restore(self):
        geometry = self.settings.value("window/geometry")
        if geometry is not None:
            self.restoreGeometry(geometry)
        else:
            self.resize(1200, 800)
        state = self.settings.value("window/splitter")
        if state is not None:
            self.splitter.restoreState(state)
        else:
            self.splitter.setSizes([850, 350])
        recent = self.settings.value("recent", [])
        recent = [recent] if isinstance(recent, str) else list(recent or [])
        self.address_combo.addItems([r for r in recent if r])
        self.address_combo.setCurrentIndex(-1)
        self.address_combo.setEditText("")

    def _remember(self, source: str):
        recent = [self.address_combo.itemText(i) for i in range(self.address_combo.count())]
        recent = [source] + [r for r in recent if r != source]
        recent = recent[:MAX_RECENT]
        self.address_combo.blockSignals(True)
        self.address_combo.clear()
        self.address_combo.addItems(recent)
        self.address_combo.setCurrentIndex(0)
        self.address_combo.blockSignals(False)
        self.settings.setValue("recent", recent)

    def closeEvent(self, event):
        if self.download_dialog is not None and self.download_dialog.thread is not None:
            self.download_dialog.reject()
            if self.download_dialog.thread is not None:
                event.ignore()
                return
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/splitter", self.splitter.saveState())
        self.settings.setValue("view/thumb", self.view.thumb_delegate.box)
        self.loader.shutdown()
        if self.manifest_thread is not None:
            self.manifest_thread.cancel.set()
            self.manifest_thread.wait(2000)
        super().closeEvent(event)

    # ------------------------------------------------------------ ouverture
    def _focus_address(self):
        self.address_combo.setFocus()
        self.address_combo.lineEdit().selectAll()

    def _open_clicked(self):
        """Le bouton « Afficher » devient « Annuler » pendant le chargement."""
        if self.manifest_thread is not None:
            self.cancel_loading()
        else:
            self.open_address()

    def cancel_loading(self):
        if self.manifest_thread is not None:
            self.manifest_thread.cancel.set()
            self.pending_source = None

    def open_address(self):
        if self.manifest_thread is not None:
            return
        source = self.address_combo.currentText().strip()
        if not source:
            self._focus_address()
            return
        self.load(source)

    def open_file(self):
        start = str(self.settings.value("last_file_dir", "") or Path.home())
        path, _ = QFileDialog.getOpenFileName(self, self.tr_("main.open_file_title"), start,
                                              self.tr_("main.json_filter") + " (*.json);;* (*)")
        if path:
            self.settings.setValue("last_file_dir", str(Path(path).parent))
            self.address_combo.setEditText(path)
            self.load(path)

    def load(self, source: str):
        if self.manifest_thread is not None:
            return
        self.address_combo.setEditText(source)
        self.statusBar().showMessage(self.tr_("main.loading"))
        self.open_button.setText(self.tr_("button.cancel"))
        QApplication.setOverrideCursor(Qt.BusyCursor)
        self.manifest_thread = ManifestThread(source, self, self.fetch)
        self.manifest_thread.loaded.connect(self._json_loaded)
        self.manifest_thread.failed.connect(self._load_failed)
        self.manifest_thread.finished.connect(self._thread_finished)
        self.manifest_thread.start()

    def _thread_finished(self):
        thread, self.manifest_thread = self.manifest_thread, None
        if thread is not None:
            thread.deleteLater()
        self.open_button.setText(self.tr_("main.open"))
        QApplication.restoreOverrideCursor()
        pending, self.pending_source = self.pending_source, None
        if pending:
            self.load(pending)

    def _load_failed(self, exc):
        if getattr(exc, "kind", "") == "cancelled":
            self.statusBar().showMessage(self.tr_("main.loading_cancelled"), 5000)
            return
        source = normalize(self.manifest_thread.source) if self.manifest_thread is not None else ""
        self.refusals.record(exc, "manifest", source)
        self.statusBar().clearMessage()
        QMessageBox.warning(self, APP_NAME, self.tr_("main.load_failed") + "\n\n" + error_text(exc, self.tr_))

    def _json_loaded(self, data, source):
        if is_collection(data):
            entries = collection_entries(data, self.tr_.lang)
            self.statusBar().clearMessage()
            if not entries:
                QMessageBox.warning(self, APP_NAME, self.tr_("main.collection_empty"))
                return
            labels = [("📁 " if sub else "") + title for title, _url, sub in entries]
            choice, ok = QInputDialog.getItem(self, self.tr_("main.collection_title"),
                                              self.tr_("main.collection_choose"), labels, 0, False)
            if ok and choice in labels:
                url = entries[labels.index(choice)][1]
                if self.manifest_thread is None:
                    self.load(url)
                else:  # lancé quand le fil courant sera terminé
                    self.pending_source = url
            return
        try:
            manifest = parse_manifest(data, source, self.tr_.lang)
        except Exception as exc:
            self._load_failed(exc)
            return
        self._remember(source)
        self.show_manifest(manifest)

    def show_manifest(self, manifest):
        self.manifest = manifest
        self.model.set_pages(manifest.pages)
        self.stack.setCurrentIndex(1)
        self.blocked_message = ""
        urls = [thumbnail_urls(p, LOAD_BOX) for p in manifest.pages]
        first = next((u[0] for u in urls if u), manifest.source)
        self.loader.concurrency = rule_for(first).concurrency
        self.loader.start(urls)
        self.loader.set_visible(*self.view.visible_rows())
        self.view.scrollToTop()
        if manifest.pages:
            self.view.setCurrentIndex(self.model.index(0))
        self.tabs.setCurrentIndex(0)
        self._update_title()
        self._show_metadata()
        self._update_counts()

    # ------------------------------------------------------------ affichage
    def _thumbnail_loaded(self, row, data):
        self.model.set_thumbnail(row, data)
        self._update_counts()

    def _thumbnails_blocked(self, error):
        self.refusals.record(error, "thumbnails", self.manifest.source if self.manifest else "")
        self.blocked_message = error_text(error, self.tr_)
        self._update_counts()

    def _current_changed(self, current, previous):
        self._show_metadata()

    def _show_metadata(self):
        if self.manifest is None:
            self.manifest_info.setHtml(f"<p>{self.tr_('main.no_manifest')}</p>")
            self.page_info.clear()
            return
        self.manifest_info.setHtml(manifest_html(self.manifest, self.tr_))
        current = self.view.currentIndex()
        if current.isValid():
            page = self.model.pages[current.row()]
            self.page_info.setHtml(page_html(page, self.manifest, self.tr_))
        else:
            self.page_info.clear()

    def _update_counts(self, *args):
        total = self.model.rowCount()
        checked = self.model.checked_count()
        if self.manifest is None:
            self.count_label.setText("")
        else:
            self.count_label.setText(self.tr_("main.count", n=total) + " · "
                                     + self.tr_("main.selected", n=checked))
        self.download_button.setText(self.tr_("main.download", n=checked))
        self.download_button.setEnabled(checked > 0)
        self.action_download.setEnabled(checked > 0)
        remaining = self.loader.remaining() if self.manifest is not None else 0
        if self.blocked_message:
            self.statusBar().showMessage(self.blocked_message)
        elif remaining:
            self.statusBar().showMessage(self.tr_("main.thumbs_loading", n=remaining))
        else:
            self.statusBar().clearMessage()

    # ------------------------------------------------------------ actions
    def show_preview(self, row: int):
        if self.preview_dialog is not None:
            self.preview_dialog.close()
            self.preview_dialog.deleteLater()
        self.preview_dialog = PreviewDialog(self.tr_, self.model, row, self, fetch=self.fetch)
        self.preview_dialog.show()

    def open_download(self):
        rows = self.model.checked_rows()
        if not rows or self.manifest is None:
            return
        old = self.download_dialog
        if old is not None:
            if old.thread is not None:
                old.raise_()
                old.activateWindow()
                return
            old.deleteLater()
        pages = [self.model.pages[r] for r in rows]
        self.download_dialog = DownloadDialog(self.tr_, self.settings, self.manifest, pages,
                                              self, fetch=self.fetch, refusals=self.refusals)
        self.download_dialog.show()

    def show_help(self):
        box = QMessageBox(self)
        box.setWindowTitle(self.tr_("menu.guide"))
        box.setTextFormat(Qt.RichText)
        box.setText(self.tr_("help.text"))
        box.exec()

    def show_refusals(self):
        if self.refusals_dialog is None:
            self.refusals_dialog = RefusalsDialog(self.tr_, self.refusals, self)
        self.refusals_dialog.refresh()
        self.refusals_dialog.show()
        self.refusals_dialog.raise_()

    def show_about(self):
        QMessageBox.about(self, self.tr_("menu.about", app=APP_NAME),
                          self.tr_("help.about", app=APP_NAME, version=__version__)
                          + "<br><br>© 2026 Marjorie Burghart — MIT<br>"
                          + '<a href="https://github.com/MarjorieBurghart/Manifestival">'
                          + "github.com/MarjorieBurghart/Manifestival</a>")

    # ------------------------------------------------------------ glisser-déposer
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() or event.mimeData().hasText():
            event.acceptProposedAction()

    def dropEvent(self, event):
        mime = event.mimeData()
        source = ""
        if mime.hasUrls() and mime.urls():
            url: QUrl = mime.urls()[0]
            source = url.toLocalFile() if url.isLocalFile() else url.toString()
        elif mime.hasText():
            source = mime.text().strip().splitlines()[0] if mime.text().strip() else ""
        if source:
            event.acceptProposedAction()
            self.load(source)

