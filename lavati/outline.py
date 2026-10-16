"""Outline panel and 'Go to symbol' for Text Compare: the code structure of both files with added / removed / changed marks."""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QTreeWidget,
                               QTreeWidgetItem, QVBoxLayout)

from . import symbols, theme

KIND_TAG = {"class": "C", "interface": "I", "enum": "E", "struct": "S", "record": "R", "object": "O", "trait": "T", "namespace": "N",
            "module": "M", "protocol": "P", "extension": "X", "impl": "I", "function": "f", "method": "m", "heading": "#", "type": "T",
            "table": "T", "view": "V", "target": "t", "section": "[", "key": "k", "file": "F", "stage": "S", "union": "U", "contract": "C",
            "library": "L", "actor": "A", "event": "e", "modifier": "m", "procedure": "f", "index": "i", "trigger": "g", "package": "P",
            "program": "P", "subroutine": "f", "message": "M", "service": "S", "rpc": "f", "query": "q", "mutation": "q", "input": "I",
            "resource": "R", "data": "D", "variable": "v", "output": "o", "provider": "p", "locals": "l"}
STATUS_TEXT = {"added": "added", "removed": "removed", "changed": "changed", "same": ""}


def status_color(st):
    return {"added": theme.GREEN, "removed": theme.RED, "changed": theme.AMBER}.get(st, theme.MUTED)


def fuzzy(query, text):
    """All characters of the query appear in order (case-insensitive); substring matches rank first."""
    q, t = query.lower(), text.lower()
    if not q:
        return 0
    i = t.find(q)
    if i >= 0:
        return i
    pos = -1
    score = 100
    for ch in q:
        pos = t.find(ch, pos + 1)
        if pos < 0:
            return None
        score += pos
    return score


class OutlinePanel(QFrame):
    activated = Signal(int, int)             # left line, right line (-1 when the symbol exists on one side only)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet(f"OutlinePanel {{ background:{theme.SURFACE}; border-right:1px solid {theme.BORDER}; }}")
        self.setMinimumWidth(220)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(8, 8, 8, 8)
        lay.setSpacing(6)
        head = QHBoxLayout()
        t = QLabel("OUTLINE")
        t.setStyleSheet(f"color:{theme.MUTED}; font-weight:700; font-size:8pt; letter-spacing:1px;")
        self.count = QLabel()
        self.count.setStyleSheet(f"color:{theme.MUTED}; font-size:8pt;")
        head.addWidget(t)
        head.addStretch(1)
        head.addWidget(self.count)
        lay.addLayout(head)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter symbols")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._filter)
        lay.addWidget(self.search)
        self.only = QCheckBox("Changed only")
        self.only.toggled.connect(self._filter)
        lay.addWidget(self.only)
        self.tree = QTreeWidget()
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        self.tree.setIndentation(14)
        self.tree.setRootIsDecorated(True)
        self.tree.itemActivated.connect(self._go)
        self.tree.itemClicked.connect(self._go)
        lay.addWidget(self.tree, 1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color:{theme.MUTED};")
        lay.addWidget(self.note)
        self.rows = []

    def set_rows(self, rows, supported=True, language=""):
        self.rows = rows
        self.tree.clear()
        self.note.setText("" if supported else f"No code structure is available for {language or 'this file type'} yet.")
        stack = []
        for r in rows:
            while stack and stack[-1][0] >= r.depth:
                stack.pop()
            parent = stack[-1][1] if stack else self.tree.invisibleRootItem()
            it = QTreeWidgetItem(parent)
            it.setText(0, f"{KIND_TAG.get(r.kind, '·')}   {r.name}")
            it.setText(1, STATUS_TEXT[r.status])
            col = QColor(status_color(r.status))
            it.setForeground(0, QColor(theme.TEXT) if r.status in ("same", "changed") else col)
            it.setForeground(1, col)
            it.setToolTip(0, f"{r.kind} {r.qname.replace('/', ' > ')}")
            it.setData(0, Qt.UserRole, (r.lline, r.rline, r.status))
            stack.append((r.depth, it))
        self.tree.expandAll()
        self.tree.header().setStretchLastSection(False)
        from PySide6.QtWidgets import QHeaderView
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        n = sum(1 for r in rows if r.status != "same")
        self.count.setText(f"{len(rows)} symbols" + (f" · {n} changed" if n else ""))
        self._filter()

    def _filter(self, *_):
        text, only = self.search.text().strip(), self.only.isChecked()

        def visit(it):
            kids = [visit(it.child(i)) for i in range(it.childCount())]
            lline, rline, st = it.data(0, Qt.UserRole) or (-1, -1, "same")
            own = (not text or fuzzy(text, it.text(0)[4:]) is not None) and (not only or st != "same")
            show = own or any(kids)
            it.setHidden(not show)
            return show
        root = self.tree.invisibleRootItem()
        for i in range(root.childCount()):
            visit(root.child(i))

    def _go(self, item, _col=0):
        data = item.data(0, Qt.UserRole)
        if data:
            self.activated.emit(data[0], data[1])


class GoToSymbolDialog(QDialog):
    """Type a few letters of a class or method name and jump to it (like Ctrl+Shift+O in an IDE)."""

    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Go to symbol")
        self.setMinimumWidth(560)
        self.rows = rows
        self.choice = None
        lay = QVBoxLayout(self)
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("Type part of a class, method or heading name")
        self.edit.textChanged.connect(self._fill)
        self.edit.returnPressed.connect(self._accept)
        lay.addWidget(self.edit)
        self.list = QListWidget()
        self.list.setMinimumHeight(320)
        self.list.itemActivated.connect(lambda _i: self._accept())
        lay.addWidget(self.list, 1)
        hint = QLabel("Enter jumps to the symbol (left file first, right file if it only exists there)")
        hint.setStyleSheet(f"color:{theme.MUTED};")
        lay.addWidget(hint)
        self._fill()

    def _fill(self, *_):
        q = self.edit.text().strip()
        scored = []
        for r in self.rows:
            sc = fuzzy(q, r.qname.replace("/", " "))
            if sc is not None:
                scored.append((sc, r))
        scored.sort(key=lambda x: x[0])
        self.list.clear()
        for _sc, r in scored[:300]:
            tag = KIND_TAG.get(r.kind, "·")
            st = f"   [{STATUS_TEXT[r.status]}]" if r.status != "same" else ""
            where = f"L{r.lline + 1}" if r.lline >= 0 else ""
            where += (" " if where else "") + (f"R{r.rline + 1}" if r.rline >= 0 else "")
            it = QListWidgetItem(f"{tag}  {'  ' * r.depth}{r.name}    {where}{st}")
            it.setData(Qt.UserRole, (r.lline, r.rline))
            it.setForeground(QColor(status_color(r.status)) if r.status != "same" else QColor(theme.TEXT))
            self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Down, Qt.Key_Up) and self.list.count():
            row = self.list.currentRow() + (1 if e.key() == Qt.Key_Down else -1)
            self.list.setCurrentRow(max(0, min(self.list.count() - 1, row)))
            return
        super().keyPressEvent(e)

    def _accept(self):
        it = self.list.currentItem()
        if it is not None:
            self.choice = it.data(Qt.UserRole)
            self.accept()
