"""Home page: sidebar navigation, quick-compare bar, session cards, recent sessions and settings pages."""
import os
import sys
import time

from PySide6.QtCore import QEvent, QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
                               QFileDialog, QFormLayout, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QInputDialog,
                               QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton, QScrollArea,
                               QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import (config, connect_dialog, engine, fileformats, gitvfs, highlight, platform_utils, regcmp, script, shell,
               textdialogs, theme, vfs)
from .widgets import Pill, card, pick_file, pick_folder, split_browse_button, sty, toast

KINDS = {
    "folder": ("Folder Compare", "Folders, archives, snapshots and servers. Sync and filter by status.", "folder", "#e3e7fb", theme.ACCENT),
    "text": ("Text Compare", "Line and character diff with live editing.", "doc", "#fdf0d9", "#b36b00"),
    "merge": ("Merge", "Two- or three-way merge of files or whole folders.", "merge", "#ece6fb", theme.PURPLE),
    "table": ("Table Compare", "CSV, Excel sheets and HTML tables by key column.", "table", "#dcf1e5", theme.GREEN),
    "hex": ("Hex Compare", "Byte-level diff for binaries and APKs.", "hex", "#dfeaf7", "#1d5a96"),
    "image": ("Picture Compare", "Overlay, slider, blink and pixel-tolerance difference.", "image", "#fbe1e4", "#b3263b"),
    "doc": ("Document Compare", "Text of Word, PDF, OpenDocument, RTF and PowerPoint.", "report", "#fdf0d9", "#b36b00"),
    "media": ("Media Compare", "MP3, FLAC and MP4/AAC tags and audio data.", "music", "#ece6fb", theme.PURPLE),
    "version": ("Version Compare", "Version info and header facts of EXE and DLL files.", "cube", "#dfeaf7", "#1d5a96"),
    "registry": ("Registry Compare", "Windows registry keys: live, or from .reg files.", "key", "#dcf1e5", theme.GREEN),
    "git": ("Git Compare", "Branches, tags, commits, stashes, index and working tree - no checkout needed.", "git", "#fbe1e4", "#b3263b"),
}
KIND_LABEL = {"git": "Git", "folder": "Folder", "text": "Text", "merge": "Merge", "table": "Table", "hex": "Hex", "image": "Picture",
              "doc": "Document", "media": "Media", "version": "Version", "registry": "Registry", "sync": "Sync"}
RESULT_STYLE = {
    "changed": (theme.AMBER, theme.CHIP_AMBER), "hunks": (theme.AMBER, theme.CHIP_AMBER), "conflicts": (theme.RED, theme.RED_BG),
    "diffs": (theme.AMBER, theme.CHIP_AMBER), "bytes": (theme.AMBER, theme.CHIP_AMBER), "diff": (theme.AMBER, theme.CHIP_AMBER),
    "identical": (theme.GREEN, theme.GREEN_BG), "resolved": (theme.GREEN, theme.GREEN_BG),
}
CARD_SHORT = {'folder': 'Folders, archives, servers', 'text': 'Line and character diff, editable', 'merge': 'Two- or three-way, files or folders', 'table': 'CSV, Excel, HTML by key column', 'hex': 'Byte-level diff for binaries', 'image': 'Overlay, slider, pixel tolerance', 'doc': 'Word, PDF, ODF, RTF, PowerPoint', 'media': 'MP3, FLAC, MP4 tags and audio', 'version': 'EXE and DLL version info', 'registry': 'Live registry or .reg files', 'git': 'Branches, commits, index, worktree'}
FOLDER_KINDS = ("folder", "merge")
ARCHIVE_NOTE = "Archive"


def _is_special_location(p):
    """Things that are 'folders' for comparison purposes but not directories on disk."""
    if not p:
        return False
    return vfs.is_remote_spec(p) or vfs.is_archive_file(p) or p.lower().endswith(vfs.SNAPSHOT_EXT) \
        or p.lower().startswith("snapshot:")


def _ext(p):
    return os.path.splitext(p)[1].lower()


def detect(left, right):
    """Return (kind, label) for a pair of paths, or (None, reason)."""
    if not left or not right:
        return None, "Enter two paths"
    if regcmp.is_registry_spec(left) or regcmp.is_registry_spec(right):
        if (regcmp.is_registry_spec(left) or _ext(left) == ".reg") and (regcmp.is_registry_spec(right) or _ext(right) == ".reg"):
            return "registry", "Registry vs Registry"
        return None, "Registry keys can only be compared with other keys or .reg files"
    if gitvfs.is_git_spec(left) or gitvfs.is_git_spec(right):
        return "folder", "Git vs Git" if (gitvfs.is_git_spec(left) and gitvfs.is_git_spec(right)) else "Git vs Folder"
    if vfs.is_remote_spec(left) or vfs.is_remote_spec(right):
        return "folder", "Server vs Folder"
    ld, rd = os.path.isdir(left), os.path.isdir(right)
    if ld and rd:
        return "folder", "Folder vs Folder"
    la, ra = _is_special_location(left), _is_special_location(right)
    if (la or ld) and (ra or rd):
        if left.lower().endswith(vfs.SNAPSHOT_EXT) or right.lower().endswith(vfs.SNAPSHOT_EXT):
            return "folder", "Snapshot vs Folder"
        return "folder", "Archive vs Archive" if (la and ra) else "Archive vs Folder"
    if ld != rd:
        return None, "Mixed file / folder"
    if not (os.path.isfile(left) and os.path.isfile(right)):
        return None, "Path not found"
    le, re_ = _ext(left), _ext(right)
    if le in engine.IMAGE_EXTS and re_ in engine.IMAGE_EXTS:
        return "image", "Picture vs Picture"
    if le == ".reg" and re_ == ".reg":
        return "registry", "Registry export vs Registry export"
    if le in engine.TABLE_EXTS and re_ in engine.TABLE_EXTS:
        return "table", "Table vs Table"
    if le in engine.MEDIA_EXTS and re_ in engine.MEDIA_EXTS:
        return "media", "Media vs Media"
    if le in engine.DOC_EXTS and re_ in engine.DOC_EXTS:
        return "doc", "Document vs Document"
    for p in (left, right):
        try:
            with open(p, "rb") as f:
                chunk = f.read(4096)
            if b"\x00" in chunk and not chunk.startswith((b"\xff\xfe", b"\xfe\xff")):
                return "hex", "Binary vs Binary"
        except OSError:
            return None, "Cannot read file"
    return "text", "Text vs Text"


def session_name(kind, left, right):
    def base(p):
        if gitvfs.is_git_spec(p):
            repo, rev = gitvfs.parse_spec(p)
            return f"{os.path.basename(repo.rstrip(chr(92) + '/'))}@{rev[:12]}"
        p = p.rstrip("\\/")
        if vfs.is_remote_spec(p):
            p = p.split("?")[0]
        return os.path.basename(p) or p
    bl, br = base(left), base(right)
    if kind in ("folder", "sync"):
        return f"{bl} vs {br}"
    if kind == "merge":
        return f"{br} merge"
    if bl == br:
        return bl
    return f"{os.path.splitext(bl)[0]} vs {os.path.splitext(br)[0]}" if kind == "table" else f"{bl} vs {br}"


def short_result_style(result):
    for k, v in RESULT_STYLE.items():
        if k in result:
            return v
    return theme.MUTED, theme.CHIP_GRAY


def human_time(ts):
    t = time.localtime(ts)
    now = time.localtime()
    if t.tm_year == now.tm_year and t.tm_yday == now.tm_yday:
        return "Today, " + time.strftime("%H:%M", t)
    if t.tm_year == now.tm_year and now.tm_yday - t.tm_yday == 1:
        return "Yesterday"
    return time.strftime("%#d %b" if os.name == "nt" else "%-d %b", t)


class PathBox(QWidget):
    """Line edit + Browse. Browse opens the right dialog immediately; the arrow lists the alternatives.

    default: "file", "folder", or "auto" (match whatever the partner box holds, else a file)."""

    def __init__(self, folder_ok=True, parent=None, registry=False, default="auto"):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self.edit = QLineEdit()
        self.edit.setProperty("mono", True)
        self.edit.setFixedHeight(42)
        self.folder_ok = folder_ok
        self.registry = registry
        self.default = default
        self.partner = None
        items = [("Choose file…", lambda: self._pick(False))]
        if folder_ok:
            items += [("Choose folder…", lambda: self._pick(True)), ("Connect to a server…", self._connect)]
        if registry and sys.platform == "win32":
            items.append(("Registry key…", self._registry))
        self.btn = split_browse_button(self.browse, items)
        lay.addWidget(self.edit, 1)
        lay.addWidget(self.btn)

    def browse(self):
        want_folder = self.default == "folder"
        if self.default == "auto" and self.folder_ok:
            other = self.partner.text() if self.partner is not None else ""
            want_folder = bool(other) and (os.path.isdir(other) or vfs.is_remote_spec(other))
        self._pick(want_folder and self.folder_ok)

    def _pick(self, folder):
        start = self.edit.text() or (self.partner.text() if self.partner is not None else "")
        p = pick_folder(self, "Select folder", start) if folder else pick_file(self, "Select file", start)
        if p:
            self.edit.setText(p)

    def _connect(self):
        dlg = connect_dialog.ConnectDialog(self)
        if dlg.exec() and dlg.url:
            self.edit.setText(dlg.url)

    def _registry(self):
        t, ok = QInputDialog.getText(self, "Registry key", "Key path, e.g. HKCU\\Software\\MyApp:", text=self.edit.text())
        if ok and t.strip():
            self.edit.setText(t.strip())

    def text(self):
        return self.edit.text().strip().strip('"')

    def setText(self, t):
        self.edit.setText(t)


class PickDialog(QDialog):
    """Ask for paths for a given session kind."""

    def __init__(self, kind, left="", right="", parent=None):
        super().__init__(parent)
        self.kind = kind
        self.setWindowTitle(KINDS[kind][0])
        self.setMinimumWidth(720)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(8)
        t = QLabel(KINDS[kind][0])
        t.setStyleSheet("font-size:15pt; font-weight:700;")
        lay.addWidget(t)
        d = QLabel(KINDS[kind][1])
        d.setStyleSheet(f"color:{theme.MUTED};")
        d.setWordWrap(True)
        lay.addWidget(d)
        lay.addSpacing(8)
        self.boxes = []
        labels = ["OURS (or LEFT)", "BASE (optional – leave empty for a two-way merge)", "THEIRS (or RIGHT)", "OUTPUT (optional)"] \
            if kind == "merge" else ["LEFT", "RIGHT"]
        folder_ok = kind in FOLDER_KINDS
        for i, lab in enumerate(labels):
            l = QLabel(lab)
            l.setStyleSheet(f"color:{theme.MUTED}; font-weight:700; font-size:8pt; letter-spacing:1px;")
            b = PathBox(folder_ok, registry=(kind == "registry"), default="folder" if kind == "folder" else ("auto" if kind == "merge" else "file"))
            lay.addWidget(l)
            lay.addWidget(b)
            self.boxes.append(b)
        for i, b in enumerate(self.boxes):
            b.partner = self.boxes[0] if i else (self.boxes[1] if len(self.boxes) > 1 else None)
        if kind != "merge":
            self.boxes[0].setText(left)
            self.boxes[1].setText(right)
        row = QHBoxLayout()
        row.addStretch(1)
        c = QPushButton("Cancel")
        c.clicked.connect(self.reject)
        ok = QPushButton("Start")
        ok.setProperty("primary", True)
        ok.clicked.connect(self._ok)
        for b in (c, ok):
            b.setMinimumHeight(38)
            row.addWidget(b)
        lay.addSpacing(10)
        lay.addLayout(row)

    def _ok(self):
        vals = [b.text() for b in self.boxes]
        if self.kind == "merge":
            need = [0, 2]
        else:
            need = [0, 1]
        if not all(vals[i] for i in need):
            QMessageBox.warning(self, "Missing path", "Please choose all required paths.")
            return
        for i, v in enumerate(vals):
            if not v:
                continue
            if i == 3 and self.kind == "merge":
                continue
            if vfs.is_remote_spec(v) or regcmp.is_registry_spec(v):
                continue
            if not os.path.exists(v):
                QMessageBox.warning(self, "Not found", f"Path does not exist:\n{v}")
                return
        self.values = vals
        self.accept()


class SessionTable(QTableWidget):
    openSession = Signal(dict)
    changed = Signal()

    def __init__(self, cfg, limit=None, parent=None):
        super().__init__(0, 5, parent)
        self.cfg, self.limit = cfg, limit
        self.setHorizontalHeaderLabels(["Session", "Type", "Left vs Right", "Result", "Opened"])
        self.verticalHeader().hide()
        self.setShowGrid(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setSelectionMode(QAbstractItemView.SingleSelection)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._menu)
        h = self.horizontalHeader()
        h.setHighlightSections(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        for c, w in ((1, 110), (3, 170), (4, 150)):
            h.setSectionResizeMode(c, QHeaderView.Fixed)
            self.setColumnWidth(c, w)
        self.horizontalHeader().setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.setStyleSheet(f"QTableWidget {{ border:1px solid {theme.BORDER}; border-radius:10px; }}"
                           f"QTableWidget::item {{ padding-left:12px; }}"
                           f"QTableWidget::item:selected {{ background:{theme.ACCENT_SOFT}; color:{theme.TEXT}; }}")
        self.cellDoubleClicked.connect(lambda r, c: self.openSession.emit(self.sessions[r]))
        self.filter = ""
        self.sessions = []
        self.empty = QLabel(self.viewport())
        self.empty.setAlignment(Qt.AlignCenter)
        self.empty.setTextFormat(Qt.RichText)
        self.empty.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.empty.setStyleSheet('background:transparent;')
        self.empty.hide()

    def populate(self, filter_text=""):
        self.filter = filter_text.lower()
        items = [s for s in self.cfg["sessions"]
                 if not self.filter or self.filter in (s["name"] + s["left"] + s["right"]).lower()]
        if self.limit:
            items = items[:self.limit]
        self.sessions = items
        self.setRowCount(len(items))
        if not items:
            msg = ('No sessions match your search' if self.filter else 'No sessions yet')
            sub = '' if self.filter else 'Drop two files or folders onto the window, or pick a session type above.'
            self.empty.setText(f"<div style='font-size:12pt; font-weight:600'>{msg}</div>"
                               f"<div style='color:{theme.MUTED}'>{sub}</div>")
            self.empty.setGeometry(self.viewport().rect())
            self.empty.show()
        else:
            self.empty.hide()
        for r, s in enumerate(items):
            self.setRowHeight(r, 46)
            name = QTableWidgetItem(("★ " if s.get("pinned") else "") + s["name"])
            f = name.font()
            f.setBold(True)
            name.setFont(f)
            self.setItem(r, 0, name)
            self.setItem(r, 1, QTableWidgetItem(KIND_LABEL.get(s["kind"], s["kind"])))

            def short(p):
                p = p.rstrip("\\/")
                return os.path.basename(p) or p
            lr = QTableWidgetItem(f"{short(s['left'])} vs {short(s['right'])}")
            lr.setFont(theme.mono(9))
            lr.setToolTip(f"{s['left']}\n{s['right']}")
            self.setItem(r, 2, lr)
            if s.get("result"):
                fg, bg = short_result_style(s["result"])
                w = QWidget()
                wl = QHBoxLayout(w)
                wl.setContentsMargins(10, 0, 0, 0)
                wl.addWidget(Pill(s["result"], fg, bg), 0, Qt.AlignLeft | Qt.AlignVCenter)
                self.setCellWidget(r, 3, w)
            else:
                self.removeCellWidget(r, 3)
            self.setItem(r, 4, QTableWidgetItem(human_time(s["opened"])))
        self.setFixedHeight(max(150, self.horizontalHeader().height() + 46 * len(items) + 4)) if self.limit else None

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.empty.setGeometry(self.viewport().rect())

    def _menu(self, pos):
        r = self.rowAt(pos.y())
        if r < 0:
            return
        s = self.sessions[r]
        m = QMenu(self)
        m.addAction("Open", lambda: self.openSession.emit(s))
        m.addAction("Unpin" if s.get("pinned") else "Pin to sidebar", lambda: (self.cfg.toggle_pin(s), self.changed.emit()))
        m.addAction("Remove", lambda: (self.cfg.remove_session(s), self.changed.emit()))
        m.exec(self.viewport().mapToGlobal(pos))


class NavButton(QPushButton):
    def __init__(self, text, icon_name):
        super().__init__("  " + text.replace("&", "&&"))
        self.name = icon_name
        self.setCheckable(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setIconSize(QSize(20, 20))
        self.setFixedHeight(46)
        self.setStyleSheet(
            f"QPushButton {{ text-align:left; padding-left:14px; border:none; border-radius:8px; background:transparent; font-size:11pt; }}"
            f"QPushButton:hover {{ background:{theme.HOVER}; }}"
            f"QPushButton:checked {{ background:{theme.ACCENT_SOFT}; color:{theme.ACCENT_TEXT}; font-weight:700; }}")
        self.toggled.connect(self._icon)
        self._icon(False)

    def _icon(self, on):
        self.setIcon(theme.icon(self.name, theme.ACCENT_TEXT if on else theme.TEXT, 20))


class FormatDialog(QDialog):
    """Edit one file format (per-file-type rules)."""

    def __init__(self, fmt, parent=None):
        super().__init__(parent)
        self.setWindowTitle("File format")
        self.setMinimumWidth(620)
        self.fmt = dict(fileformats.blank_format(), **fmt)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(22, 18, 22, 16)
        form = QFormLayout()
        self.name = QLineEdit(self.fmt["name"])
        self.patterns = QLineEdit(self.fmt["patterns"])
        self.patterns.setPlaceholderText("*.log; *.out")
        self.lang = QComboBox()
        self.lang.addItem("(detect from the file name)", "")
        for n in highlight.LANG_NAMES:
            self.lang.addItem(n, n)
        self.lang.setCurrentIndex(max(0, self.lang.findData(self.fmt.get("language", ""))))
        self.enc = QComboBox()
        self.enc.addItem("(detect automatically)", "")
        for n in engine.ENCODINGS:
            self.enc.addItem(n, n)
        self.enc.setCurrentIndex(max(0, self.enc.findData(self.fmt.get("encoding", ""))))
        self.conv = QComboBox()
        self.conv.addItem("None – compare the file as text", "")
        self.conv.addItem("Built-in text extraction (Word, PDF, OpenDocument, RTF, PowerPoint)", "builtin")
        self.conv.addItem("External converter command", "external")
        self.conv.setCurrentIndex(max(0, self.conv.findData(self.fmt.get("converter", ""))))
        self.cmd = QLineEdit(self.fmt.get("command", ""))
        self.cmd.setPlaceholderText('pdftotext -layout "{in}" "{out}"      ({in} = source file, {out} = text file)')
        form.addRow("Name", self.name)
        form.addRow("File patterns", self.patterns)
        form.addRow("Grammar / language", self.lang)
        form.addRow("Encoding", self.enc)
        form.addRow("Conversion", self.conv)
        form.addRow("Converter command", self.cmd)
        lay.addLayout(form)
        r = engine.TextRules(ws=self.fmt.get("ws") or "none", ignore_case=bool(self.fmt.get("ignore_case")),
                             unimportant=list(self.fmt.get("unimportant", [])), replacements=[tuple(x) for x in self.fmt.get("replacements", [])])
        self.use_ws = QCheckBox("Override whitespace / case rules for these files")
        self.use_ws.setChecked(bool(self.fmt.get("ws")) or self.fmt.get("ignore_case") is not None)
        lay.addWidget(self.use_ws)
        self.rules = textdialogs.RulesEditor(r, show_eol=False)
        lay.addWidget(self.rules)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        lay.addWidget(bb)
        self.conv.currentIndexChanged.connect(self._conv)
        self._conv()

    def _conv(self):
        self.cmd.setEnabled(self.conv.currentData() == "external")

    def _ok(self):
        if not self.name.text().strip() or not self.patterns.text().strip():
            QMessageBox.warning(self, "File format", "Give the format a name and at least one file pattern.")
            return
        if self.conv.currentData() == "external" and not self.cmd.text().strip():
            QMessageBox.warning(self, "File format", "Enter the converter command line.")
            return
        r = self.rules.rules()
        if r.invalid_patterns():
            return
        self.result = dict(name=self.name.text().strip(), patterns=self.patterns.text().strip(), language=self.lang.currentData(),
                           encoding=self.enc.currentData(), converter=self.conv.currentData(), command=self.cmd.text().strip(),
                           ws=r.ws if self.use_ws.isChecked() and r.ws != "none" else "",
                           ignore_case=True if (self.use_ws.isChecked() and r.ignore_case) else None,
                           unimportant=list(r.unimportant), replacements=[list(x) for x in r.replacements])
        self.accept()


class ScriptEditor(QDialog):
    """Edit and run a Lavati script."""
    output = Signal(str)

    def __init__(self, path, parent=None):
        super().__init__(parent)
        self.path = path
        self.setWindowTitle(os.path.basename(path))
        self.resize(900, 640)
        lay = QVBoxLayout(self)
        self.edit = QPlainTextEdit()
        self.edit.setFont(theme.mono(10))
        self.edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                self.edit.setPlainText(f.read())
        except OSError:
            pass
        lay.addWidget(self.edit, 2)
        self.log = QPlainTextEdit()
        self.log.setReadOnly(True)
        self.log.setFont(theme.mono(9))
        self.log.setPlaceholderText("Run output appears here")
        lay.addWidget(self.log, 1)
        row = QHBoxLayout()
        b_save, b_run, b_close = QPushButton("Save"), QPushButton("Save and run"), QPushButton("Close")
        b_run.setProperty("primary", True)
        b_save.clicked.connect(self.save)
        b_run.clicked.connect(self.run)
        b_close.clicked.connect(self.accept)
        row.addStretch(1)
        for b in (b_save, b_run, b_close):
            b.setMinimumHeight(34)
            row.addWidget(b)
        lay.addLayout(row)
        self.output.connect(self.log.appendPlainText)
        self.thread = None

    def save(self):
        with open(self.path, "w", encoding="utf-8", newline="\n") as f:
            f.write(self.edit.toPlainText())

    def run(self):
        self.save()
        self.log.clear()
        outer = self

        class T(QThread):
            def run(self_):
                try:
                    code = script.run_file(outer.path, out=lambda s: outer.output.emit(s), cfg=outer_cfg)
                    outer.output.emit(f"-- finished with exit code {code}")
                except Exception as e:
                    outer.output.emit(f"-- failed: {e}")
        outer_cfg = config.Config()
        self.thread = T()
        self.thread.start()


class HomePage(QWidget):
    compareRequested = Signal(str, str, str)    # kind, left, right  (kind "" = auto)
    sessionRequested = Signal(dict)
    kindRequested = Signal(str)

    def __init__(self, cfg, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(self._sidebar())
        self.stack = QStackedWidget()
        sa = QScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        sa.setWidget(self._start_page())
        self.stack.addWidget(sa)
        for page in (self._sessions_page(), self._rules_page(), self._scripts_page(), self._settings_page()):
            sc = QScrollArea()
            sc.setWidgetResizable(True)
            sc.setFrameShape(QFrame.NoFrame)
            sc.setWidget(page)
            self.stack.addWidget(sc)
        root.addWidget(self.stack, 1)
        self.nav[0].setChecked(True)
        self.refresh()
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._focus_search)

    # ------------------------------------------------------------ sidebar
    def _sidebar(self):
        side = QFrame()
        side.setFixedWidth(334)
        sty(side, f"background:{theme.SIDEBAR}; border-right:1px solid {theme.BORDER};")
        lay = QVBoxLayout(side)
        lay.setContentsMargins(20, 26, 14, 20)
        lay.setSpacing(4)
        logo = QHBoxLayout()
        mark = QLabel()
        mark.setFixedSize(44, 44)
        mark.setPixmap(self._logo())
        logo.addWidget(mark)
        tl = QVBoxLayout()
        tl.setSpacing(0)
        t = QLabel("Lavati Compare")
        t.setStyleSheet("font-size:13pt; font-weight:700; background:transparent; border:none;")
        from . import __version__
        v = QLabel(f"v{__version__} · Pro")
        v.setStyleSheet(f"color:{theme.MUTED}; background:transparent; border:none;")
        tl.addWidget(t)
        tl.addWidget(v)
        logo.addLayout(tl)
        logo.addStretch(1)
        lay.addLayout(logo)
        lay.addSpacing(18)
        self.nav = []
        grp = QButtonGroup(self)
        for i, (text, ic) in enumerate((("Home", "home"), ("Saved sessions", "list"), ("Rules & file formats", "rules"),
                                        ("Scripts & automation", "scripts"), ("Settings", "settings"))):
            b = NavButton(text, ic)
            grp.addButton(b, i)
            self.nav.append(b)
            lay.addWidget(b)
        grp.idClicked.connect(self._nav)
        lay.addSpacing(24)
        lab = QLabel("PINNED SESSIONS")
        lab.setStyleSheet(f"color:{theme.MUTED}; font-weight:700; font-size:9pt; letter-spacing:1px; margin-left:14px; background:transparent; border:none;")
        lay.addWidget(lab)
        self.pin_box = QVBoxLayout()
        self.pin_box.setSpacing(0)
        lay.addLayout(self.pin_box)
        lay.addStretch(1)
        return side

    @staticmethod
    def _logo():
        pm = theme.logo_pixmap(88)
        pm.setDevicePixelRatio(2)
        return pm

    def _nav(self, i):
        self.stack.setCurrentIndex(i)
        self.refresh()

    def show_page(self, i):
        self.nav[i].setChecked(True)
        self._nav(i)

    # --------------------------------------------------------- start page
    def _page(self):
        w = QFrame()
        sty(w, f"background:{theme.BG};")
        lay = QVBoxLayout(w)
        lay.setContentsMargins(48, 28, 40, 24)
        lay.setSpacing(0)
        return w, lay

    def _title(self, title, sub):
        box = QVBoxLayout()
        box.setSpacing(2)
        t = QLabel(title)
        t.setStyleSheet("font-size:24pt; font-weight:700; background:transparent;")
        s = QLabel(sub)
        s.setStyleSheet(f"color:{theme.MUTED}; font-size:12pt; background:transparent;")
        s.setWordWrap(True)
        box.addWidget(t)
        box.addWidget(s)
        return box

    def _start_page(self):
        w, lay = self._page()
        top = QHBoxLayout()
        top.addLayout(self._title("Start a comparison", "Drop two files or folders anywhere, or pick a session type."))
        top.addStretch(1)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search sessions, paths...")
        self.search.setFixedHeight(58)
        self.search.setMinimumWidth(240)
        self.search.setMaximumWidth(396)
        self.search.setStyleSheet(f"padding-left:8px; padding-right:60px; font-size:11pt; background:{theme.PANEL};")
        self.search.addAction(theme.icon("search", theme.MUTED, 18), QLineEdit.LeadingPosition)
        self.search.textChanged.connect(lambda t: self.recent.populate(t))
        kb = QLabel("Ctrl K", self.search)
        kb.setFont(theme.mono(8))
        kb.setStyleSheet(f"color:{theme.MUTED}; border:1px solid {theme.BORDER}; border-radius:4px; padding:1px 6px; background:{theme.BTN};")
        kb.adjustSize()
        self._kb = kb
        self.search.installEventFilter(self)
        kb.move(self.search.width() - kb.width() - 16, (58 - kb.height()) // 2)
        top.addWidget(self.search, 0, Qt.AlignTop)
        lay.addLayout(top)
        lay.addSpacing(26)

        bar = card()
        bl = QVBoxLayout(bar)
        bl.setContentsMargins(28, 24, 28, 22)
        bl.setSpacing(10)
        row = QHBoxLayout()
        row.setSpacing(10)
        self.p_left, self.p_right = PathBox(registry=True), PathBox(registry=True)
        for lab, box in (("LEFT", self.p_left), ("RIGHT", self.p_right)):
            col = QVBoxLayout()
            col.setSpacing(6)
            l = QLabel(lab)
            l.setStyleSheet(f"color:{theme.MUTED}; font-weight:700; font-size:9pt; letter-spacing:1px; border:none;")
            col.addWidget(l)
            col.addWidget(box)
            row.addLayout(col, 1)
            if lab == "LEFT":
                sw = QPushButton()
                sw.setIcon(theme.icon("swap", theme.TEXT, 20))
                sw.setFixedSize(60, 42)
                sw.clicked.connect(self._swap)
                v = QVBoxLayout()
                v.addSpacing(26)
                v.addWidget(sw)
                row.addLayout(v)
        bl.addLayout(row)
        go = QPushButton("Compare  ")
        go.setIcon(theme.icon("right", theme.PANEL, 18))
        go.setLayoutDirection(Qt.RightToLeft)
        go.setProperty("primary", True)
        go.setFixedSize(170, 40)
        go.clicked.connect(self._go)
        self.detected = QLabel()
        self.detected.setTextFormat(Qt.RichText)
        self.detected.setStyleSheet("border:none;")
        self.detected.setWordWrap(True)
        self.detected.linkActivated.connect(lambda _: self.show_page(2))
        foot = QHBoxLayout()
        foot.addWidget(self.detected, 1)
        foot.addWidget(go)
        bl.addLayout(foot)
        lay.addWidget(bar)
        self.p_left.partner, self.p_right.partner = self.p_right, self.p_left
        for b in (self.p_left, self.p_right):
            b.edit.textChanged.connect(self._detect)
            b.edit.returnPressed.connect(self._go)
        lay.addSpacing(30)

        h = QLabel("New session")
        h.setStyleSheet("font-size:13pt; font-weight:700; background:transparent;")
        lay.addWidget(h)
        lay.addSpacing(10)
        self.card_grid = QGridLayout()
        self.card_grid.setSpacing(12)
        self.cards = [self._kind_card(k, title, desc, ic, bg, fg) for k, (title, desc, ic, bg, fg) in KINDS.items()]
        self._cols = 0
        self._layout_cards(4)
        lay.addLayout(self.card_grid)
        lay.addSpacing(28)

        rh = QHBoxLayout()
        h2 = QLabel("Recent")
        h2.setStyleSheet("font-size:13pt; font-weight:700; background:transparent;")
        va = QLabel(f"<a href='x' style='color:{theme.ACCENT}'>View all</a>")
        va.linkActivated.connect(lambda _: self.show_page(1))
        rh.addWidget(h2)
        rh.addStretch(1)
        rh.addWidget(va)
        lay.addLayout(rh)
        lay.addSpacing(8)
        self.recent = SessionTable(self.cfg, limit=6)
        self.recent.openSession.connect(self.sessionRequested)
        self.recent.changed.connect(self.refresh)
        lay.addWidget(self.recent)
        lay.addStretch(1)
        self._detect()
        return w

    def _kind_card(self, kind, title, desc, ic, bg, fg):
        f = QPushButton()
        f.setCursor(Qt.PointingHandCursor)
        f.setFixedHeight(76)
        f.setToolTip(desc)
        f.setStyleSheet(f"QPushButton {{ background:{theme.PANEL}; border:1px solid {theme.BORDER}; border-radius:10px; text-align:left; }}"
                        f"QPushButton:hover {{ border:1px solid {theme.ACCENT}; background:{theme.HOVER}; }}")
        lay = QHBoxLayout(f)
        lay.setContentsMargins(16, 10, 14, 10)
        lay.setSpacing(12)
        i = QLabel()
        i.setPixmap(theme.icon(ic, fg, 20).pixmap(20, 20))
        i.setFixedSize(40, 40)
        i.setAlignment(Qt.AlignCenter)
        i.setStyleSheet(f"background:{bg}; border-radius:8px; border:none;")
        i.setAttribute(Qt.WA_TransparentForMouseEvents)
        col = QVBoxLayout()
        col.setSpacing(0)
        t = QLabel(title)
        t.setStyleSheet("font-weight:700; font-size:11pt; background:transparent; border:none;")
        short = desc.split(".")[0]
        d = QLabel(short if len(short) < 46 else short[:43] + "\u2026")
        d.setStyleSheet(f"color:{theme.MUTED}; font-size:9pt; background:transparent; border:none;")
        for x in (i, t, d):
            x.setAttribute(Qt.WA_TransparentForMouseEvents)
        col.addStretch(1)
        col.addWidget(t)
        col.addWidget(d)
        col.addStretch(1)
        lay.addWidget(i)
        lay.addLayout(col, 1)
        f.clicked.connect(lambda: self.kindRequested.emit(kind))
        return f

    def _layout_cards(self, cols):
        """Re-flow the session cards: as many columns as the width allows (2 to 4)."""
        if cols == self._cols:
            return
        self._cols = cols
        for c in self.cards:
            self.card_grid.removeWidget(c)
        for i, c in enumerate(self.cards):
            self.card_grid.addWidget(c, i // cols, i % cols)
        for c in range(6):
            self.card_grid.setColumnStretch(c, 1 if c < cols else 0)

    def eventFilter(self, obj, ev):
        if obj is getattr(self, "search", None) and ev.type() == QEvent.Resize and hasattr(self, "_kb"):
            self._kb.move(self.search.width() - self._kb.width() - 16, (self.search.height() - self._kb.height()) // 2)
        return super().eventFilter(obj, ev)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        avail = self.width() - 334 - 96            # sidebar + page margins
        self._layout_cards(max(2, min(4, avail // 235)))

    def _swap(self):
        a, b = self.p_left.text(), self.p_right.text()
        self.p_left.setText(b)
        self.p_right.setText(a)

    def set_paths(self, left=None, right=None):
        if left is not None:
            self.p_left.setText(left)
        if right is not None:
            self.p_right.setText(right)

    def _detect(self):
        kind, label = detect(self.p_left.text(), self.p_right.text())
        r = engine.TextRules.from_config(self.cfg)
        rules = {"edges": ["Ignore leading/trailing whitespace"], "changes": ["Ignore whitespace changes"], "all": ["Ignore all whitespace"]}.get(r.ws, [])
        if r.ignore_case:
            rules.append("Ignore case")
        rules += self.cfg["ignore_patterns"]
        self.detected.setText(
            f"<span style='color:{theme.MUTED}'>Detected:</span> <b>{label}</b> &nbsp;&nbsp;&nbsp; "
            f"<span style='color:{theme.MUTED}'>Rules:</span> <b>{', '.join(rules) or 'none'}</b> &nbsp;&nbsp; "
            f"<a href='r' style='color:{theme.ACCENT}'>Edit rules</a>")

    def _go(self):
        l, r = self.p_left.text(), self.p_right.text()
        kind, label = detect(l, r)
        if kind is None:
            QMessageBox.information(self, "Compare", f"{label}. Choose two files or two folders.")
            return
        self.compareRequested.emit(kind, l, r)

    def _focus_search(self):
        self.show_page(0)
        self.search.setFocus()
        self.search.selectAll()

    # ------------------------------------------------------ other pages
    def _sessions_page(self):
        w, lay = self._page()
        lay.addLayout(self._title("Saved sessions", "Everything you compared recently, and saved sync sessions. Right-click to pin or remove."))
        lay.addSpacing(24)
        self.all_search = QLineEdit()
        self.all_search.setPlaceholderText("Filter sessions…")
        self.all_search.setFixedWidth(360)
        lay.addWidget(self.all_search)
        lay.addSpacing(12)
        self.all_table = SessionTable(self.cfg)
        self.all_table.openSession.connect(self.sessionRequested)
        self.all_table.changed.connect(self.refresh)
        self.all_search.textChanged.connect(lambda t: self.all_table.populate(t))
        self.all_table.setMinimumHeight(420)
        lay.addWidget(self.all_table, 1)
        return w

    def _rules_page(self):
        w, lay = self._page()
        lay.addLayout(self._title("Rules & file formats", "How files and text are compared. Applied to folder, text and merge sessions."))
        lay.addSpacing(24)
        c = card()
        cl = QVBoxLayout(c)
        cl.setContentsMargins(28, 24, 28, 24)
        cl.setSpacing(10)
        l = QLabel("Folder filters")
        l.setStyleSheet("border:none; font-weight:700; font-size:12pt;")
        cl.addWidget(l)
        l = QLabel("Ignore patterns (one per line – wildcards allowed, trailing / matches folders only)")
        l.setStyleSheet("border:none;")
        cl.addWidget(l)
        self.rules_edit = QPlainTextEdit("\n".join(self.cfg["ignore_patterns"]))
        self.rules_edit.setFont(theme.mono(10))
        self.rules_edit.setFixedHeight(130)
        cl.addWidget(self.rules_edit)
        t = QLabel("Text rules")
        t.setStyleSheet("border:none; font-weight:700; font-size:12pt; margin-top:10px;")
        cl.addWidget(t)
        self.rules_editor = textdialogs.RulesEditor(engine.TextRules.from_config(self.cfg))
        cl.addWidget(self.rules_editor)
        save = QPushButton("Save rules")
        save.setProperty("primary", True)
        save.setFixedWidth(160)
        save.setMinimumHeight(38)
        save.clicked.connect(self._save_rules)
        cl.addWidget(save)
        lay.addWidget(c)
        lay.addSpacing(20)

        f = card()
        fl = QVBoxLayout(f)
        fl.setContentsMargins(28, 24, 28, 24)
        fl.setSpacing(10)
        ft = QLabel("File formats")
        ft.setStyleSheet("border:none; font-weight:700; font-size:12pt;")
        fs = QLabel("Rules that depend on the file type: grammar for syntax colours, unimportant text, replacements, and converters "
                    "that turn Word, PDF or any other format into text before it is compared. Your formats win over the built-in ones.")
        fs.setWordWrap(True)
        fs.setStyleSheet(f"color:{theme.MUTED}; border:none;")
        fl.addWidget(ft)
        fl.addWidget(fs)
        self.fmt_table = QTableWidget(0, 4)
        self.fmt_table.setHorizontalHeaderLabels(["Name", "Patterns", "Conversion", "Source"])
        self.fmt_table.verticalHeader().hide()
        self.fmt_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.fmt_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.fmt_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.fmt_table.horizontalHeader().setStretchLastSection(True)
        self.fmt_table.setColumnWidth(0, 200)
        self.fmt_table.setColumnWidth(1, 240)
        self.fmt_table.setColumnWidth(2, 180)
        self.fmt_table.setFixedHeight(230)
        self.fmt_table.cellDoubleClicked.connect(lambda *_: self._edit_format())
        fl.addWidget(self.fmt_table)
        row = QHBoxLayout()
        for text, fn in (("Add format…", self._add_format), ("Edit…", self._edit_format), ("Remove", self._remove_format)):
            b = QPushButton(text)
            b.setMinimumHeight(36)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch(1)
        fl.addLayout(row)
        lay.addWidget(f)
        lay.addStretch(1)
        self._fill_formats()
        return w

    def _save_rules(self):
        r = self.rules_editor.rules()
        bad = r.invalid_patterns()
        if bad:
            QMessageBox.warning(self, "Rules", "Fix the invalid pattern first:\n" + bad[0])
            return
        self.cfg["ignore_patterns"] = [x.strip() for x in self.rules_edit.toPlainText().splitlines() if x.strip()]
        textdialogs.store_rules_in_config(self.cfg, r)
        self._detect()
        toast("Rules saved")

    def _fill_formats(self):
        user = self.cfg["file_formats"]
        t = self.fmt_table
        rows = [(f, "yours") for f in user] + [(dict(fileformats.blank_format(), **f), "built-in") for f in fileformats.BUILTIN_FORMATS]
        t.setRowCount(len(rows))
        for r, (f, src) in enumerate(rows):
            conv = {"": "–", "builtin": "Built-in text extraction", "external": "External converter"}.get(f.get("converter", ""), "–")
            for c, v in enumerate((f["name"], f["patterns"], conv, src)):
                it = QTableWidgetItem(v)
                if src == "built-in":
                    it.setForeground(Qt.gray)
                t.setItem(r, c, it)
            t.setRowHeight(r, 32)

    def _selected_user_format(self):
        r = self.fmt_table.currentRow()
        n = len(self.cfg["file_formats"])
        return r if 0 <= r < n else None

    def _add_format(self):
        dlg = FormatDialog(fileformats.blank_format(), self)
        if dlg.exec():
            self.cfg["file_formats"] = list(self.cfg["file_formats"]) + [dlg.result]
            self._fill_formats()

    def _edit_format(self):
        i = self._selected_user_format()
        if i is None:
            toast("Select one of your own formats (built-in formats can be overridden by adding a new one)", "warn")
            return
        dlg = FormatDialog(self.cfg["file_formats"][i], self)
        if dlg.exec():
            fm = list(self.cfg["file_formats"])
            fm[i] = dlg.result
            self.cfg["file_formats"] = fm
            self._fill_formats()

    def _remove_format(self):
        i = self._selected_user_format()
        if i is None:
            return
        fm = list(self.cfg["file_formats"])
        del fm[i]
        self.cfg["file_formats"] = fm
        self._fill_formats()

    # -------------------------------------------------------------- scripts
    def _scripts_page(self):
        w, lay = self._page()
        lay.addLayout(self._title("Scripts & automation", "Unattended jobs: nightly syncs, reports, snapshots. "
                                  "Run them here or with  lavati --script FILE.lcs"))
        lay.addSpacing(24)
        self.script_list = QTableWidget(0, 3)
        self.script_list.setHorizontalHeaderLabels(["Script", "Modified", ""])
        self.script_list.verticalHeader().hide()
        self.script_list.setShowGrid(False)
        self.script_list.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = self.script_list.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.Fixed)
        h.setSectionResizeMode(2, QHeaderView.Fixed)
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.script_list.setColumnWidth(1, 170)
        self.script_list.setColumnWidth(2, 330)
        self.script_list.setStyleSheet(f"QTableWidget {{ border:1px solid {theme.BORDER}; border-radius:10px; }}")
        self.script_list.setMinimumHeight(360)
        lay.addWidget(self.script_list, 1)
        row = QHBoxLayout()
        b_new = QPushButton("New script…")
        b_new.setProperty("primary", True)
        b_new.setMinimumHeight(38)
        b_new.clicked.connect(self._new_script)
        b = QPushButton("Open scripts folder")
        b.setMinimumHeight(38)
        b.clicked.connect(lambda: (os.makedirs(config.SCRIPTS_DIR, exist_ok=True),
                                   QDesktopServices.openUrl(QUrl.fromLocalFile(config.SCRIPTS_DIR))))
        row.addWidget(b_new)
        row.addWidget(b)
        row.addStretch(1)
        lay.addSpacing(12)
        lay.addLayout(row)
        return w

    def _fill_scripts(self):
        os.makedirs(config.SCRIPTS_DIR, exist_ok=True)
        files = sorted((f for f in os.listdir(config.SCRIPTS_DIR) if f.lower().endswith((".lcs", ".bat", ".cmd", ".ps1", ".sh"))),
                       key=lambda f: -os.path.getmtime(os.path.join(config.SCRIPTS_DIR, f)))
        t = self.script_list
        t.setRowCount(len(files))
        for r, f in enumerate(files):
            p = os.path.join(config.SCRIPTS_DIR, f)
            t.setRowHeight(r, 46)
            t.setItem(r, 0, QTableWidgetItem(f))
            t.setItem(r, 1, QTableWidgetItem(time.strftime("%Y-%m-%d %H:%M", time.localtime(os.path.getmtime(p)))))
            w = QWidget()
            wl = QHBoxLayout(w)
            wl.setContentsMargins(6, 4, 6, 4)
            is_lcs = f.lower().endswith(".lcs")
            run = QPushButton("Run")
            run.setProperty("primary", True)
            ed = QPushButton("Edit")
            sch = QPushButton("Schedule…")
            de = QPushButton("Delete")
            run.clicked.connect(lambda _=False, p=p: self._run_script(p))
            ed.clicked.connect(lambda _=False, p=p: self._edit_script(p))
            sch.clicked.connect(lambda _=False, p=p: self._schedule(p))
            de.clicked.connect(lambda _=False, p=p: self._delete_script(p))
            for x in (run, ed, sch, de):
                wl.addWidget(x)
            sch.setEnabled(is_lcs)
            t.setCellWidget(r, 2, w)

    def _new_script(self):
        name, ok = QInputDialog.getText(self, "New script", "File name:", text="nightly.lcs")
        if not ok or not name.strip():
            return
        name = name.strip()
        if not name.lower().endswith(".lcs"):
            name += ".lcs"
        os.makedirs(config.SCRIPTS_DIR, exist_ok=True)
        p = os.path.join(config.SCRIPTS_DIR, os.path.basename(name))
        if not os.path.exists(p):
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                f.write(script.TEMPLATE)
        self._fill_scripts()
        self._edit_script(p)

    def _edit_script(self, p):
        if p.lower().endswith(".lcs"):
            ScriptEditor(p, self).exec()
            self._fill_scripts()
        else:
            platform_utils.edit_file(p)

    def _delete_script(self, p):
        if QMessageBox.question(self, "Delete", f"Delete {os.path.basename(p)}?") == QMessageBox.Yes:
            os.remove(p)
            self._fill_scripts()

    def _schedule(self, p):
        cmd = platform_utils.schedule_command(p)
        dlg = QDialog(self)
        dlg.setWindowTitle("Run every night")
        dlg.setMinimumWidth(640)
        lay = QVBoxLayout(dlg)
        lay.addWidget(QLabel("Run this in a terminal to schedule the script every night at 02:00 "
                             "(change the time as you like):"))
        box = QPlainTextEdit(cmd)
        box.setReadOnly(True)
        box.setFont(theme.mono(9))
        box.setFixedHeight(100)
        lay.addWidget(box)
        row = QHBoxLayout()
        b = QPushButton("Copy command")
        b.clicked.connect(lambda: QGuiApplication.clipboard().setText(cmd))
        c = QPushButton("Close")
        c.clicked.connect(dlg.accept)
        row.addStretch(1)
        row.addWidget(b)
        row.addWidget(c)
        lay.addLayout(row)
        dlg.exec()

    def _run_script(self, p):
        if p.lower().endswith(".lcs"):
            dlg = ScriptEditor(p, self)
            dlg.run()
            dlg.exec()
            return
        if QMessageBox.question(self, "Run script", f"Run {os.path.basename(p)}?") != QMessageBox.Yes:
            return
        try:
            platform_utils.run_script(p)
        except OSError as e:
            QMessageBox.warning(self, "Run script", str(e))

    # ------------------------------------------------------------- settings
    def _settings_page(self):
        w, lay = self._page()
        lay.addLayout(self._title("Settings", "Defaults for sync, appearance and integration."))
        lay.addSpacing(24)
        c = card()
        cl = QVBoxLayout(c)
        cl.setContentsMargins(28, 24, 28, 24)
        cl.setSpacing(12)
        self.s_backup = QCheckBox("Back up overwritten and deleted files by default")
        self.s_recycle = QCheckBox("Use Recycle Bin / Trash for deletes by default")
        self.s_restore = QCheckBox("Reopen the tabs that were open when I last closed the app")
        for cb, key in ((self.s_backup, "backup_overwritten"), (self.s_recycle, "use_recycle_bin"), (self.s_restore, "restore_tabs")):
            cb.setChecked(bool(self.cfg[key]))
            cb.setStyleSheet("border:none;")
            cb.toggled.connect(lambda on, k=key: self.cfg.__setitem__(k, on))
            cl.addWidget(cb)
        lay.addWidget(c)
        lay.addSpacing(20)
        ap = card()
        al = QVBoxLayout(ap)
        al.setContentsMargins(28, 24, 28, 24)
        al.setSpacing(12)
        at = QLabel("Appearance")
        at.setStyleSheet("font-weight:700; font-size:12pt; border:none;")
        al.addWidget(at)
        row = QHBoxLayout()
        row.setSpacing(12)
        lab1 = QLabel("Theme")
        lab1.setStyleSheet("border:none;")
        self.s_theme = QComboBox()
        self.s_theme.addItem("Light", "light")
        self.s_theme.addItem("Dark", "dark")
        self.s_theme.addItem("Follow the system", "system")
        self.s_theme.setCurrentIndex(max(0, self.s_theme.findData(self.cfg["theme"])))
        self.s_theme.setFixedWidth(180)
        lab2 = QLabel("Font size")
        lab2.setStyleSheet("border:none;")
        self.s_font = QComboBox()
        self.s_font.addItems([f"{n} pt" for n in range(9, 15)])
        cur = max(9, min(14, int(self.cfg["font_size"])))
        self.s_font.setCurrentIndex(cur - 9)
        self.s_font.setFixedWidth(100)
        apply_btn = QPushButton("Apply && restart")
        apply_btn.setProperty("primary", True)
        apply_btn.setMinimumHeight(38)
        apply_btn.clicked.connect(self._apply_appearance)
        for w_ in (lab1, self.s_theme, lab2, self.s_font):
            row.addWidget(w_)
        row.addSpacing(10)
        row.addWidget(apply_btn)
        row.addStretch(1)
        al.addLayout(row)
        lay.addWidget(ap)
        lay.addSpacing(20)
        ex = card()
        el = QVBoxLayout(ex)
        el.setContentsMargins(28, 24, 28, 24)
        el.setSpacing(10)
        et = QLabel(shell.title())
        et.setStyleSheet("font-weight:700; font-size:12pt; border:none;")
        ed = QLabel(shell.description())
        ed.setWordWrap(True)
        ed.setStyleSheet(f"color:{theme.MUTED}; border:none;")
        self.shell_state = QLabel()
        self.shell_state.setStyleSheet("border:none; font-weight:600;")
        btns = QHBoxLayout()
        self.b_reg = QPushButton("Add to the menu")
        self.b_unreg = QPushButton("Remove")
        self.b_reg.clicked.connect(lambda: self._shell(True))
        self.b_unreg.clicked.connect(lambda: self._shell(False))
        for b in (self.b_reg, self.b_unreg):
            b.setMinimumHeight(38)
            btns.addWidget(b)
        btns.addStretch(1)
        el.addWidget(et)
        el.addWidget(ed)
        el.addWidget(self.shell_state)
        el.addLayout(btns)
        lay.addWidget(ex)
        self._shell_refresh()
        lay.addSpacing(20)
        g = card()
        gl = QVBoxLayout(g)
        gl.setContentsMargins(28, 24, 28, 24)
        gl.setSpacing(8)
        t = QLabel("Git integration")
        t.setStyleSheet("font-weight:700; font-size:12pt; border:none;")
        gl.addWidget(t)
        self.git_launch = " ".join('"' + p.replace(chr(92), "/") + '"' for p in platform_utils.app_launcher())
        self.git_cmd = "\n".join(f"git config --global {k} '{v}'" for k, v in gitvfs.config_commands(self.git_launch))
        box = QPlainTextEdit(self.git_cmd)
        box.setReadOnly(True)
        box.setFont(theme.mono(9))
        box.setFixedHeight(150)
        gl.addWidget(box)
        hint = QLabel("Then use  git difftool [--dir-diff]  and  git mergetool.  A merge exits with a failure code unless the merged file was saved, so Git never accepts an unresolved merge.")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color:{theme.MUTED}; border:none;")
        gl.addWidget(hint)
        row = QHBoxLayout()
        self.git_state = QLabel()
        self.git_state.setStyleSheet("border:none; font-weight:600;")
        cfgbtn = QPushButton("Configure Git now")
        cfgbtn.setProperty("primary", True)
        cfgbtn.setMinimumHeight(38)
        cfgbtn.clicked.connect(self._configure_git)
        self.b_getgit = QPushButton("Download Git")
        self.b_getgit.setMinimumHeight(38)
        self.b_getgit.clicked.connect(self._download_git)
        cp = QPushButton("Copy commands")
        cp.setMinimumHeight(38)
        cp.clicked.connect(lambda: QGuiApplication.clipboard().setText(self.git_cmd))
        row.addWidget(cfgbtn)
        row.addWidget(self.b_getgit)
        row.addWidget(cp)
        row.addSpacing(12)
        row.addWidget(self.git_state, 1)
        gl.addLayout(row)
        self._git_refresh()
        lay.addWidget(g)
        lay.addStretch(1)
        return w

    def _download_git(self):
        from .git_view import download_git_with_progress
        if download_git_with_progress(self):
            toast("Git downloaded")
        self._git_refresh()

    def _git_refresh(self):
        msg = gitvfs.check_git()
        self.b_getgit.setVisible(bool(msg) and gitvfs.can_download_git())
        if msg:
            self.git_state.setText(msg)
            self.git_state.setStyleSheet(f"border:none; font-weight:600; color:{theme.RED};")
        elif gitvfs.is_configured():
            self.git_state.setText("\u25cf Configured as difftool and mergetool")
            self.git_state.setStyleSheet(f"border:none; font-weight:600; color:{theme.GREEN};")
        else:
            self.git_state.setText("\u25cb Not configured yet")
            self.git_state.setStyleSheet(f"border:none; font-weight:600; color:{theme.MUTED};")

    def _configure_git(self):
        if QMessageBox.question(self, "Configure Git", "Write the difftool / mergetool settings to your global Git config "
                                "(~/.gitconfig)?\n\nIt sets diff.tool, merge.tool and the lavati tool commands.") != QMessageBox.Yes:
            return
        try:
            keys = gitvfs.configure_global(self.git_launch)
        except gitvfs.GitError as e:
            QMessageBox.warning(self, "Git", str(e))
        else:
            toast(f"Git configured ({len(keys)} settings written)")
        self._git_refresh()

    def _apply_appearance(self):
        self.cfg["theme"] = self.s_theme.currentData()
        self.cfg["font_size"] = self.s_font.currentIndex() + 9
        win = self.window()
        if hasattr(win, "restart"):
            win.restart()

    def _shell(self, on):
        try:
            shell.register() if on else shell.unregister()
            toast("Context menu entries " + ("added" if on else "removed"))
        except (OSError, shell.ShellError) as e:
            QMessageBox.warning(self, "File manager integration", str(e))
        self._shell_refresh()

    def _shell_refresh(self):
        supported = shell.is_supported()
        on = supported and shell.is_registered()
        self.shell_state.setText("● Enabled" if on else ("○ Not enabled" if supported else "○ Not available on this system"))
        self.shell_state.setStyleSheet(f"border:none; font-weight:600; color:{theme.GREEN if on else theme.MUTED};")
        self.b_reg.setEnabled(supported and not on)
        self.b_unreg.setEnabled(on)

    # ----------------------------------------------------------- refresh
    def refresh(self):
        self.recent.populate(self.search.text())
        self.all_table.populate(self.all_search.text())
        self._fill_scripts()
        while self.pin_box.count():
            it = self.pin_box.takeAt(0)
            w = it.widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        pins = [s for s in self.cfg["sessions"] if s.get("pinned")]
        if not pins:
            l = QLabel("Right-click a recent session to pin it")
            l.setWordWrap(True)
            l.setStyleSheet(f"color:{theme.MUTED}; margin-left:14px; margin-top:8px; background:transparent; border:none;")
            self.pin_box.addWidget(l)
        for s in pins:
            b = QPushButton(s["name"])
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet(f"QPushButton {{ text-align:left; border:none; background:transparent; padding:10px 14px; "
                            f"border-radius:8px; }} QPushButton:hover {{ background:{theme.HOVER}; }}")
            b.clicked.connect(lambda _=False, s=s: self.sessionRequested.emit(s))
            self.pin_box.addWidget(b)
        self._detect()
