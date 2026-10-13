"""Dialogs and editors shared by the text compare views: rules editor, patch view."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
                               QFormLayout, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMenu,
                               QPlainTextEdit, QPushButton, QSpinBox, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from . import engine, theme

WS_LABELS = [("none", "Compare whitespace exactly"),
             ("edges", "Ignore leading and trailing whitespace"),
             ("changes", "Ignore changes in the amount of whitespace"),
             ("all", "Ignore all whitespace")]


class RulesEditor(QWidget):
    """Edits a TextRules object."""
    changed = Signal()

    def __init__(self, rules, parent=None, show_eol=True):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)
        self.ws = QComboBox()
        for key, label in WS_LABELS:
            self.ws.addItem(label, key)
        self.ws.setCurrentIndex(max(0, self.ws.findData(rules.ws)))
        form.addRow("Whitespace", self.ws)
        lay.addLayout(form)
        self.case = QCheckBox("Ignore case")
        self.case.setChecked(rules.ignore_case)
        self.eol = QCheckBox("Ignore line-ending differences (CRLF / LF / CR)")
        self.eol.setChecked(rules.ignore_eol)
        lay.addWidget(self.case)
        if show_eol:
            lay.addWidget(self.eol)

        lay.addWidget(self._title("Unimportant text",
                                  "Text matching these regular expressions is ignored when deciding whether lines differ "
                                  "(comments, timestamps, version keywords …)."))
        self.unimp = QListWidget()
        self.unimp.setFixedHeight(110)
        self.unimp.setSelectionMode(QAbstractItemView.ExtendedSelection)
        for u in rules.unimportant:
            self._add_unimp(u)
        lay.addWidget(self.unimp)
        row = QHBoxLayout()
        b_preset = QPushButton("Add preset…")
        b_add = QPushButton("Add pattern")
        b_rm = QPushButton("Remove")
        m = QMenu(b_preset)
        for name, rx in engine.UNIMPORTANT_PRESETS.items():
            m.addAction(name, lambda rx=rx: self._add_unimp(rx, True))
        b_preset.setMenu(m)
        b_add.clicked.connect(lambda: self._add_unimp("", True, edit=True))
        b_rm.clicked.connect(self._remove_unimp)
        for b in (b_preset, b_add, b_rm):
            row.addWidget(b)
        row.addStretch(1)
        lay.addLayout(row)

        lay.addWidget(self._title("Replacements",
                                  "Applied to every line before comparing, e.g. replace \\d+ with N to ignore numbers."))
        self.rep = QTableWidget(0, 2)
        self.rep.setHorizontalHeaderLabels(["Regular expression", "Replace with"])
        self.rep.horizontalHeader().setStretchLastSection(True)
        self.rep.setColumnWidth(0, 260)
        self.rep.verticalHeader().hide()
        self.rep.setFixedHeight(120)
        for pat, rp in rules.replacements:
            self._add_rep(pat, rp)
        lay.addWidget(self.rep)
        row = QHBoxLayout()
        b1, b2 = QPushButton("Add replacement"), QPushButton("Remove")
        b1.clicked.connect(lambda: self._add_rep("", "", edit=True))
        b2.clicked.connect(self._remove_rep)
        row.addWidget(b1)
        row.addWidget(b2)
        row.addStretch(1)
        lay.addLayout(row)
        self.problem = QLabel()
        self.problem.setWordWrap(True)
        self.problem.setStyleSheet(f"color:{theme.RED};")
        lay.addWidget(self.problem)
        for w in (self.ws,):
            w.currentIndexChanged.connect(lambda *_: self._emit())
        for w in (self.case, self.eol):
            w.toggled.connect(lambda *_: self._emit())
        self.unimp.itemChanged.connect(lambda *_: self._emit())
        self.rep.itemChanged.connect(lambda *_: self._emit())
        self._emit()

    @staticmethod
    def _title(t, sub):
        w = QWidget()
        l = QVBoxLayout(w)
        l.setContentsMargins(0, 8, 0, 0)
        l.setSpacing(0)
        a = QLabel(t)
        a.setStyleSheet("font-weight:700;")
        b = QLabel(sub)
        b.setWordWrap(True)
        b.setStyleSheet(f"color:{theme.MUTED}; font-size:9pt;")
        l.addWidget(a)
        l.addWidget(b)
        return w

    def _add_unimp(self, rx, emit=False, edit=False):
        it = QListWidgetItem(rx)
        it.setFlags(it.flags() | Qt.ItemIsEditable)
        it.setFont(theme.mono(9))
        self.unimp.addItem(it)
        if edit:
            self.unimp.editItem(it)
        if emit:
            self._emit()

    def _remove_unimp(self):
        for it in self.unimp.selectedItems():
            self.unimp.takeItem(self.unimp.row(it))
        self._emit()

    def _add_rep(self, pat, rp, edit=False):
        r = self.rep.rowCount()
        self.rep.insertRow(r)
        self.rep.setItem(r, 0, QTableWidgetItem(pat))
        self.rep.setItem(r, 1, QTableWidgetItem(rp))
        if edit:
            self.rep.editItem(self.rep.item(r, 0))

    def _remove_rep(self):
        rows = sorted({i.row() for i in self.rep.selectedItems()}, reverse=True)
        for r in rows:
            self.rep.removeRow(r)
        self._emit()

    def rules(self):
        unimp = [self.unimp.item(i).text() for i in range(self.unimp.count()) if self.unimp.item(i).text().strip()]
        reps = []
        for r in range(self.rep.rowCount()):
            a = self.rep.item(r, 0)
            b = self.rep.item(r, 1)
            if a and a.text().strip():
                reps.append((a.text(), b.text() if b else ""))
        return engine.TextRules(ws=self.ws.currentData(), ignore_case=self.case.isChecked(),
                                ignore_eol=self.eol.isChecked(), unimportant=unimp, replacements=reps)

    def _emit(self):
        bad = self.rules().invalid_patterns()
        self.problem.setText("Invalid pattern: " + "; ".join(bad[:2]) if bad else "")
        self.changed.emit()


class RulesDialog(QDialog):
    def __init__(self, rules, parent=None, title="Text compare rules"):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        self.editor = RulesEditor(rules)
        lay.addWidget(self.editor)
        self.save_default = QCheckBox("Use these rules as the default for new comparisons")
        lay.addWidget(self.save_default)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        lay.addWidget(bb)

    def _ok(self):
        if self.editor.rules().invalid_patterns():
            return
        self.accept()

    def rules(self):
        return self.editor.rules()


def store_rules_in_config(cfg, rules):
    cfg["ignore_whitespace"] = rules.ws != "none"
    cfg["ignore_case_text"] = rules.ignore_case
    cfg["ignore_eol"] = rules.ignore_eol
    cfg["unimportant"] = list(rules.unimportant)
    cfg["replacements"] = [list(x) for x in rules.replacements]


class DiffHighlighter(QSyntaxHighlighter):
    def __init__(self, doc):
        super().__init__(doc)
        self.fmts = {}
        for key, col in (("+", theme.GREEN), ("-", theme.RED), ("@", theme.PURPLE), ("h", theme.MUTED)):
            f = QTextCharFormat()
            f.setForeground(QColor(col))
            if key in "@h":
                f.setFontWeight(700)
            self.fmts[key] = f

    def highlightBlock(self, text):
        if text.startswith(("+++", "---")):
            self.setFormat(0, len(text), self.fmts["h"])
        elif text.startswith("@@"):
            self.setFormat(0, len(text), self.fmts["@"])
        elif text.startswith("+"):
            self.setFormat(0, len(text), self.fmts["+"])
        elif text.startswith("-"):
            self.setFormat(0, len(text), self.fmts["-"])


class PatchDialog(QDialog):
    """Unified-diff view ('Text Patch') of the two sides as they are right now."""

    def __init__(self, left_lines, right_lines, left_name, right_name, rules, anchors=None, parent=None,
                 left_time="", right_time=""):
        super().__init__(parent)
        self.setWindowTitle("Text patch")
        self.resize(900, 640)
        self.a, self.b, self.ln, self.rn, self.rules, self.anchors = left_lines, right_lines, left_name, right_name, rules, anchors
        self.lt, self.rt = left_time, right_time
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        row.addWidget(QLabel("Context lines"))
        self.ctx = QSpinBox()
        self.ctx.setRange(0, 50)
        self.ctx.setValue(3)
        self.use_rules = QCheckBox("Apply compare rules (ignore whitespace / case / unimportant text)")
        self.use_rules.setChecked(not rules.is_plain())
        self.use_rules.setEnabled(not rules.is_plain())
        row.addWidget(self.ctx)
        row.addSpacing(12)
        row.addWidget(self.use_rules)
        row.addStretch(1)
        lay.addLayout(row)
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setFont(theme.mono(10))
        self.text.setLineWrapMode(QPlainTextEdit.NoWrap)
        DiffHighlighter(self.text.document())
        lay.addWidget(self.text, 1)
        self.info = QLabel()
        self.info.setStyleSheet(f"color:{theme.MUTED};")
        lay.addWidget(self.info)
        row = QHBoxLayout()
        b_copy, b_save, b_close = QPushButton("Copy"), QPushButton("Save as .patch…"), QPushButton("Close")
        b_copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.text.toPlainText()))
        b_save.clicked.connect(self.save)
        b_close.clicked.connect(self.accept)
        row.addStretch(1)
        for b in (b_copy, b_save, b_close):
            b.setMinimumHeight(34)
            row.addWidget(b)
        lay.addLayout(row)
        self.ctx.valueChanged.connect(self.refresh)
        self.use_rules.toggled.connect(self.refresh)
        self.refresh()

    def patch_text(self):
        rules = self.rules if self.use_rules.isChecked() else engine.TextRules()
        lines = engine.unified_diff(self.a, self.b, self.ln, self.rn, self.ctx.value(), rules,
                                    self.anchors, self.lt, self.rt)
        return lines

    def refresh(self):
        lines = self.patch_text()
        self.text.setPlainText("\n".join(lines))
        hunks = sum(1 for l in lines if l.startswith("@@"))
        self.info.setText(f"{hunks} hunk(s) · {len(lines)} lines" if lines else "The files are identical under the current rules.")

    def save(self):
        p, _ = QFileDialog.getSaveFileName(self, "Save patch", "changes.patch", "Patch (*.patch *.diff);;All files (*)")
        if p:
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(self.patch_text()) + "\n")
