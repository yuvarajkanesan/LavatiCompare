"""Folder comparison view: two-pane tree, filters, comparison methods, file operations, archives, remote."""
import os
import time

from PySide6.QtCore import QDateTime, QThread, QTimer, Qt, Signal
from PySide6.QtGui import QActionGroup, QBrush, QColor, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QDateTimeEdit, QDialog, QDialogButtonBox,
                               QFileDialog, QFrame, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QMenu,
                               QMessageBox, QPlainTextEdit, QProgressBar, QProgressDialog, QPushButton,
                               QStyledItemDelegate, QToolButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from . import connect_dialog, engine, fsops, gitvfs, platform_utils, remote, reports, theme, vfs
from .textdialogs import RulesDialog
from .widgets import ToolbarAdapter, Segmented, sty, toast, tool_button, vsep
from .workers import TaskWorker

STATUS_TEXT = {"changed": "Different", "newer": "Left is newer", "older": "Left is older", "left": "Left only",
               "right": "Right only", "same": "Identical"}
NAME_COLOR = {"changed": theme.RED, "newer": theme.AMBER, "older": theme.TEAL, "same": theme.TEXT}
ALL = set(engine.STATUSES)
FILTERS = {
    "all": ALL, "diff": ALL - {"same"}, "same": {"same"}, "orphans": {"left", "right"},
    "newer": {"newer"}, "older": {"older"}, "left": {"left"}, "right": {"right"},
    "changed": {"changed", "newer", "older"},
}
SEG_KEYS = ["all", "diff", "same", "orphans"]
METHODS = [("size_time", "Size and timestamp", "Fast: files are equal when size and modified time match"),
           ("crc", "CRC32 checksum", "Compares a checksum of the content (ignores timestamps)"),
           ("binary", "Byte by byte", "Compares every byte (most thorough)"),
           ("rules", "Text content with rules", "Compares text using the whitespace / case / unimportant-text rules")]

_ICONS = {}
IMG_EXT = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico", ".svg"}
TBL_EXT = {".csv", ".tsv", ".xlsx", ".xls", ".xlsm"}
BIN_EXT = {".exe", ".dll", ".apk", ".so", ".bin", ".7z", ".rar", ".class", ".aar", ".sys", ".ocx"}
ARC_EXT = {".zip", ".jar", ".war", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".whl"}


def file_icon(n):
    ext = os.path.splitext(n.name)[1].lower()
    if n.archive or ext in ARC_EXT:
        key = ("archive", "#d9a441")
    elif n.is_dir:
        key = ("folder", "#d9a441")
    elif ext in IMG_EXT:
        key = ("image", "#b3263b")
    elif ext in TBL_EXT:
        key = ("table", theme.GREEN)
    elif ext in BIN_EXT or ext == ".pdf":
        key = ("hex", theme.BLUE)
    else:
        key = ("doc", theme.MUTED)
    if key not in _ICONS:
        _ICONS[key] = theme.icon(key[0], key[1], 18)
    return _ICONS[key]


class FItem(QTreeWidgetItem):
    """Tree row that sorts by real values (size/time), folders always first."""
    STATUS_ORDER = {"changed": 0, "newer": 1, "older": 2, "left": 3, "right": 4, "same": 5}

    def _key(self, col):
        n = self.data(0, Qt.UserRole)
        if col in (0, 4):
            return n.name.lower()
        if col == 3:
            return self.STATUS_ORDER[n.status]
        info = n.left if col in (1, 2) else n.right
        if not info:
            return -1
        return info["size"] if col in (1, 5) else info["mtime"]

    def __lt__(self, other):
        tw = self.treeWidget()
        col = tw.sortColumn() if tw else 0
        a, b = self.data(0, Qt.UserRole), other.data(0, Qt.UserRole)
        asc = tw is None or tw.header().sortIndicatorOrder() == Qt.AscendingOrder
        if a.is_dir != b.is_dir:
            return (a.is_dir and not b.is_dir) if asc else (b.is_dir and not a.is_dir)
        return self._key(col) < other._key(col)


class CompareWorker(QThread):
    done = Signal(object, object, object)
    failed = Signal(str)
    need = Signal(object)

    def __init__(self, lspec, rspec, opts, ctx, cache, renames=False):
        super().__init__()
        self.lspec, self.rspec, self.opts, self.ctx, self.cache = lspec, rspec, opts, ctx, cache
        self.renames = renames

    def _open(self, spec):
        spec = (spec or "").strip().strip('"')
        if not spec:
            return None, None
        if spec in self.cache:
            return self.cache[spec]
        if not vfs.is_remote_spec(spec) and not gitvfs.is_git_spec(spec) and not os.path.exists(spec):
            raise FileNotFoundError(f"Not found: {spec}")
        res = vfs.open_location(spec)
        self.cache[spec] = res
        return res

    def run(self):
        try:
            lfs, lroot = self._open(self.lspec)
            rfs, rroot = self._open(self.rspec)
            for fs in (lfs, rfs):
                if isinstance(fs, remote.RemoteFS):
                    fs.invalidate()
            counter = {}
            nodes = engine.compare_tree_parallel(lfs, lroot, rfs, rroot, self.opts, counter=counter, ctx=self.ctx)
            if self.renames and gitvfs.is_git_spec(self.lspec) and gitvfs.is_git_spec(self.rspec):
                lrepo, lrev = gitvfs.parse_spec(self.lspec)
                rrepo, rrev = gitvfs.parse_spec(self.rspec)
                if os.path.normcase(lrepo) == os.path.normcase(rrepo):
                    top = gitvfs.find_repo(lrepo) or lrepo
                    if gitvfs.apply_renames(nodes, gitvfs.detect_renames_specs(top, lrev, rrev)):
                        counter.clear()
                        counter.update(engine.count_statuses(nodes))
            engine.compress(nodes)
            self.done.emit(nodes, counter, fsops.Sides(lfs, lroot, rfs, rroot))
        except engine.Cancelled:
            pass
        except (remote.NeedCredentials, remote.UnknownHost) as e:
            self.need.emit(e)
        except Exception as e:  # report instead of dying silently in the thread
            self.failed.emit(f"{type(e).__name__}: {e}")


def fmt_time(t):
    return time.strftime("%m/%d %H:%M", time.localtime(t)) if t else ""


def fmt_size(n):
    if n is None:
        return "—"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


class HatchDelegate(QStyledItemDelegate):
    """Paints diagonal hatching on cells flagged as 'missing on this side'."""

    def paint(self, p, opt, idx):
        if idx.data(Qt.UserRole + 1):
            p.fillRect(opt.rect, QColor(theme.SURFACE3))
            p.fillRect(opt.rect, QBrush(QColor(theme.HATCH), Qt.BDiagPattern))
            p.setPen(QColor(theme.LINE_SOFT))
            p.drawLine(opt.rect.bottomLeft(), opt.rect.bottomRight())
            return
        super().paint(p, opt, idx)


class FiltersDialog(QDialog):
    def __init__(self, exclude, include, parent=None):
        super().__init__(parent)
        self.setWindowTitle("File filters")
        self.setMinimumWidth(520)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        for title, sub, attr, val in (
                ("Exclude", "Names to ignore, one pattern per line (wildcards allowed; a trailing / matches folders only).", "ex", exclude),
                ("Include only", "If filled in, only files matching one of these patterns are compared (e.g. *.kt).", "inc", include)):
            t = QLabel(title)
            t.setStyleSheet("font-weight:700;")
            lay.addWidget(t)
            s = QLabel(sub)
            s.setWordWrap(True)
            s.setStyleSheet(f"color:{theme.MUTED}; font-size:9pt;")
            lay.addWidget(s)
            e = QPlainTextEdit("\n".join(val))
            e.setFont(theme.mono(10))
            e.setFixedHeight(110)
            setattr(self, attr, e)
            lay.addWidget(e)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        lay.addWidget(bb)

    def values(self):
        f = lambda e: [l.strip() for l in e.toPlainText().splitlines() if l.strip()]
        return f(self.ex), f(self.inc)


class ReportDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Folder report")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        self.fmt = QComboBox()
        self.fmt.addItem("HTML", "html")
        self.fmt.addItem("Plain text", "text")
        self.fmt.addItem("XML", "xml")
        self.show_ = QComboBox()
        for k, label in (("differences", "Differences only"), ("all", "Everything"), ("orphans", "Orphans (one side only)"),
                         ("same", "Identical files"), ("newer", "Left newer"), ("older", "Left older")):
            self.show_.addItem(label, k)
        lay.addWidget(QLabel("Format"))
        lay.addWidget(self.fmt)
        lay.addWidget(QLabel("Include"))
        lay.addWidget(self.show_)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        lay.addWidget(bb)


class FolderCompareView(QWidget):
    openFile = Signal(str, str)       # left file, right file (local paths)
    openAs = Signal(str, str, str)    # kind, left, right
    resultChanged = Signal(str)
    optionsChanged = Signal()
    loaded = Signal()

    def __init__(self, cfg, left, right, parent=None, extra=None):
        super().__init__(parent)
        self.cfg = cfg
        self.left, self.right = left, right
        self.opts = engine.CompareOptions.from_config(cfg)
        extra = extra or {}
        if extra.get("options"):
            self.opts.apply_dict(extra["options"])
        self.nodes = []
        self.view_nodes = []
        self.items = {}
        self.S = None
        self.counter = {}
        self.compare_time = time.time()
        self.filter_key = extra.get("filter", "diff")
        self.flat = bool(extra.get("flat", False))
        self.worker = None
        self.task = None
        self.ctx = None
        self._open_cache = {}
        self._mono = theme.mono(9)
        self._retries = 0
        self._pending_sync = None
        self.generation = 0              # increments after every finished comparison
        self.git_renames = bool(extra.get("git_renames", True))
        self._ui()
        self.refresh()

    # --------------------------------------------------------------- UI
    def _ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QFrame()
        sty(bar, f"background:{theme.SURFACE}; border-bottom:1px solid {theme.BORDER};")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 6, 12, 6)
        bl.setSpacing(3)
        self.seg = Segmented(["All", "Differences", "Same", "Orphans"], current=SEG_KEYS.index(self.filter_key) if self.filter_key in SEG_KEYS else 1)
        if self.filter_key not in SEG_KEYS:
            self.seg.clear()
        self.seg.changed.connect(lambda i: self._set_filter(SEG_KEYS[i]))
        bl.addWidget(self.seg)
        self.b_show = tool_button("Show", "filter", "More ways to filter by status")
        self.b_show.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(self.b_show)
        for key, label in (("changed", "Different files (any)"), ("newer", "Left newer"), ("older", "Left older"),
                           ("left", "Left only"), ("right", "Right only"), ("same", "Identical"), ("all", "Everything")):
            m.addAction(label, lambda key=key: self._set_filter(key, True))
        self.b_show.setMenu(m)
        bl.addWidget(self.b_show)
        bl.addWidget(vsep())
        self.b_expand = tool_button("Expand", "expand", "Expand / collapse all folders", checkable=True)
        self.b_expand.setChecked(True)
        self.b_expand.toggled.connect(lambda on: self.tree.expandAll() if on else self.tree.collapseAll())
        self.b_flat = tool_button("Flatten", "layers", "Show all files in one list", checkable=True)
        self.b_flat.setChecked(self.flat)
        self.b_flat.toggled.connect(self._toggle_flat)
        bl.addWidget(self.b_expand)
        bl.addWidget(self.b_flat)
        self.b_select = tool_button("Select", "select", "Select by status")
        self.b_select.setPopupMode(QToolButton.InstantPopup)
        sm = QMenu(self.b_select)
        for label, st in (("All differences", "diff"), ("Left only", "left"), ("Right only", "right"), ("Orphans", "orphans"),
                          ("Left newer", "newer"), ("Left older", "older"), ("Different (any)", "changed"), ("Identical", "same")):
            sm.addAction(label, lambda st=st: self.select_status(st))
        sm.addSeparator()
        sm.addAction("Select all", lambda: self.tree.selectAll())
        sm.addAction("Invert selection", self.invert_selection)
        sm.addAction("Clear selection", lambda: self.tree.clearSelection())
        self.b_select.setMenu(sm)
        bl.addWidget(self.b_select)
        bl.addWidget(vsep())
        self.b_method = tool_button("Compare by", "tool", "How files are decided to be equal")
        self.b_method.setPopupMode(QToolButton.InstantPopup)
        self.b_method.setMenu(self._method_menu())
        b_filters = tool_button("Filters", "rules", "Include / exclude patterns")
        b_filters.clicked.connect(self.edit_filters)
        bl.addWidget(self.b_method)
        bl.addWidget(b_filters)
        bl.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("*.kt; *.xml; -build/")
        self.search.setProperty("mono", True)
        self.search.setFixedWidth(250)
        self.search.setToolTip("Quick filter on the results. Separate patterns with ;  Start a pattern with - to exclude it.")
        self.search.addAction(theme.icon("search", theme.MUTED, 16), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(self.apply_filter)
        bl.addWidget(self.search)
        ToolbarAdapter(bar)
        root.addWidget(bar)

        bar2 = QFrame()
        sty(bar2, f"background:{theme.SURFACE}; border-bottom:1px solid {theme.BORDER};")
        b2 = QHBoxLayout(bar2)
        b2.setContentsMargins(12, 4, 12, 4)
        b2.setSpacing(3)
        b_cr = tool_button("To right", "right", "Copy the selected items from left to right (Ctrl+Right)")
        b_cl = tool_button("To left", "left", "Copy the selected items from right to left (Ctrl+Left)")
        b_cr.clicked.connect(lambda: self.copy_selected("R"))
        b_cl.clicked.connect(lambda: self.copy_selected("L"))
        self.b_actions = tool_button("File actions", "more", "Move, delete, rename, touch, new folder ...")
        self.b_actions.setPopupMode(QToolButton.InstantPopup)
        self.b_actions.setMenu(self._actions_menu())
        b_sync = tool_button("Sync", "refresh", "Synchronize folders (Ctrl+Shift+Y)")
        b_sync.clicked.connect(lambda: self.sync())
        for b in (b_cr, b_cl, self.b_actions, b_sync):
            b2.addWidget(b)
        b2.addWidget(vsep())
        self.b_snap = tool_button("Snapshot", "camera", "Save a folder's file list and compare it later")
        self.b_snap.setPopupMode(QToolButton.InstantPopup)
        sn = QMenu(self.b_snap)
        sn.addAction("Save snapshot of the left side…", lambda: self.save_snapshot("L"))
        sn.addAction("Save snapshot of the right side…", lambda: self.save_snapshot("R"))
        sn.addSeparator()
        sn.addAction("Use a snapshot as the left side…", lambda: self.load_snapshot("L"))
        sn.addAction("Use a snapshot as the right side…", lambda: self.load_snapshot("R"))
        self.b_snap.setMenu(sn)
        b2.addWidget(self.b_snap)
        b_ref = tool_button("Refresh", "refresh", "Re-compare (F5)")
        b_rep = tool_button("Report", "report", "Save a difference report (HTML, text or XML)")
        b_ref.clicked.connect(self.refresh)
        b_rep.clicked.connect(self.report)
        b2.addWidget(b_ref)
        b2.addWidget(b_rep)
        b2.addStretch(1)
        self.how = QLabel()
        self.how.setStyleSheet(f"color:{theme.MUTED}; font-size:9pt;")
        b2.addWidget(self.how)
        ToolbarAdapter(bar2)
        root.addWidget(bar2)

        paths = QFrame()
        sty(paths, f"background:{theme.PANEL}; border-bottom:1px solid {theme.BORDER};")
        pl = QHBoxLayout(paths)
        pl.setContentsMargins(16, 8, 16, 8)
        self.le_left = self._path_edit(self.left)
        self.le_right = self._path_edit(self.right)
        for lab, le, side in (("LEFT", self.le_left, "L"), ("RIGHT", self.le_right, "R")):
            l = QLabel(lab)
            l.setStyleSheet(f"color:{theme.MUTED}; font-weight:700; font-size:8pt; letter-spacing:1px;")
            pl.addWidget(l)
            pl.addWidget(le, 1)
            b = QPushButton()
            b.setIcon(theme.icon("folder", theme.TEXT, 18))
            b.setProperty("plain", True)
            b.setFixedWidth(44)
            b.setToolTip("Browse, open an archive or snapshot, or connect to a server")
            b.clicked.connect(lambda _=False, s=side, w=b: self.browse(s, w))
            pl.addWidget(b)
            if side == "L":
                pl.addSpacing(18)
        root.addWidget(paths)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(7)
        self.tree.setHeaderLabels(["Name", "Size", "Modified", "", "Name", "Size", "Modified"])
        self.tree.setRootIsDecorated(True)
        self.tree.setIndentation(24)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setStyleSheet(
            f"QTreeWidget::item {{ height:34px; border-bottom:1px solid {theme.LINE_SOFT}; }}"
            f"QTreeWidget::item:selected {{ background:{theme.ACCENT_SOFT}; color:{theme.TEXT}; }}")
        h = self.tree.header()
        h.setStretchLastSection(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(4, QHeaderView.Stretch)
        for c, w in ((1, 90), (2, 120), (3, 62), (5, 90), (6, 120)):
            h.setSectionResizeMode(c, QHeaderView.Fixed)
            self.tree.setColumnWidth(c, w)
        h.setSectionsMovable(False)
        for c in (1, 5):
            self.tree.headerItem().setTextAlignment(c, Qt.AlignRight | Qt.AlignVCenter)
        self.tree.headerItem().setTextAlignment(3, Qt.AlignCenter)
        self.tree.setItemDelegate(HatchDelegate(self.tree))
        self.tree.itemDoubleClicked.connect(self._dbl)
        self.tree.itemSelectionChanged.connect(self._sel)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._context_menu)
        self.tree.setSortingEnabled(True)
        self.tree.header().setSortIndicatorShown(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        root.addWidget(self.tree, 1)

        self.busy = QFrame()
        sty(self.busy, f"background:{theme.SURFACE}; border-top:1px solid {theme.BORDER};")
        bz = QHBoxLayout(self.busy)
        bz.setContentsMargins(18, 8, 18, 8)
        self.busy_lbl = QLabel("Comparing…")
        self.busy_lbl.setStyleSheet("font-weight:600;")
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setFixedHeight(8)
        self.busy_bar.setTextVisible(False)
        cancel = QPushButton("Cancel")
        cancel.setProperty("plain", True)
        cancel.clicked.connect(self.cancel_work)
        bz.addWidget(self.busy_lbl)
        bz.addWidget(self.busy_bar, 1)
        bz.addWidget(cancel)
        self.busy.hide()
        root.addWidget(self.busy)

        foot = QFrame()
        sty(foot, f"background:{theme.SURFACE}; border-top:1px solid {theme.BORDER};")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(18, 7, 18, 7)
        fl.setSpacing(16)
        self.legend = {}
        for key in ("changed", "newer", "older", "left", "right", "same"):
            sq = QLabel()
            sq.setPixmap(theme.status_icon(key, 14).pixmap(14, 14))
            lab = QLabel()
            lab.setStyleSheet(f"color:{theme.MUTED}; font-size:9pt;")
            row = QHBoxLayout()
            row.setSpacing(5)
            row.addWidget(sq)
            row.addWidget(lab)
            fl.addLayout(row)
            self.legend[key] = (lab, STATUS_TEXT[key])
        fl.addStretch(1)
        self.selected_lbl = QLabel("")
        self.selected_lbl.setStyleSheet("font-weight:700; font-size:9pt;")
        fl.addWidget(self.selected_lbl)
        root.addWidget(foot)
        self.poll = QTimer(self)
        self.poll.timeout.connect(self._poll)
        for seq, fn in (("Ctrl+Right", lambda: self.copy_selected("R")), ("Ctrl+Left", lambda: self.copy_selected("L")),
                        ("F2", self.rename_selected), ("Delete", lambda: self.delete_selected("ask")),
                        ("Return", self._open_current)):
            sc = QShortcut(QKeySequence(seq), self.tree)
            sc.setContext(Qt.WidgetShortcut)
            sc.activated.connect(fn)
        self._update_how()

    def _path_edit(self, text):
        le = QLineEdit(text)
        le.setProperty("mono", True)
        le.setFixedHeight(34)
        le.returnPressed.connect(self._paths_edited)
        return le

    def _paths_edited(self):
        # passwords typed into a URL are kept in memory only and never shown again or saved with the session
        self.left = remote.stash_url_secrets(self.le_left.text().strip().strip('"'))
        self.right = remote.stash_url_secrets(self.le_right.text().strip().strip('"'))
        self.le_left.setText(self.left)
        self.le_right.setText(self.right)
        self.refresh()

    def browse(self, side, anchor):
        m = QMenu(self)
        m.addAction("Folder…", lambda: self._browse_folder(side))
        m.addAction("Archive or snapshot file…", lambda: self._browse_file(side))
        m.addAction("Connect to a server…", lambda: self._connect(side))
        m.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def _set_path(self, side, text):
        (self.le_left if side == "L" else self.le_right).setText(text)
        self._paths_edited()

    def _browse_folder(self, side):
        cur = (self.le_left if side == "L" else self.le_right).text()
        d = QFileDialog.getExistingDirectory(self, "Select folder", cur if os.path.isdir(cur) else "")
        if d:
            self._set_path(side, os.path.normpath(d))

    def _browse_file(self, side):
        p, _ = QFileDialog.getOpenFileName(self, "Open archive or snapshot", "",
                                           "Archives and snapshots (*.zip *.jar *.war *.apk *.aar *.tar *.tgz *.gz *.bz2 *.xz *.whl *.lsnap);;All files (*)")
        if p:
            self._set_path(side, os.path.normpath(p))

    def _connect(self, side):
        dlg = connect_dialog.ConnectDialog(self, "left" if side == "L" else "right")
        if dlg.exec() and dlg.url:
            self._set_path(side, dlg.url)

    # ------------------------------------------------------------ options
    def _method_menu(self):
        m = QMenu(self)
        grp = QActionGroup(m)
        self._method_actions = {}
        for key, label, tip in METHODS:
            a = m.addAction(label)
            a.setCheckable(True)
            a.setChecked(self.opts.method == key)
            a.setStatusTip(tip)
            a.setToolTip(tip)
            grp.addAction(a)
            a.triggered.connect(lambda _=False, key=key: self.set_method(key))
            self._method_actions[key] = a
        m.addSeparator()
        m.addAction("Rules for text comparison…", self.edit_rules)
        m.addAction("Timestamp tolerance…", self.edit_tolerance)
        m.addSeparator()
        self.act_case = m.addAction("Match names ignoring case")
        self.act_case.setCheckable(True)
        self.act_case.setChecked(self.opts.ignore_case_names)
        self.act_case.toggled.connect(lambda on: self._set_opt("ignore_case_names", on))
        self.act_arch = m.addAction("Compare inside archives (zip, tar, gz, jar …)")
        self.act_arch.setCheckable(True)
        self.act_arch.setChecked(self.opts.archives)
        self.act_arch.toggled.connect(lambda on: self._set_opt("archives", on))
        m.addSeparator()
        self.act_ren = m.addAction("Detect renamed files when comparing Git revisions")
        self.act_ren.setCheckable(True)
        self.act_ren.setChecked(self.git_renames)
        self.act_ren.toggled.connect(self._set_renames)
        m.addAction("Clear manual alignments", self.clear_aligns)
        return m

    def _set_renames(self, on):
        self.git_renames = on
        self._options_changed()

    def _set_opt(self, name, value):
        setattr(self.opts, name, value)
        key = {"ignore_case_names": "ignore_case_names", "archives": "archives"}.get(name)
        if key:
            self.cfg[key] = value
        self._options_changed()

    def _options_changed(self):
        self._update_how()
        self.optionsChanged.emit()
        self.refresh()

    def set_method(self, key):
        self.opts.method = key
        self.cfg["compare_method"] = key
        self._options_changed()

    def edit_tolerance(self):
        v, ok = QInputDialog.getDouble(self, "Timestamp tolerance",
                                       "Files whose modified times differ by no more than this many seconds count as equal:",
                                       self.opts.time_tol, 0, 86400, 1)
        if ok:
            self.opts.time_tol = v
            self.cfg["time_tolerance"] = v
            self._options_changed()

    def edit_rules(self):
        dlg = RulesDialog(self.opts.rules or engine.TextRules(), self, "Rules for text comparison")
        if dlg.exec():
            self.opts.rules = dlg.rules()
            if self.opts.method != "rules":
                self.opts.method = "rules"
                self._method_actions["rules"].setChecked(True)
            self._options_changed()

    def edit_filters(self):
        dlg = FiltersDialog(self.opts.patterns, self.opts.include, self)
        if dlg.exec():
            self.opts.patterns, self.opts.include = dlg.values()
            self.cfg["ignore_patterns"] = list(self.opts.patterns)
            self._options_changed()

    def clear_aligns(self):
        if self.opts.aligns:
            self.opts.aligns = []
            self._options_changed()

    def _update_how(self):
        label = dict((k, l) for k, l, _ in METHODS)[self.opts.method]
        bits = [label]
        if self.opts.archives:
            bits.append("archives opened")
        if self.opts.include:
            bits.append("include " + ", ".join(self.opts.include[:3]))
        if self.opts.aligns:
            bits.append(f"{len(self.opts.aligns)} alignment(s)")
        self.how.setText("Compared by: " + " · ".join(bits))

    def session_extra(self):
        return dict(options=self.opts.to_dict(), filter=self.filter_key, flat=self.flat, git_renames=self.git_renames)

    # -------------------------------------------------------- populate
    def refresh(self):
        self.cancel_work()
        if self.left == "" and self.right == "":
            self.busy.hide()
            return
        self.ctx = engine.Ctx()
        self.worker = CompareWorker(self.left, self.right, self.opts, self.ctx, self._open_cache, self.git_renames)
        self.worker.done.connect(self._finished)
        self.worker.failed.connect(self._failed)
        self.worker.need.connect(self._need_input)
        self.busy_lbl.setText("Comparing…")
        self.busy.show()
        self.poll.start(120)
        self.worker.start()

    def cancel_work(self):
        if self.worker is not None and self.worker.isRunning():
            self.ctx.cancel = True
            self.worker.wait(5000)
        self.poll.stop()
        self.busy.hide()

    def _poll(self):
        if self.ctx:
            self.busy_lbl.setText(f"Comparing…  {self.ctx.count:,} items")

    def _failed(self, msg):
        self.poll.stop()
        self.busy.hide()
        toast(msg, "error")

    def _need_input(self, exc):
        self.poll.stop()
        self.busy.hide()
        self._retries += 1
        if self._retries <= 4 and connect_dialog.prompt_for(self, exc):
            self.refresh()
        else:
            self._retries = 0
            toast(str(exc), "error")

    def _finished(self, nodes, counter, S):
        self.poll.stop()
        self.busy.hide()
        self._retries = 0
        self.nodes, self.S = nodes, S
        self.generation += 1
        self.compare_time = time.time()
        self.counter = counter
        self._populate()
        diffs = sum(counter.get(k, 0) for k in engine.DIFF_STATUSES)
        self.resultChanged.emit(f"{diffs} changed" if diffs else "identical")
        self.loaded.emit()
        if self._pending_sync is not None:
            preset, self._pending_sync = self._pending_sync, None
            QTimer.singleShot(0, lambda: self.sync(preset or None))

    def sync_when_ready(self, preset):
        """Open the synchronise dialog as soon as the comparison has finished (used by saved sync sessions)."""
        if self.S is not None and not self.worker.isRunning():
            self.sync(preset)
        else:
            self._pending_sync = preset or {}

    def _populate(self):
        """Rebuild the tree with only the rows that pass the filters (hiding thousands of rows one by one is far slower)."""
        base = engine.flatten(self.nodes) if self.flat else self.nodes
        self.view_nodes = self._visible(base)
        self.tree.setUpdatesEnabled(False)
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        self.items = {}
        self.tree.setRootIsDecorated(not self.flat)
        self._fill(self.tree.invisibleRootItem(), self.view_nodes)
        self.tree.setSortingEnabled(True)
        self.tree.expandAll() if self.b_expand.isChecked() else self.tree.collapseAll()
        self.tree.setUpdatesEnabled(True)
        for k, (lab, text) in self.legend.items():
            lab.setText(f"{text} \u00b7 {self.counter.get(k, 0):,}")

    def _visible(self, nodes):
        """Copies of the nodes that pass the status filter and the quick-search box (folders only if they keep content)."""
        searching = bool(self.search.text().strip())
        show_all = self.filter_key == "all" and not searching
        out = []
        for n in nodes:
            if n.is_dir and n.children:
                kids = self._visible(n.children)
                if kids or show_all:
                    out.append(n if (len(kids) == len(n.children) and all(a is b for a, b in zip(kids, n.children))) else self._clone(n, kids))
            elif self._match_mode(n) and self._match_search(n):
                out.append(n)
        return out

    @staticmethod
    def _clone(n, kids):
        return engine.Node(**{**n.__dict__, "children": kids})

    def _fill(self, parent, nodes):
        mono = self._mono
        for n in nodes:
            it = FItem(parent)
            it.setData(0, Qt.UserRole, n)
            self.items[n.rel] = it
            it.setIcon(3, theme.status_icon(n.status))
            tip = STATUS_TEXT[n.status] + ("  (decided from size / time only)" if n.approx else "") \
                + (f"  (renamed from {n.renamed_from})" if n.renamed_from else "")
            it.setToolTip(3, tip)
            for side, base in (("left", 0), ("right", 4)):
                info = getattr(n, side)
                if info is None:
                    for c in range(base, base + 3):
                        it.setData(c, Qt.UserRole + 1, True)
                    continue
                it.setIcon(base, file_icon(n))
                it.setText(base, self._display_name(n, side))
                it.setText(base + 1, "—" if (n.is_dir and not n.archive) else fmt_size(info["size"]))
                it.setText(base + 2, "   " + fmt_time(info["mtime"]))
                it.setTextAlignment(base + 1, Qt.AlignRight | Qt.AlignVCenter)
                col = NAME_COLOR.get(n.status)
                if col is None:
                    col = theme.BLUE if n.status == "left" else theme.PURPLE
                it.setForeground(base, QColor(col))
                if n.is_dir:
                    fnt = it.font(base)
                    fnt.setBold(True)
                    it.setFont(base, fnt)
                for c in (base + 1, base + 2):
                    it.setForeground(c, QColor(theme.MUTED if n.status == "same" else theme.TEXT))
                    it.setFont(c, mono)
            if n.children:
                self._fill(it, n.children)

    def _display_name(self, n, side):
        name = n.name if side == "left" else n.right_name()
        if self.flat:
            parent = n.rel.rpartition("/")[0]
            return (parent + "/" + name) if parent else name
        return name

    def _toggle_flat(self, on):
        self.flat = on
        self.optionsChanged.emit()
        if self.nodes:
            self._populate()

    # --------------------------------------------------------- filters
    def _set_filter(self, key, from_menu=False):
        self.filter_key = key
        if key in SEG_KEYS and not from_menu:
            pass
        elif key in SEG_KEYS:
            self.seg.set_current(SEG_KEYS.index(key))
        else:
            self.seg.clear()
        self.apply_filter()
        self.optionsChanged.emit()

    def _match_mode(self, n):
        return n.status in FILTERS.get(self.filter_key, ALL)

    def _match_search(self, n):
        import fnmatch
        pats = [p.strip() for p in self.search.text().split(";") if p.strip()]
        if not pats:
            return True
        pos = [p for p in pats if not p.startswith("-")]
        neg = [p[1:].strip() for p in pats if p.startswith("-") and len(p) > 1]
        parts = n.rel.lower().split("/")
        for p in neg:
            d = p.endswith("/")
            q = p.rstrip("/").lower()
            comps = (parts if n.is_dir else parts[:-1]) if d else parts[-1:]
            if any(fnmatch.fnmatch(c, q) for c in comps):
                return False
        if pos:
            return any(fnmatch.fnmatch(parts[-1], p.lower()) or fnmatch.fnmatch(n.rel.lower(), p.lower()) for p in pos)
        return True

    def apply_filter(self):
        if not self.nodes:
            return
        if not hasattr(self, "_filter_timer"):
            self._filter_timer = QTimer(self)
            self._filter_timer.setSingleShot(True)
            self._filter_timer.setInterval(180)
            self._filter_timer.timeout.connect(self._populate)
        self._filter_timer.start()                     # debounced: typing in the search box rebuilds once

    # ---------------------------------------------------------- selection
    def _sel_nodes(self):
        return [i.data(0, Qt.UserRole) for i in self.tree.selectedItems()]

    def _sel_pruned(self):
        """Selected nodes, minus those that live inside another selected folder."""
        nodes = self._sel_nodes()
        dirs = [n.rel + "/" for n in nodes if n.is_dir and not n.archive]
        return [n for n in nodes if not any(n.rel.startswith(d) for d in dirs)]

    def _sel(self):
        s = self._sel_nodes()
        self.selected_lbl.setText(f"Selected: {s[0].name}" if len(s) == 1 else (f"Selected: {len(s)} items" if s else ""))

    def select_status(self, key):
        want = FILTERS.get(key, ALL)
        self.tree.clearSelection()
        for rel, it in self.items.items():
            n = it.data(0, Qt.UserRole)
            if not n.is_dir and n.status in want:
                it.setSelected(True)

    def invert_selection(self):
        for it in self.items.values():
            if not it.data(0, Qt.UserRole).is_dir:
                it.setSelected(not it.isSelected())

    # ----------------------------------------------------------- opening
    def _paths(self, n):
        return (n.left["path"] if n.left else ""), (n.right["path"] if n.right else "")

    def _local(self, n, side):
        """Local file for a node side (downloads / extracts when needed). May raise."""
        info = fsops.info(n, side)
        if not info:
            return ""
        return fsops.node_fs(self.S, n, side).local_path(info["path"])

    def _dbl(self, item, col):
        n = item.data(0, Qt.UserRole)
        self._open_node(n)

    def _open_current(self):
        s = self._sel_nodes()
        if s:
            self._open_node(s[0])

    def _open_node(self, n, kind=None):
        if n.is_dir and not n.archive:
            return
        if not (n.left and n.right):
            toast("This item exists on one side only", "warn")
            return
        try:
            QGuiApplication.setOverrideCursor(Qt.WaitCursor)
            lp, rp = self._local(n, "L"), self._local(n, "R")
        except (OSError, vfs.VFSError) as e:
            toast(str(e), "error")
            return
        finally:
            QGuiApplication.restoreOverrideCursor()
        if n.archive and not kind:
            self.openAs.emit("folder", lp, rp)
        elif kind:
            self.openAs.emit(kind, lp, rp)
        else:
            self.openFile.emit(lp, rp)

    # ------------------------------------------------------- actions menu
    def _actions_menu(self):
        m = QMenu(self)
        m.aboutToShow.connect(lambda: self._fill_actions(m))
        return m

    def _fill_actions(self, m):
        m.clear()
        self._add_file_actions(m, self._sel_nodes())

    def _add_file_actions(self, m, nodes):
        has_l = any(n.left for n in nodes)
        has_r = any(n.right for n in nodes)
        wl = self.S is not None and not self.S.lfs.readonly
        wr = self.S is not None and not self.S.rfs.readonly
        m.addAction("Copy left to right", lambda: self.copy_selected("R")).setEnabled(has_l and wr)
        m.addAction("Copy right to left", lambda: self.copy_selected("L")).setEnabled(has_r and wl)
        m.addAction("Move left to right", lambda: self.copy_selected("R", move=True)).setEnabled(has_l and wr and wl)
        m.addAction("Move right to left", lambda: self.copy_selected("L", move=True)).setEnabled(has_r and wl and wr)
        m.addSeparator()
        m.addAction("Delete left…", lambda: self.delete_selected("L")).setEnabled(has_l and wl)
        m.addAction("Delete right…", lambda: self.delete_selected("R")).setEnabled(has_r and wr)
        m.addAction("Delete both…", lambda: self.delete_selected("B")).setEnabled(has_l and has_r and wl and wr)
        m.addAction("Rename…  (F2)", self.rename_selected).setEnabled(bool(nodes) and (wl or wr))
        m.addAction("New folder…", self.new_folder).setEnabled(wl or wr)
        t = m.addMenu("Touch (modified time)")
        t.addAction("Set left to now", lambda: self.touch_selected("L", "now")).setEnabled(has_l and wl)
        t.addAction("Set right to now", lambda: self.touch_selected("R", "now")).setEnabled(has_r and wr)
        t.addAction("Copy timestamp left to right", lambda: self.touch_selected("R", "other")).setEnabled(has_l and has_r and wr)
        t.addAction("Copy timestamp right to left", lambda: self.touch_selected("L", "other")).setEnabled(has_l and has_r and wl)
        t.addAction("Set both to a date…", lambda: self.touch_selected("B", "value")).setEnabled(bool(nodes))

    def _context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if item is None:
            return
        if not item.isSelected():
            self.tree.clearSelection()
            item.setSelected(True)
        n = item.data(0, Qt.UserRole)
        nodes = self._sel_nodes()
        lp, rp = self._paths(n)
        m = QMenu(self)
        if n.archive and n.left and n.right:
            m.addAction("Open archives as folders", lambda: self._open_node(n))
            m.addSeparator()
        elif not n.is_dir and n.left and n.right:
            m.addAction("Open comparison", lambda: self._open_node(n))
            sub = m.addMenu("Compare as")
            for k, label in (("text", "Text"), ("hex", "Hex"), ("image", "Picture"), ("table", "Table"), ("version", "Version info"),
                             ("media", "Media tags"), ("doc", "Document text"), ("registry", "Registry (.reg)")):
                sub.addAction(label, lambda k=k: self._open_node(n, k))
            m.addSeparator()
        elif n.is_dir and n.left and n.right:
            m.addAction("Compare this folder", lambda: self.openAs.emit("folder", lp, rp))
            m.addSeparator()
        self._add_file_actions(m, nodes)
        m.addSeparator()
        sel_files = [x for x in nodes if not x.is_dir]
        if len(sel_files) == 2:
            a, b = sel_files
            lo = [x for x in sel_files if x.status == "left"]
            ro = [x for x in sel_files if x.status == "right"]
            if len(lo) == 1 and len(ro) == 1:
                m.addAction("Align these two files (compare as a pair)", lambda: self.align(lo[0], ro[0]))
        if n.rname and n.rname != n.name:
            m.addAction("Remove this alignment", lambda: self.unalign(n))
        m.addSeparator()
        local_l = isinstance(n.lfs, vfs.LocalFS)
        local_r = isinstance(n.rfs, vfs.LocalFS)
        m.addAction("Show left in file manager", lambda: platform_utils.reveal(lp)).setEnabled(bool(lp) and local_l)
        m.addAction("Show right in file manager", lambda: platform_utils.reveal(rp)).setEnabled(bool(rp) and local_r)
        m.addAction("Copy path", lambda: (QGuiApplication.clipboard().setText(rp or lp), toast("Path copied")))
        m.exec(self.tree.viewport().mapToGlobal(pos))

    # ------------------------------------------------------------ aligning
    def align(self, lnode, rnode):
        lp, rp = lnode.rel.rpartition("/")[0], rnode.rel.rpartition("/")[0]
        if lp != rp:
            QMessageBox.information(self, "Align", "Only files in the same folder can be paired.")
            return
        self.opts.aligns = [a for a in self.opts.aligns if a[0] != lnode.rel and a[1] != rnode.rel]
        self.opts.aligns.append([lnode.rel, rnode.rel])
        self._options_changed()

    def unalign(self, n):
        left_name = n.rel
        self.opts.aligns = [a for a in self.opts.aligns if a[0] != left_name]
        self._options_changed()

    # --------------------------------------------------------- background ops
    def _run_task(self, title, fn, summary):
        """Run fn(ctx) -> (count, errors) on a thread with a cancellable progress dialog."""
        if self.task is not None and self.task.isRunning():
            return
        ctx = engine.Ctx()
        dlg = QProgressDialog(title, "Cancel", 0, 0, self)
        dlg.setWindowTitle("Working")
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(300)
        w = TaskWorker(fn, ctx, self)
        self.task = w

        def finish(res):
            dlg.close()
            count, errors = res if isinstance(res, tuple) else (res, [])
            if errors:
                QMessageBox.warning(self, "Completed with errors", "\n".join(errors[:12]) + (f"\n… and {len(errors) - 12} more" if len(errors) > 12 else ""))
            else:
                toast(summary.format(n=count))
            self.refresh()

        def fail(exc):
            dlg.close()
            if not isinstance(exc, engine.Cancelled):
                QMessageBox.warning(self, "Operation failed", str(exc))
            self.refresh()
        w.done.connect(finish)
        w.failed.connect(fail)
        dlg.canceled.connect(w.cancel)
        w.start()

    def _need_S(self):
        if self.S is None:
            toast("Nothing is loaded yet", "warn")
            return False
        return True

    def copy_selected(self, direction, move=False):
        if not self._need_S():
            return
        nodes = self._sel_pruned()
        if not nodes:
            QMessageBox.information(self, "Copy", "Select one or more items first.")
            return
        src = "L" if direction == "R" else "R"
        todo = [n for n in nodes if fsops.info(n, src)]
        if not todo:
            QMessageBox.information(self, "Copy", "Nothing to copy from that side.")
            return
        over = [n for n in todo if fsops.info(n, direction) and not n.is_dir]
        arrow = "left to right" if direction == "R" else "right to left"
        msg = f"{'Move' if move else 'Copy'} {len(todo)} item(s) {arrow}?"
        if over:
            msg += f"\n{len(over)} existing file(s) will be overwritten" + (" (backups are kept)." if self.cfg["backup_overwritten"] else ".")
        if QMessageBox.question(self, "Move" if move else "Copy", msg) != QMessageBox.Yes:
            return
        S, backup = self.S, bool(self.cfg["backup_overwritten"])
        bdir = self.cfg["sync_backup_dir"]
        self._run_task("Moving…" if move else "Copying…",
                       lambda ctx: fsops.copy_nodes(todo, direction, S, ctx, backup, bdir, move),
                       "{n} file(s) " + ("moved" if move else "copied"))

    def delete_selected(self, side):
        if not self._need_S():
            return
        nodes = self._sel_pruned()
        if not nodes:
            return
        if side == "ask":
            side = "B" if all(n.left and n.right for n in nodes) else ("L" if all(n.left for n in nodes) else "R")
        sides = ["L", "R"] if side == "B" else [side]
        sides = [s for s in sides if not self.S.fs(s).readonly]
        if not sides:
            toast("That side is read-only", "warn")
            return
        names = "\n".join(n.rel for n in nodes[:8]) + ("\n…" if len(nodes) > 8 else "")
        where = " and ".join("left" if s == "L" else "right" for s in sides)
        trash = bool(self.cfg["use_recycle_bin"]) and all(isinstance(self.S.fs(s), vfs.LocalFS) for s in sides)
        how = "Move to the Recycle Bin / Trash" if trash else "PERMANENTLY delete"
        if QMessageBox.warning(self, "Delete", f"{how} {len(nodes)} item(s) on the {where} side?\n\n{names}",
                               QMessageBox.Yes | QMessageBox.Cancel, QMessageBox.Cancel) != QMessageBox.Yes:
            return
        S = self.S

        def go(ctx):
            total, errs = 0, []
            for s in sides:
                c, e = fsops.delete_nodes(nodes, s, S, trash, ctx)
                total += c
                errs += e
            return total, errs
        self._run_task("Deleting…", go, "{n} item(s) deleted")

    def rename_selected(self):
        if not self._need_S():
            return
        nodes = self._sel_nodes()
        if len(nodes) != 1:
            toast("Select a single item to rename", "warn")
            return
        n = nodes[0]
        new, ok = QInputDialog.getText(self, "Rename", "New name (applied to every side where the item exists):", text=n.name.rsplit("/", 1)[-1])
        if not ok or not new.strip():
            return
        errs = []
        for s in ("L", "R"):
            if fsops.info(n, s) and not self.S.fs(s).readonly:
                e = fsops.rename_node(n, s, new.strip(), self.S)
                if e:
                    errs.append(f"{'left' if s == 'L' else 'right'}: {e}")
        if errs:
            QMessageBox.warning(self, "Rename", "\n".join(errs))
        self.refresh()

    def new_folder(self):
        if not self._need_S():
            return
        nodes = self._sel_nodes()
        base_rel = ""
        if nodes:
            n = nodes[0]
            base_rel = n.rel if n.is_dir else n.rel.rpartition("/")[0]
        name, ok = QInputDialog.getText(self, "New folder", "Folder name (created on both sides when possible):")
        if not ok or not name.strip():
            return
        errs = []
        for s in ("L", "R"):
            if self.S.fs(s).readonly or self.S.root(s) is None:
                continue
            fs = self.S.fs(s)
            parent = fs.join(self.S.root(s), base_rel) if base_rel else self.S.root(s)
            e = fsops.make_folder(s, parent, name.strip(), self.S)
            if e:
                errs.append(e)
        if errs:
            QMessageBox.warning(self, "New folder", "\n".join(errs))
        self.refresh()

    def touch_selected(self, side, mode):
        if not self._need_S():
            return
        nodes = self._sel_pruned()
        value = None
        if mode == "value":
            dlg = QDialog(self)
            dlg.setWindowTitle("Set modified time")
            lay = QVBoxLayout(dlg)
            e = QDateTimeEdit(QDateTime.currentDateTime())
            e.setCalendarPopup(True)
            e.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
            lay.addWidget(QLabel("New modified time:"))
            lay.addWidget(e)
            bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            bb.accepted.connect(dlg.accept)
            bb.rejected.connect(dlg.reject)
            lay.addWidget(bb)
            if not dlg.exec():
                return
            value = e.dateTime().toSecsSinceEpoch()
        S = self.S
        sides = ["L", "R"] if side == "B" else [side]

        def go(ctx):
            total, errs = 0, []
            for s in sides:
                files = []
                for n in nodes:
                    files.append(n)
                c, e = fsops.touch_nodes(files, s, S, mode, value)
                total += c
                errs += e
            return total, errs
        self._run_task("Updating timestamps…", go, "{n} timestamp(s) updated")

    # ------------------------------------------------------------- snapshots
    def save_snapshot(self, side):
        if not self._need_S():
            return
        fs, root = self.S.fs(side), self.S.root(side)
        if fs is None:
            toast("That side is empty", "warn")
            return
        name = os.path.basename((self.left if side == "L" else self.right).rstrip("/\\")) or "snapshot"
        path, _ = QFileDialog.getSaveFileName(self, "Save snapshot", f"{name}{vfs.SNAPSHOT_EXT}", "Lavati snapshot (*.lsnap)")
        if not path:
            return
        box = QMessageBox(QMessageBox.Question, "Snapshot", "Include CRC32 checksums of every file?\n(Slower, but lets you detect content changes later.)",
                          QMessageBox.Yes | QMessageBox.No, self)
        with_crc = box.exec() == QMessageBox.Yes
        patterns = list(self.opts.patterns)
        self._run_task("Saving snapshot…", lambda ctx: (vfs.capture_snapshot(fs, root, path, patterns, with_crc, ctx), []),
                       "Snapshot saved ({n} entries)")

    def load_snapshot(self, side):
        p, _ = QFileDialog.getOpenFileName(self, "Open snapshot", "", "Lavati snapshot (*.lsnap)")
        if p:
            self._set_path(side, os.path.normpath(p))

    # --------------------------------------------------------------- sync
    def sync(self, preset=None):
        from .sync_dialog import SyncDialog
        if not self._need_S():
            return
        if self.S.lfs is None or self.S.rfs is None:
            QMessageBox.warning(self, "Sync", "Both sides must be loaded.")
            return
        sel = [n.rel for n in self._sel_nodes()]
        dlg = SyncDialog(self.nodes, self.S, sel, self.cfg, self.compare_time, self, self.left, self.right,
                         self.opts.to_dict(), preset)
        dlg.exec()
        if dlg.ran:
            self.refresh()

    # -------------------------------------------------------------- report
    def report(self):
        dlg = ReportDialog(self)
        if not dlg.exec():
            return
        fmt, show = dlg.fmt.currentData(), dlg.show_.currentData()
        ext = {"html": "html", "text": "txt", "xml": "xml"}[fmt]
        path, _ = QFileDialog.getSaveFileName(self, "Save report", f"folder-report.{ext}", f"{fmt.upper()} (*.{ext})")
        if not path:
            return
        try:
            n = reports.write_folder_report(path, fmt, self.left, self.right, self.nodes, show, self.counter)
        except OSError as e:
            QMessageBox.warning(self, "Report", str(e))
            return
        toast(f"Report saved ({n} files) to {os.path.basename(path)}")

    def swap_sides(self):
        self.left, self.right = self.right, self.left
        self.le_left.setText(self.left)
        self.le_right.setText(self.right)
        self.opts.aligns = [[b, a] for a, b in self.opts.aligns]
        self.refresh()
