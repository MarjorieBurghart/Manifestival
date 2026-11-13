# -*- coding: utf-8 -*-
"""Boîte « Télécharger » : dossier, noms des fichiers, taille, progression."""
from __future__ import annotations

from pathlib import Path
import threading

from PySide6.QtCore import QStandardPaths, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout)

from .. import download as dl
from .. import naming
from ..images import SIZES
from .texts import error_text

PREVIEW_COUNT = 4
DEFAULT_PARTS = ("prefix", "n", "label")


class DownloadThread(QThread):
    progressed = Signal(int, int, object)
    waiting = Signal(float)
    finished_with = Signal(object, object)  # résultats, erreur de l'index

    def __init__(self, jobs, folder, size, if_exists, manifest, write_index, index_stem,
                 delimiter, parent=None, fetch=None):
        super().__init__(parent)
        self.jobs, self.folder, self.size, self.if_exists = jobs, folder, size, if_exists
        self.manifest, self.write_index, self.index_stem = manifest, write_index, index_stem
        self.delimiter = delimiter
        self.cancel = threading.Event()
        self.fetch = fetch

    def run(self):
        kwargs = {"fetch": self.fetch} if self.fetch else {}
        index_error = None
        try:
            results = dl.download(self.jobs, self.folder, size=self.size, if_exists=self.if_exists,
                                  progress=lambda d, t, r: self.progressed.emit(d, t, r),
                                  cancel=self.cancel, notify=self.waiting.emit, **kwargs)
        except OSError as exc:
            self.finished_with.emit([], exc)
            return
        if self.write_index and results:
            try:
                dl.write_index(self.folder, results, self.manifest, self.index_stem, self.delimiter)
            except OSError as exc:
                index_error = exc
        self.finished_with.emit(results, index_error)


def _default_folder() -> str:
    for location in (QStandardPaths.PicturesLocation, QStandardPaths.DownloadLocation,
                     QStandardPaths.HomeLocation):
        path = QStandardPaths.writableLocation(location)
        if path:
            return path
    return str(Path.home())


class DownloadDialog(QDialog):
    def __init__(self, tr, settings, manifest, pages, parent=None, fetch=None):
        super().__init__(parent)
        self.tr_ = tr
        self.settings = settings
        self.manifest = manifest
        self.pages = list(pages)
        self.fetch = fetch
        self.thread: DownloadThread | None = None
        self.results: list[dl.Result] = []
        self.last_folder: Path | None = None
        self.setMinimumWidth(620)
        self._build()
        self._load_settings()
        self.retranslate()
        self._update_preview()

    # ------------------------------------------------------------ interface
    def _build(self):
        self.folder_label = QLabel()
        self.folder_edit = QLineEdit()
        self.folder_button = QPushButton()
        self.folder_button.clicked.connect(self._choose_folder)
        self.subfolder_check = QCheckBox()
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder_edit, 1)
        folder_row.addWidget(self.folder_button)

        # Noms des fichiers
        self.names_group = QGroupBox()
        self.prefix_label = QLabel()
        self.prefix_combo = QComboBox()
        self.prefix_combo.setEditable(True)
        self.prefix_combo.setInsertPolicy(QComboBox.NoInsert)
        self.prefix_combo.addItems(naming.prefix_suggestions(self.manifest))
        self.prefix_combo.setCurrentIndex(-1)
        self.prefix_combo.setEditText("")
        self.contains_label = QLabel()
        self.part_checks = {part: QCheckBox() for part in ("prefix", "n", "label", "meta")}
        self.meta_combo = QComboBox()
        self.meta_combo.addItems(self.manifest.page_metadata_labels())
        parts_box = QVBoxLayout()
        for part in ("prefix", "n", "label"):
            parts_box.addWidget(self.part_checks[part])
        meta_row = QHBoxLayout()
        meta_row.addWidget(self.part_checks["meta"])
        meta_row.addWidget(self.meta_combo, 1)
        parts_box.addLayout(meta_row)
        has_meta = self.meta_combo.count() > 0
        self.part_checks["meta"].setVisible(has_meta)
        self.meta_combo.setVisible(has_meta)

        self.separator_label = QLabel()
        self.separator_combo = QComboBox()
        self.replace_spaces_check = QCheckBox()
        self.accents_check = QCheckBox()
        self.custom_check = QCheckBox()
        self.template_edit = QLineEdit()
        self.template_help = QLabel()
        self.template_help.setWordWrap(True)
        self.template_help.setTextFormat(Qt.PlainText)
        self.preview_title = QLabel()
        self.preview_label = QLabel()
        self.preview_label.setTextFormat(Qt.PlainText)
        self.preview_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.preview_label.setStyleSheet("font-family: monospace;")

        names_form = QFormLayout(self.names_group)
        names_form.addRow(self.prefix_label, self.prefix_combo)
        names_form.addRow(self.contains_label, parts_box)
        names_form.addRow(self.separator_label, self.separator_combo)
        options_row = QHBoxLayout()
        options_row.addWidget(self.replace_spaces_check)
        options_row.addWidget(self.accents_check)
        options_row.addStretch(1)
        names_form.addRow("", options_row)
        names_form.addRow(self.custom_check, self.template_edit)
        names_form.addRow("", self.template_help)
        names_form.addRow(self.preview_title, self.preview_label)

        # Images
        self.images_group = QGroupBox()
        self.size_label = QLabel()
        self.size_combo = QComboBox()
        self.exists_label = QLabel()
        self.exists_combo = QComboBox()
        self.index_check = QCheckBox()
        images_form = QFormLayout(self.images_group)
        images_form.addRow(self.size_label, self.size_combo)
        images_form.addRow(self.exists_label, self.exists_combo)
        images_form.addRow("", self.index_check)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        self.status = QLabel()
        self.status.setWordWrap(True)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setVisible(False)
        self.details.setMaximumHeight(140)

        self.start_button = QPushButton()
        self.start_button.setDefault(True)
        self.cancel_button = QPushButton()
        self.cancel_button.setVisible(False)
        self.retry_button = QPushButton()
        self.retry_button.setVisible(False)
        self.open_button = QPushButton()
        self.open_button.setVisible(False)
        self.close_button = QPushButton()
        self.start_button.clicked.connect(self.start)
        self.cancel_button.clicked.connect(self.cancel)
        self.retry_button.clicked.connect(self.retry_failed)
        self.open_button.clicked.connect(self.open_folder)
        self.close_button.clicked.connect(self.reject)
        for button in (self.cancel_button, self.retry_button, self.open_button, self.close_button):
            button.setAutoDefault(False)
        buttons = QHBoxLayout()
        buttons.addWidget(self.open_button)
        buttons.addWidget(self.retry_button)
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.start_button)
        buttons.addWidget(self.close_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.folder_label)
        layout.addLayout(folder_row)
        layout.addWidget(self.subfolder_check)
        layout.addWidget(self.names_group)
        layout.addWidget(self.images_group)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.details)
        layout.addLayout(buttons)

        for check in (*self.part_checks.values(), self.replace_spaces_check,
                      self.accents_check, self.custom_check, self.subfolder_check):
            check.toggled.connect(self._options_changed)
        for combo in (self.separator_combo, self.meta_combo):
            combo.currentIndexChanged.connect(self._options_changed)
        self.prefix_combo.editTextChanged.connect(self._update_preview)
        self.template_edit.textChanged.connect(self._update_preview)
        self.folder_edit.textChanged.connect(self._update_preview)

    def retranslate(self):
        tr = self.tr_
        self.setWindowTitle(tr("download.title"))
        self.folder_label.setText(tr("download.folder"))
        self.folder_button.setText(tr("download.choose_folder"))
        self.subfolder_check.setText(tr("download.subfolder"))
        self.names_group.setTitle(tr("download.names"))
        self.prefix_label.setText(tr("download.prefix"))
        self.prefix_combo.lineEdit().setPlaceholderText(tr("download.prefix_placeholder"))
        self.contains_label.setText(tr("download.contains"))
        self.part_checks["prefix"].setText(tr("download.part_prefix"))
        self.part_checks["n"].setText(tr("download.part_number"))
        self.part_checks["label"].setText(tr("download.part_label"))
        self.part_checks["meta"].setText(tr("download.part_meta"))
        self.separator_label.setText(tr("download.separator"))
        current = max(0, self.separator_combo.currentIndex())
        self.separator_combo.blockSignals(True)
        self.separator_combo.clear()
        for sep, key in zip(naming.SEPARATORS, ("sep_underscore", "sep_hyphen", "sep_dot", "sep_space")):
            self.separator_combo.addItem(tr("download." + key), sep)
        self.separator_combo.setCurrentIndex(current)
        self.separator_combo.blockSignals(False)
        self.replace_spaces_check.setText(tr("download.replace_spaces"))
        self.accents_check.setText(tr("download.strip_accents"))
        self.custom_check.setText(tr("download.custom"))
        self.template_help.setText(tr("download.template_help"))
        self.preview_title.setText(tr("download.preview"))
        self.images_group.setTitle(tr("download.images"))
        self.size_label.setText(tr("download.size"))
        current = max(0, self.size_combo.currentIndex())
        self.size_combo.clear()
        for size in SIZES:
            self.size_combo.addItem(tr("download.size_max") if size == "max"
                                    else tr("download.size_px", px=size), size)
        self.size_combo.setCurrentIndex(current)
        self.exists_label.setText(tr("download.if_exists"))
        current = max(0, self.exists_combo.currentIndex())
        self.exists_combo.clear()
        for value, key in ((dl.SKIP, "exists_skip"), (dl.REPLACE, "exists_replace"),
                           (dl.KEEP_BOTH, "exists_keep")):
            self.exists_combo.addItem(tr("download." + key), value)
        self.exists_combo.setCurrentIndex(current)
        self.index_check.setText(tr("download.index"))
        self.start_button.setText(tr("download.start", n=len(self.pages)))
        self.cancel_button.setText(tr("button.cancel"))
        self.retry_button.setText(tr("download.retry"))
        self.open_button.setText(tr("download.open_folder"))
        self.close_button.setText(tr("button.close"))
        self._update_preview()

    # ------------------------------------------------------------ réglages
    def _load_settings(self):
        s = self.settings
        self.folder_edit.setText(str(s.value("download/folder", "") or _default_folder()))
        self.subfolder_check.setChecked(_bool(s.value("download/subfolder", True)))
        parts = s.value("naming/parts", list(DEFAULT_PARTS))
        parts = [parts] if isinstance(parts, str) else list(parts or [])
        for part, check in self.part_checks.items():
            check.setChecked(part in parts)
        meta = str(s.value("naming/meta_field", "") or "")
        if meta and self.meta_combo.findText(meta) >= 0:
            self.meta_combo.setCurrentText(meta)
        sep = str(s.value("naming/separator", "_"))
        self.separator_combo.setProperty("wanted", sep)
        self.replace_spaces_check.setChecked(_bool(s.value("naming/replace_spaces", True)))
        self.accents_check.setChecked(_bool(s.value("naming/strip_accents", False)))
        self.custom_check.setChecked(_bool(s.value("naming/custom", False)))
        self.template_edit.setText(str(s.value("naming/template", "") or ""))
        self._wanted_size = str(s.value("download/size", "max"))
        self._wanted_exists = str(s.value("download/if_exists", dl.SKIP))
        self.index_check.setChecked(_bool(s.value("download/index", True)))
        # Les listes sont remplies par retranslate() ; on y place ensuite
        # les choix enregistrés.
        self.retranslate()
        index = naming.SEPARATORS.index(sep) if sep in naming.SEPARATORS else 0
        self.separator_combo.setCurrentIndex(index)
        for combo, wanted in ((self.size_combo, self._wanted_size),
                              (self.exists_combo, self._wanted_exists)):
            for i in range(combo.count()):
                if str(combo.itemData(i)) == wanted:
                    combo.setCurrentIndex(i)
        if not self.template_edit.text():
            self.template_edit.setText(self._simple_template())
        self._sync_custom()

    def _save_settings(self):
        s = self.settings
        s.setValue("download/folder", self.folder_edit.text().strip())
        s.setValue("download/subfolder", self.subfolder_check.isChecked())
        s.setValue("naming/parts", [p for p, c in self.part_checks.items() if c.isChecked()])
        s.setValue("naming/meta_field", self.meta_combo.currentText())
        s.setValue("naming/separator", self.separator())
        s.setValue("naming/replace_spaces", self.replace_spaces_check.isChecked())
        s.setValue("naming/strip_accents", self.accents_check.isChecked())
        s.setValue("naming/custom", self.custom_check.isChecked())
        s.setValue("naming/template", self.template_edit.text())
        s.setValue("download/size", str(self.size_combo.currentData()))
        s.setValue("download/if_exists", self.exists_combo.currentData())
        s.setValue("download/index", self.index_check.isChecked())

    # ------------------------------------------------------------ noms
    def separator(self) -> str:
        return self.separator_combo.currentData() or "_"

    def _simple_template(self) -> str:
        parts = [p for p in ("prefix", "n", "label", "meta") if self.part_checks[p].isChecked()]
        meta = self.meta_combo.currentText() if self.meta_combo.count() else ""
        return naming.build_template(parts, self.separator(), meta)

    def template(self) -> str:
        if self.custom_check.isChecked():
            return self.template_edit.text()
        return self._simple_template()

    def options(self) -> naming.NamingOptions:
        return naming.NamingOptions(
            template=self.template(), prefix=self.prefix_combo.currentText().strip(),
            separator=self.separator(), replace_spaces=self.replace_spaces_check.isChecked(),
            strip_accents=self.accents_check.isChecked())

    def stems(self) -> list[str]:
        return naming.file_stems(self.pages, self.manifest, self.options())

    def target_folder(self) -> Path:
        base = Path(self.folder_edit.text().strip()).expanduser()
        if self.subfolder_check.isChecked():
            name = self.subfolder_name()
            if name:
                return base / name
        return base

    def subfolder_name(self) -> str:
        opts = self.options()
        text = opts.prefix or self.manifest.label
        return naming.clean(text, opts).strip("_-. ")[:naming.MAX_STEM]

    def _sync_custom(self):
        custom = self.custom_check.isChecked()
        for check in self.part_checks.values():
            check.setEnabled(not custom)
        self.meta_combo.setEnabled(not custom)
        self.template_edit.setEnabled(custom)
        self.template_help.setVisible(custom)
        if not custom:
            self.template_edit.setText(self._simple_template())

    def _options_changed(self, *args):
        self._sync_custom()
        self._update_preview()

    def _update_preview(self, *args):
        if not hasattr(self, "preview_label"):
            return
        stems = self.stems()
        shown = stems[:PREVIEW_COUNT]
        lines = [s + ".jpg" for s in shown]
        if len(stems) > PREVIEW_COUNT + 1:
            lines.append("…")
        if len(stems) > PREVIEW_COUNT:
            lines.append(stems[-1] + ".jpg")
        self.preview_label.setText("\n".join(lines))
        self.folder_edit.setToolTip(str(self.target_folder()))
        if self.thread is None and not self.results:
            self.status.setText(self.tr_("download.destination", path=str(self.target_folder())))

    def _choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, self.tr_("download.choose_folder_title"),
                                                  self.folder_edit.text() or _default_folder())
        if folder:
            self.folder_edit.setText(folder)

    # ------------------------------------------------------------ travail
    def _set_running(self, running: bool):
        for widget in (self.folder_edit, self.folder_button, self.subfolder_check,
                       self.names_group, self.images_group, self.start_button):
            widget.setEnabled(not running)
        self.cancel_button.setVisible(running)
        self.start_button.setVisible(not running)
        self.progress.setVisible(True)
        if running:
            self.open_button.setVisible(False)
            self.retry_button.setVisible(False)
            self.details.setVisible(False)

    def start(self, jobs=None):
        if not self.folder_edit.text().strip():
            QMessageBox.warning(self, self.tr_("download.title"), self.tr_("download.no_folder"))
            return
        folder = self.target_folder()
        try:
            folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            QMessageBox.warning(self, self.tr_("download.title"), error_text(exc, self.tr_))
            return
        self._save_settings()
        if jobs is None or isinstance(jobs, bool):
            jobs = list(zip(self.pages, self.stems()))
            self.results = []  # sinon : nouvel essai, fusionné dans _finished
        self.last_folder = folder
        self.progress.setRange(0, len(jobs))
        self.progress.setValue(0)
        self._set_running(True)
        self.status.setText(self.tr_("download.starting"))
        lang = getattr(self.tr_, "lang", "en")
        delimiter = "," if lang in ("en", "ja") else ";"
        index_stem = naming.clean(self.options().prefix, self.options()).strip("_-. ")
        self.thread = DownloadThread(jobs, folder, self.size_combo.currentData(),
                                     self.exists_combo.currentData(), self.manifest,
                                     self.index_check.isChecked(), index_stem, delimiter,
                                     self, fetch=self.fetch)
        self.thread.progressed.connect(self._progressed)
        self.thread.waiting.connect(self._waiting)
        self.thread.finished_with.connect(self._finished)
        self.thread.start()

    def _progressed(self, done, total, result):
        self.progress.setValue(done)
        name = result.path.name if result.path else result.stem
        self.status.setText(self.tr_("download.progress", done=done, total=total, name=name))

    def _waiting(self, seconds):
        self.status.setText(self.tr_("download.waiting", seconds=int(round(seconds))))

    def _finished(self, results, index_error):
        thread, self.thread = self.thread, None
        if thread is not None:
            thread.wait()
            thread.deleteLater()
        if isinstance(index_error, OSError) and not results:
            self._set_running(False)
            self.status.setText(error_text(index_error, self.tr_))
            return
        # Un nouvel essai ne remplace que les images réessayées.
        previous = {id(r.page): r for r in self.results}
        for r in results:
            previous[id(r.page)] = r
        self.results = list(previous.values()) if self.results else list(results)
        self._set_running(False)
        counts = {k: sum(1 for r in self.results if r.status == k)
                  for k in ("ok", "skipped", "failed", "cancelled")}
        lines = [self.tr_("download.done_ok", n=counts["ok"])]
        if counts["skipped"]:
            lines.append(self.tr_("download.done_skipped", n=counts["skipped"]))
        if counts["failed"]:
            lines.append(self.tr_("download.done_failed", n=counts["failed"]))
        if counts["cancelled"]:
            lines.append(self.tr_("download.done_cancelled", n=counts["cancelled"]))
        if index_error is not None:
            lines.append(error_text(index_error, self.tr_))
        self.status.setText(" · ".join(lines))
        failures = [r for r in self.results if r.status == "failed"]
        if failures:
            self.details.setPlainText("\n".join(
                f"#{r.page.index} {r.page.label} — {error_text(r.error, self.tr_)}" for r in failures))
            self.details.setVisible(True)
        self.retry_button.setVisible(bool(failures) or counts["cancelled"] > 0)
        self.open_button.setVisible(True)
        self.start_button.setText(self.tr_("download.start_again", n=len(self.pages)))

    def retry_failed(self):
        jobs = [(r.page, r.stem) for r in self.results if r.status in ("failed", "cancelled")]
        if jobs:
            self.start(jobs)

    def cancel(self):
        if self.thread is not None:
            self.thread.cancel.set()
            self.status.setText(self.tr_("download.cancelling"))

    def open_folder(self):
        if self.last_folder:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.last_folder)))

    def reject(self):
        """Fermeture (bouton, Échap ou fenêtre) : demande confirmation pendant
        un téléchargement ; QDialog.closeEvent passe aussi par ici."""
        if self.thread is not None:
            answer = QMessageBox.question(self, self.tr_("download.title"),
                                          self.tr_("download.confirm_stop"))
            if answer != QMessageBox.Yes:
                return
            self.thread.cancel.set()
            self.thread.wait(15000)
        else:
            self._save_settings()
        super().reject()


def _bool(value) -> bool:
    if isinstance(value, str):
        return value.lower() in ("1", "true", "yes")
    return bool(value)
