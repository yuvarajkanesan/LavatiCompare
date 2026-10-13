"""Views for Version, Media and Registry comparison."""
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QGuiApplication
from PySide6.QtWidgets import (QAbstractItemView, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMenu,
                               QTableWidget, QTableWidgetItem, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import engine, mediatags, regcmp, theme, versioninfo
from .folder_view import STATUS_TEXT, HatchDelegate
from .widgets import ToolbarAdapter, Segmented, sty, toast, tool_button, vsep
from .workers import TaskWorker


def _row_bg(status):
    return {"changed": theme.ROW_CHANGED, "left": theme.ROW_LEFT, "right": theme.ROW_RIGHT}.get(status)


class PropertyCompareView(QWidget):
    """Compares two lists of (group, property, value) rows - used for Version and Media compare."""
    resultChanged = Signal(str)

    def __init__(self, cfg, left, right, loader, noun="Properties", parent=None):
        super().__init__(parent)
        self.cfg, self.left, self.right, self.loader, self.noun = cfg, left, right, loader, noun
        self.rows = []
        self.mode = 1
        self.errors = []
        self._ui()
        self.compare()

    def _ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QFrame()
        sty(bar, f"background:{theme.SURFACE}; border-bottom:1px solid {theme.BORDER};")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 6, 16, 6)
        self.seg = Segmented(["All", "Differences", "Same"], current=1)
        self.seg.changed.connect(self._set_mode)
        bl.addWidget(self.seg)
        bl.addWidget(vsep())
        b = tool_button("Refresh", "refresh", "Read both files again (F5)")
        b.clicked.connect(self.compare)
        bl.addWidget(b)
        bl.addStretch(1)
        self.stats = QLabel()
        self.stats.setTextFormat(Qt.RichText)
        bl.addWidget(self.stats)
        ToolbarAdapter(bar)
        root.addWidget(bar)
        hdr = QFrame()
        sty(hdr, f"background:{theme.PANEL}; border-bottom:1px solid {theme.BORDER};")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(16, 8, 16, 8)
        self.lh, self.rh = QLabel(self.left), QLabel(self.right)
        for l in (self.lh, self.rh):
            l.setFont(theme.mono(10))
            l.setToolTip(l.text())
        hl.addWidget(self.lh, 1)
        hl.addSpacing(40)
        hl.addWidget(self.rh, 1)
        root.addWidget(hdr)
        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Group", self.noun[:-1] if self.noun.endswith("s") else self.noun, "Left", "", "Right"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setWordWrap(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        h = self.table.horizontalHeader()
        h.setHighlightSections(False)
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        h.setSectionResizeMode(1, QHeaderView.Fixed)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.Fixed)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 150)
        self.table.setColumnWidth(1, 220)
        self.table.setColumnWidth(3, 46)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._menu)
        self.table.setStyleSheet(f"QTableWidget::item {{ padding:6px 10px; border-bottom:1px solid {theme.LINE_SOFT}; }}")
        root.addWidget(self.table, 1)
        self.foot = QLabel()
        self.foot.setStyleSheet(f"color:{theme.RED}; padding:6px 16px;")
        self.foot.setWordWrap(True)
        root.addWidget(self.foot)

    def _set_mode(self, i):
        self.mode = i
        self.render()

    def _load(self, path):
        try:
            return self.loader(path), None
        except Exception as e:
            return [], f"{os.path.basename(path)}: {e}"

    def compare(self):
        L, el = self._load(self.left)
        R, er = self._load(self.right)
        self.foot.setText("\n".join(x for x in (el, er) if x))
        lmap = {(g, k): v for g, k, v in L}
        rmap = {(g, k): v for g, k, v in R}
        keys = [(g, k) for g, k, _ in L] + [(g, k) for g, k, _ in R if (g, k) not in lmap]
        # keep groups together, in order of first appearance
        order = {}
        for g, k in keys:
            order.setdefault(g, len(order))
        keys.sort(key=lambda gk: order[gk[0]])
        self.rows = []
        for g, k in keys:
            a, b = lmap.get((g, k)), rmap.get((g, k))
            st = "left" if b is None else ("right" if a is None else ("same" if a == b else "changed"))
            self.rows.append((g, k, a, b, st))
        self.render()
        c = {s: sum(1 for r in self.rows if r[4] == s) for s in ("changed", "left", "right", "same")}
        diffs = c["changed"] + c["left"] + c["right"]
        self.stats.setText(f"<b style='color:{theme.RED if diffs else theme.GREEN}'>{diffs} difference(s)</b> "
                           f"<span style='color:{theme.MUTED}'>&nbsp; {c['same']} identical</span>")
        self.resultChanged.emit(f"{diffs} diffs" if diffs else "identical")

    def reload(self):
        self.compare()

    def render(self):
        rows = [r for r in self.rows if self.mode == 0 or (self.mode == 1 and r[4] != "same") or (self.mode == 2 and r[4] == "same")]
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(rows))
        last = None
        mono = theme.mono(9)
        for i, (g, k, a, b, st) in enumerate(rows):
            vals = [g if g != last else "", k, "" if a is None else a, "", "" if b is None else b]
            last = g
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                it.setToolTip(v if c != 3 else STATUS_TEXT[st])
                bg = _row_bg(st)
                if bg:
                    it.setBackground(QColor(bg))
                if c == 0:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    it.setForeground(QColor(theme.MUTED))
                elif c == 3:
                    it.setIcon(theme.status_icon(st))
                elif c in (2, 4):
                    it.setFont(mono)
                    if st == "changed":
                        it.setForeground(QColor(theme.RED))
                self.table.setItem(i, c, it)
            self.table.setRowHeight(i, 34)
        self.table.setUpdatesEnabled(True)
        self.table.resizeRowsToContents()

    def _menu(self, pos):
        idx = self.table.indexAt(pos)
        if not idx.isValid():
            return
        m = QMenu(self)
        txt = self.table.item(idx.row(), idx.column()).text()
        m.addAction("Copy value", lambda: QGuiApplication.clipboard().setText(txt))
        m.addAction("Copy row", lambda: QGuiApplication.clipboard().setText(
            "\t".join(self.table.item(idx.row(), c).text() for c in range(5) if c != 3)))
        m.exec(self.table.viewport().mapToGlobal(pos))

    def has_unsaved(self):
        return False

    def swap_sides(self):
        self.left, self.right = self.right, self.left
        self.lh.setText(self.left)
        self.rh.setText(self.right)
        self.compare()


def VersionCompareView(cfg, left, right, parent=None):
    return PropertyCompareView(cfg, left, right, versioninfo.read_info, "Properties", parent)


def MediaCompareView(cfg, left, right, parent=None):
    return PropertyCompareView(cfg, left, right, mediatags.rows_for, "Tags", parent)


# ================================================================== registry
class RegistryCompareView(QWidget):
    resultChanged = Signal(str)

    def __init__(self, cfg, left, right, parent=None):
        super().__init__(parent)
        self.cfg, self.left, self.right = cfg, left, right
        self.nodes = []
        self.items = {}
        self.mode = 1
        self.worker = None
        self._ui()
        self.compare()

    def _ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QFrame()
        sty(bar, f"background:{theme.SURFACE}; border-bottom:1px solid {theme.BORDER};")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 6, 16, 6)
        self.seg = Segmented(["All", "Differences", "Same", "Orphans"], current=1)
        self.seg.changed.connect(self._set_mode)
        bl.addWidget(self.seg)
        bl.addWidget(vsep())
        self.b_expand = tool_button("Expand", "expand", "Expand / collapse all keys", checkable=True)
        self.b_expand.setChecked(True)
        self.b_expand.toggled.connect(lambda on: self.tree.expandAll() if on else self.tree.collapseAll())
        b = tool_button("Refresh", "refresh", "Read both sides again (F5)")
        b.clicked.connect(self.compare)
        bl.addWidget(self.b_expand)
        bl.addWidget(b)
        bl.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find keys or values")
        self.search.setFixedWidth(260)
        self.search.addAction(theme.icon("search", theme.MUTED, 16), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self.apply_filter)
        bl.addWidget(self.search)
        ToolbarAdapter(bar)
        root.addWidget(bar)
        hdr = QFrame()
        sty(hdr, f"background:{theme.PANEL}; border-bottom:1px solid {theme.BORDER};")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(16, 8, 16, 8)
        self.lh, self.rh = QLabel(self.left), QLabel(self.right)
        for l in (self.lh, self.rh):
            l.setFont(theme.mono(10))
            l.setToolTip(l.text())
        hl.addWidget(self.lh, 1)
        hl.addSpacing(60)
        hl.addWidget(self.rh, 1)
        root.addWidget(hdr)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(7)
        self.tree.setHeaderLabels(["Name", "Type", "Data", "", "Name", "Type", "Data"])
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(20)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setItemDelegate(HatchDelegate(self.tree))
        self.tree.setStyleSheet(f"QTreeWidget::item {{ height:30px; border-bottom:1px solid {theme.LINE_SOFT}; }}"
                                f"QTreeWidget::item:selected {{ background:{theme.ACCENT_SOFT}; color:{theme.TEXT}; }}")
        h = self.tree.header()
        h.setStretchLastSection(False)
        for c, mode, w in ((0, QHeaderView.Interactive, 230), (1, QHeaderView.Fixed, 110), (2, QHeaderView.Stretch, 0),
                           (3, QHeaderView.Fixed, 46), (4, QHeaderView.Interactive, 230), (5, QHeaderView.Fixed, 110),
                           (6, QHeaderView.Stretch, 0)):
            h.setSectionResizeMode(c, mode)
            if w:
                self.tree.setColumnWidth(c, w)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._menu)
        root.addWidget(self.tree, 1)
        self.busy = QLabel("Reading the registry…")
        self.busy.setStyleSheet(f"padding:8px 18px; color:{theme.MUTED};")
        self.busy.hide()
        root.addWidget(self.busy)
        foot = QFrame()
        sty(foot, f"background:{theme.SURFACE}; border-top:1px solid {theme.BORDER};")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(18, 7, 18, 7)
        self.legend = {}
        for key in ("changed", "left", "right", "same"):
            ic = QLabel()
            ic.setPixmap(theme.status_icon(key, 14).pixmap(14, 14))
            fl.addWidget(ic)
            lab = QLabel()
            lab.setStyleSheet(f"color:{theme.status_color(key)}; font-size:9pt;")
            fl.addWidget(lab)
            fl.addSpacing(12)
            self.legend[key] = lab
        fl.addStretch(1)
        self.err = QLabel()
        self.err.setStyleSheet(f"color:{theme.RED};")
        fl.addWidget(self.err)
        root.addWidget(foot)

    def _set_mode(self, i):
        self.mode = i
        self.apply_filter()

    def compare(self):
        self.cancel_work()
        L, R = self.left, self.right

        def load(ctx):
            a, b = regcmp.load(L, ctx), regcmp.load(R, ctx)
            nodes = regcmp.compare_registry(a, b, ctx)
            return nodes
        self.err.setText("")
        self.busy.show()
        w = TaskWorker(load, parent=self)
        w.done.connect(self._finished)
        w.failed.connect(self._failed)
        self.worker = w
        w.start()

    def cancel_work(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)
        self.busy.hide()

    def _failed(self, exc):
        self.busy.hide()
        if not isinstance(exc, engine.Cancelled):
            self.err.setText(str(exc))
            toast(str(exc), "error")

    def _finished(self, nodes):
        self.busy.hide()
        self.nodes = nodes
        self.tree.clear()
        self.items = {}
        self._fill(self.tree.invisibleRootItem(), nodes)
        self.tree.expandAll() if self.b_expand.isChecked() else self.tree.collapseAll()
        self.apply_filter()
        c = regcmp.count_values(nodes)
        for k, lab in self.legend.items():
            lab.setText(f"{STATUS_TEXT[k]} values · {c.get(k, 0):,}")
        diffs = c.get("changed", 0) + c.get("left", 0) + c.get("right", 0)
        self.resultChanged.emit(f"{diffs} diffs" if diffs else "identical")

    def _fill(self, parent, nodes):
        mono = theme.mono(9)
        folder = theme.icon("folder", "#d9a441", 18)
        for n in nodes:
            it = QTreeWidgetItem(parent)
            it.setData(0, Qt.UserRole, n)
            self.items[id(n)] = it
            it.setIcon(3, theme.status_icon(n.status))
            for side, base in (("left", 0), ("right", 4)):
                info = getattr(n, side)
                if info is None:
                    for c in range(base, base + 3):
                        it.setData(c, Qt.UserRole + 1, True)
                    continue
                name = n.name if n.is_dir else ("(Default)" if n.name == "@" else n.name)
                it.setText(base, name)
                col = {"changed": theme.RED, "left": theme.BLUE, "right": theme.PURPLE}.get(n.status, theme.TEXT)
                it.setForeground(base, QColor(col))
                if n.is_dir:
                    it.setIcon(base, folder)
                    f = it.font(base)
                    f.setBold(True)
                    it.setFont(base, f)
                else:
                    it.setText(base + 1, info.get("type", ""))
                    it.setText(base + 2, info.get("data", ""))
                    it.setFont(base + 1, mono)
                    it.setFont(base + 2, mono)
                    it.setToolTip(base + 2, info.get("data", ""))
            if n.children:
                self._fill(it, n.children)

    def apply_filter(self):
        term = self.search.text().strip().lower()

        def mode_ok(n):
            return (self.mode == 0 or (self.mode == 1 and n.status != "same") or (self.mode == 2 and n.status == "same")
                    or (self.mode == 3 and n.status in ("left", "right")))

        def text_ok(n):
            if not term:
                return True
            hay = " ".join([n.name] + [str((getattr(n, s) or {}).get("data", "")) for s in ("left", "right")]).lower()
            return term in hay

        def visit(nodes):
            any_vis = False
            for n in nodes:
                it = self.items.get(id(n))
                if n.is_dir:
                    child = visit(n.children)
                    vis = child or (not n.children and mode_ok(n) and text_ok(n)) or (self.mode == 0 and not term)
                    if not child and term and not text_ok(n):
                        vis = False
                else:
                    vis = mode_ok(n) and text_ok(n)
                if it is not None:
                    it.setHidden(not vis)
                any_vis |= vis
            return any_vis
        visit(self.nodes)

    def _menu(self, pos):
        it = self.tree.itemAt(pos)
        if it is None:
            return
        n = it.data(0, Qt.UserRole)
        m = QMenu(self)
        path = (n.left or n.right or {}).get("path", "")
        val = (n.right if n.right and n.right.get("data") else n.left or {}) or {}
        if path:
            m.addAction("Copy key path", lambda: QGuiApplication.clipboard().setText(path))
        if val.get("data"):
            m.addAction("Copy value", lambda: QGuiApplication.clipboard().setText(val["data"]))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    def has_unsaved(self):
        return False

    def reload(self):
        self.compare()

    def swap_sides(self):
        self.left, self.right = self.right, self.left
        self.lh.setText(self.left)
        self.rh.setText(self.right)
        self.compare()
