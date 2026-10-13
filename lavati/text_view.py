"""Text comparison: side-by-side or over-under, character-level highlights, live editing."""
import html
import math
import os
import re
import time

from PySide6.QtCore import QTimer, Qt, Signal
from PySide6.QtGui import QActionGroup, QColor, QKeySequence, QShortcut
from PySide6.QtWidgets import (QScrollArea, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMenu, QMessageBox, QPlainTextEdit, QPushButton,
                               QSpinBox, QSplitter, QToolButton, QVBoxLayout, QWidget)

from . import engine, fileformats, highlight, platform_utils, theme
from .workers import run_responsive
from .editor import CopyGutter, EditorPane, ERow, MiniMap
from .textdialogs import PatchDialog, RulesDialog, store_rules_in_config
from .widgets import Pill, Segmented, ToolbarAdapter, sty, toast, tool_button, vsep

SYNTAX_LIMIT_LINES = 80000
ENC_LABELS = list(engine.ENCODINGS)


class EditDialog(QDialog):
    """Small modal text editor (used for manual conflict resolution)."""

    def __init__(self, title, lines, parent=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(900, 640)
        lay = QVBoxLayout(self)
        self.edit = QPlainTextEdit("\n".join(lines))
        self.edit.setFont(theme.mono(10))
        self.edit.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(self.edit)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        lay.addWidget(bb)

    def lines(self):
        return self.edit.toPlainText().split("\n")


class Side:
    """Everything we know about one side's file besides its text (which lives in the pane)."""

    def __init__(self, key, path):
        self.key, self.path = key, path
        self.title = ""                 # friendly name (e.g. from git) shown instead of a temp file path
        self.enc, self.eol, self.mixed = "UTF-8", "LF", False
        self.mtime = None
        self.readonly = False
        self.fmt = None
        self.note = ""
        self.final_nl = True
        self.meta_dirty = False
        self.forced_enc = None
        self.gen = 0
        self.hl_gen = -1


class SideHeader(QFrame):
    metaMenu = Signal(str)
    pathMenu = Signal(str)

    def __init__(self, key):
        super().__init__()
        self.key = key
        sty(self, f"background:{theme.PANEL}; border-bottom:1px solid {theme.BORDER};")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 6, 8, 6)
        lay.setSpacing(8)
        self.tag = QLabel("LEFT" if key == "L" else "RIGHT")
        self.tag.setStyleSheet(f"color:{theme.MUTED}; font-weight:700; font-size:8pt; letter-spacing:1px;")
        self.path = QLabel()
        self.path.setFont(theme.mono(10))
        self.path.setMinimumWidth(40)
        self.meta = QToolButton()
        self.meta.setAutoRaise(True)
        self.meta.setCursor(Qt.PointingHandCursor)
        self.meta.setPopupMode(QToolButton.InstantPopup)
        self.meta.setStyleSheet(f"QToolButton {{ color:{theme.MUTED}; font-size:9pt; padding:2px 14px 2px 6px; }}")
        self.badge = Pill("Edited", theme.AMBER, theme.CHIP_AMBER)
        self.ro = Pill("Read-only", theme.MUTED, theme.CHIP_GRAY)
        self.mixed = Pill("Mixed line endings", theme.RED, theme.RED_BG)
        for b in (self.badge, self.ro, self.mixed):
            b.hide()
        lay.addWidget(self.tag)
        lay.addWidget(self.path, 1)
        lay.addWidget(self.mixed)
        lay.addWidget(self.ro)
        lay.addWidget(self.badge)
        lay.addWidget(self.meta)


class TextCompareView(QWidget):
    resultChanged = Signal(str)

    def __init__(self, cfg, left_path, right_path, parent=None, extra=None):
        super().__init__(parent)
        self.cfg = cfg
        self.sides = {"L": Side("L", left_path), "R": Side("R", right_path)}
        self.rules_base = engine.TextRules.from_config(cfg)
        self.rules = self.rules_base.copy()
        self.rules_pinned = False           # user edited the rules for this tab
        self.syntax = True
        self.wrap = bool(cfg["word_wrap"])
        self.layout_mode = cfg["text_layout"]
        self.hl_level = "char"
        self.show_details = True
        self.lang_override = None
        self.mode = 0
        self.context = 3
        self.anchors = []                    # [(left line, right line)] forced alignments
        self.cur = -1                        # current display row
        self.cur_hunk = -1
        self.pairs, self.hunks, self.disp = [], [], []
        self.hunk_first, self.hunk_last = {}, {}
        self._span_cache = {}
        self._focus = "L"
        self._lang = None
        self._lang_key = None
        self._syncing = False
        self.panes = {}
        self.headers = {}
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._rebuild)
        self._ui()
        self._load_all()
        self._rebuild()
        if self.hunks:
            self.goto_hunk(0)

    # ================================================================ UI
    def _ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        bar = QFrame()
        sty(bar, f"background:{theme.SURFACE}; border-bottom:1px solid {theme.BORDER};")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 6, 12, 6)
        bl.setSpacing(3)
        self.seg = Segmented(["All lines", "Diffs + context", "Diffs only"])
        self.seg.changed.connect(self._set_mode)
        bl.addWidget(self.seg)
        self.ctx_spin = QSpinBox()
        self.ctx_spin.setRange(0, 99)
        self.ctx_spin.setValue(self.context)
        self.ctx_spin.setPrefix("±")
        self.ctx_spin.setSuffix(" lines")
        self.ctx_spin.setToolTip("Lines of context shown around each difference (type a number or scroll the mouse wheel)")
        self.ctx_spin.setFixedWidth(104)
        self.ctx_spin.setButtonSymbols(QSpinBox.NoButtons)
        self.ctx_spin.valueChanged.connect(self._set_context)
        bl.addWidget(self.ctx_spin)
        bl.addSpacing(4)
        self.b_prev = tool_button("Prev", "up", "Previous difference (F7)")
        self.b_next = tool_button("Next", "down", "Next difference (F8)")
        self.b_prev.clicked.connect(lambda: self.step(-1))
        self.b_next.clicked.connect(lambda: self.step(1))
        bl.addWidget(self.b_prev)
        bl.addWidget(self.b_next)
        bl.addWidget(vsep())
        self.b_tl = tool_button("To right", "right", "Copy the current difference from left to right (Alt+Right)")
        self.b_tr = tool_button("To left", "left", "Copy the current difference from right to left (Alt+Left)")
        self.b_tl.clicked.connect(lambda: self.take("right"))
        self.b_tr.clicked.connect(lambda: self.take("left"))
        bl.addWidget(self.b_tl)
        bl.addWidget(self.b_tr)
        self.b_undo = tool_button("Undo", "undo", "Undo in the active pane (Ctrl+Z)")
        self.b_redo = tool_button("Redo", "redo", "Redo in the active pane (Ctrl+Y)", icon_only=True)
        self.b_undo.clicked.connect(self.undo)
        self.b_redo.clicked.connect(self.redo)
        bl.addWidget(self.b_undo)
        bl.addWidget(self.b_redo)
        bl.addWidget(vsep())
        self.b_rules = tool_button("Rules", "rules", "Whitespace, case, line-ending and unimportant-text rules")
        self.b_rules.clicked.connect(self.edit_rules)
        bl.addWidget(self.b_rules)
        self.b_view = tool_button("View", "layout_side", "Layout, word wrap, syntax colours, highlighting")
        self.b_view.setPopupMode(QToolButton.InstantPopup)
        self.b_view.setMenu(self._view_menu())
        bl.addWidget(self.b_view)
        self.b_align = tool_button("Align", "align", "Manually align two lines")
        self.b_align.setPopupMode(QToolButton.InstantPopup)
        m = QMenu(self.b_align)
        m.addAction("Align the cursor lines (left with right)", self.align_cursors)
        m.addAction("Clear manual alignments", self.clear_alignments)
        self.b_align.setMenu(m)
        bl.addWidget(self.b_align)
        self.b_patch = tool_button("Patch", "patch", "Show the differences as a unified diff")
        self.b_patch.clicked.connect(self.show_patch)
        self.b_find = tool_button("Find", "search", "Find in both files (Ctrl+F)", icon_only=True)
        self.b_find.clicked.connect(self.find)
        self.b_report = tool_button("Report", "report", "Save a difference report (HTML, text or XML)", icon_only=True)
        self.b_report.clicked.connect(self.report)
        bl.addWidget(self.b_patch)
        bl.addWidget(self.b_find)
        bl.addWidget(self.b_report)
        bl.addWidget(vsep())
        self.b_save = tool_button("Save", "save", "Save modified files (Ctrl+S)")
        self.b_save.clicked.connect(self.save)
        self.b_swap = tool_button("Swap", "swap", "Swap left and right (Ctrl+Shift+X)", icon_only=True)
        self.b_swap.clicked.connect(self.swap_sides)
        self.b_reload = tool_button("Reload", "refresh", "Reload both files from disk (F5)", icon_only=True)
        self.b_reload.clicked.connect(self.reload)
        for b in (self.b_save, self.b_swap, self.b_reload):
            bl.addWidget(b)
        bl.addStretch(1)
        self.stats = QLabel()
        self.stats.setTextFormat(Qt.RichText)
        self.bar_adapter = ToolbarAdapter(bar)
        root.addWidget(bar)

        # find bar
        self.findbar = QFrame()
        sty(self.findbar, f"background:{theme.PANEL}; border-bottom:1px solid {theme.BORDER};")
        fl = QHBoxLayout(self.findbar)
        fl.setContentsMargins(14, 6, 14, 6)
        self.find_edit = QLineEdit()
        self.find_edit.setPlaceholderText("Find in both files")
        self.find_edit.setFixedWidth(320)
        self.find_edit.textChanged.connect(self._find_changed)
        self.find_edit.returnPressed.connect(lambda: self.find_next(1))
        self.find_case = QCheckBox("Match case")
        self.find_case.toggled.connect(self._find_changed)
        self.find_info = QLabel()
        self.find_info.setStyleSheet(f"color:{theme.MUTED};")
        bp, bn, bc = QPushButton("Previous"), QPushButton("Next"), QPushButton("Close")
        bp.clicked.connect(lambda: self.find_next(-1))
        bn.clicked.connect(lambda: self.find_next(1))
        bc.clicked.connect(self.close_find)
        for w in (self.find_edit, bp, bn, self.find_case, self.find_info):
            fl.addWidget(w)
        fl.addStretch(1)
        fl.addWidget(bc)
        self.findbar.hide()
        root.addWidget(self.findbar)

        # panes
        for k in ("L", "R"):
            self.headers[k] = SideHeader(k)
            self.headers[k].meta.setMenu(self._meta_menu(k))
            pane = EditorPane()
            pane.wrap = self.wrap
            pane.rowClicked.connect(lambda r: self._row_clicked(r))
            pane.cursorMoved.connect(lambda li, col, k=k: self._cursor_moved(k, li, col))
            pane.edited.connect(lambda k=k: self._on_edited(k))
            pane.spliced.connect(lambda a, o, n, k=k: self._on_spliced(k, a, o, n))
            pane.focusedIn.connect(lambda k=k: self._set_focus(k))
            pane.contextRequested.connect(lambda r, pos, k=k: self._context_menu(k, r, pos))
            pane.resized.connect(self._maybe_rewrap)
            self.panes[k] = pane
        self.panes["L"].setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)       # the right pane's scrollbar drives both
        self.gutter = CopyGutter(self.panes["L"])
        self.gutter.copyRequested.connect(self._gutter_copy)
        self.mm = MiniMap(self.panes["R"])
        self.mm.jump.connect(lambda r: (self.panes["L"].ensure_visible_row(r, True), self.panes["R"].ensure_visible_row(r, True)))
        self.wraps = {}
        for k in ("L", "R"):
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.setSpacing(0)
            v.addWidget(self.headers[k])
            v.addWidget(self.panes[k], 1)
            self.wraps[k] = w
        self.split = QSplitter(Qt.Horizontal)
        self.split.setHandleWidth(1)
        self.split.setChildrenCollapsible(False)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        body.addWidget(self.split, 1)
        body.addWidget(self.mm)
        bodyw = QWidget()
        bodyw.setLayout(body)
        # the line-details panel sits under a splitter handle so its height can be dragged
        self.vsplit = QSplitter(Qt.Vertical)
        self.vsplit.setHandleWidth(7)
        self.vsplit.setChildrenCollapsible(False)
        self.vsplit.setStyleSheet(f"QSplitter::handle {{ background:{theme.BORDER}; }} QSplitter::handle:hover {{ background:{theme.ACCENT}; }}")
        self.vsplit.addWidget(bodyw)
        root.addWidget(self.vsplit, 1)
        self._apply_layout()
        for a, b in (("L", "R"), ("R", "L")):
            self.panes[a].verticalScrollBar().valueChanged.connect(
                lambda v, b=b: self.panes[b].verticalScrollBar().setValue(v))
            self.panes[a].horizontalScrollBar().valueChanged.connect(
                lambda v, b=b: self._hsync(b, v))

        # line detail panel
        self.det = QFrame()
        sty(self.det, f"background:{theme.SURFACE}; border-top:1px solid {theme.BORDER};")
        dl = QVBoxLayout(self.det)
        dl.setContentsMargins(16, 8, 16, 8)
        dl.setSpacing(2)
        self.dh = QLabel("")
        self.dh.setStyleSheet(f"color:{theme.MUTED}; font-weight:600; font-size:9pt;")
        self.dl = QLabel()
        self.dr = QLabel()
        for l in (self.dl, self.dr):
            l.setTextFormat(Qt.RichText)
            l.setFont(theme.mono(10))
            l.setTextInteractionFlags(Qt.TextSelectableByMouse)
        dl.addWidget(self.dh)
        dl.addWidget(self.dl)
        dl.addWidget(self.dr)
        dl.addStretch(1)
        self.det_scroll = QScrollArea()
        self.det_scroll.setWidgetResizable(True)
        self.det_scroll.setFrameShape(QFrame.NoFrame)
        self.det_scroll.setWidget(self.det)
        self.det_scroll.setMinimumHeight(46)

        self.vsplit.addWidget(self.det_scroll)
        self.vsplit.setStretchFactor(0, 1)
        self.vsplit.setStretchFactor(1, 0)
        self.vsplit.setSizes([700, 110])
        st = QFrame()
        sty(st, f"background:{theme.TABBAR};")
        sl = QHBoxLayout(st)
        sl.setContentsMargins(20, 4, 20, 4)
        sl.setSpacing(14)
        self.s_pos = QLabel()
        self.s_rules = QLabel()
        self.s_hunk = QLabel()
        self.s_note = QLabel()
        for l in (self.s_pos, self.s_rules, self.s_hunk, self.s_note):
            l.setStyleSheet(f"color:{theme.MUTED}; font-size:9pt;")
        self.lang_combo = QComboBox()
        self.lang_combo.addItems(["Auto"] + highlight.LANG_NAMES)
        self.lang_combo.setFixedWidth(130)
        self.lang_combo.setToolTip("Syntax highlighting language")
        self.lang_combo.currentTextChanged.connect(self._lang_changed)
        sl.addWidget(self.s_pos)
        sl.addWidget(self.lang_combo)
        sl.addWidget(self.s_rules)
        sl.addWidget(self.s_note)
        sl.addStretch(1)
        sl.addWidget(self.stats)
        sl.addSpacing(10)
        sl.addWidget(self.s_hunk)
        root.addWidget(st)

        QShortcut(QKeySequence("F3"), self, activated=lambda: self.find_next(1))
        QShortcut(QKeySequence("Shift+F3"), self, activated=lambda: self.find_next(-1))
        QShortcut(QKeySequence("Alt+Down"), self, activated=lambda: self.step(1))
        QShortcut(QKeySequence("Alt+Up"), self, activated=lambda: self.step(-1))
        QShortcut(QKeySequence("Alt+Right"), self, activated=lambda: self.take("right"))
        QShortcut(QKeySequence("Alt+Left"), self, activated=lambda: self.take("left"))
        QShortcut(QKeySequence("Escape"), self.findbar, activated=self.close_find, context=Qt.WidgetWithChildrenShortcut)

    def _view_menu(self):
        m = QMenu(self)
        self.act_wrap = m.addAction("Word wrap")
        self.act_wrap.setCheckable(True)
        self.act_wrap.setChecked(self.wrap)
        self.act_wrap.toggled.connect(self.set_wrap)
        self.act_syn = m.addAction("Syntax colours")
        self.act_syn.setCheckable(True)
        self.act_syn.setChecked(self.syntax)
        self.act_syn.toggled.connect(self._toggle_syn)
        self.act_det = m.addAction("Line details panel")
        self.act_det.setCheckable(True)
        self.act_det.setChecked(True)
        self.act_det.toggled.connect(self._toggle_details)
        m.addSeparator()
        lm = m.addMenu("Layout")
        g = QActionGroup(lm)
        for key, label in (("side", "Side by side"), ("over", "Over / under")):
            a = lm.addAction(label)
            a.setCheckable(True)
            a.setChecked(self.layout_mode == key)
            g.addAction(a)
            a.triggered.connect(lambda _=False, key=key: self.set_layout(key))
        hm = m.addMenu("Highlight differences within a line")
        g2 = QActionGroup(hm)
        for key, label in (("line", "Whole line only"), ("word", "Changed words"), ("char", "Changed characters")):
            a = hm.addAction(label)
            a.setCheckable(True)
            a.setChecked(self.hl_level == key)
            g2.addAction(a)
            a.triggered.connect(lambda _=False, key=key: self.set_hl_level(key))
        return m

    def _meta_menu(self, k):
        m = QMenu(self)
        m.aboutToShow.connect(lambda m=m, k=k: self._fill_meta_menu(m, k))
        return m

    def _fill_meta_menu(self, m, k):
        m.clear()
        s = self.sides[k]
        rl = m.addMenu("Reload with encoding")
        for lab in ENC_LABELS:
            a = rl.addAction(lab + ("   ✓" if lab == s.enc else ""))
            a.triggered.connect(lambda _=False, lab=lab, k=k: self.reload_with_encoding(k, lab))
        sv = m.addMenu("Save with encoding")
        for lab in ENC_LABELS:
            a = sv.addAction(lab + ("   ✓" if lab == s.enc else ""))
            a.triggered.connect(lambda _=False, lab=lab, k=k: self.set_save_encoding(k, lab))
        eo = m.addMenu("Line endings")
        for lab, name in (("CRLF", "Windows (CRLF)"), ("LF", "Unix (LF)"), ("CR", "Classic Mac (CR)")):
            a = eo.addAction(name + ("   ✓" if lab == s.eol and not s.mixed else ""))
            a.triggered.connect(lambda _=False, lab=lab, k=k: self.set_eol(k, lab))
        m.addSeparator()
        m.addAction("Show in file manager", lambda: platform_utils.reveal(s.path)).setEnabled(bool(s.path) and os.path.exists(s.path))
        m.addAction("Copy path", lambda: __import__("PySide6.QtGui", fromlist=["QGuiApplication"]).QGuiApplication.clipboard().setText(s.path))

    # ============================================================ loading
    def _load_all(self):
        for k in ("L", "R"):
            self._load_side(k)
        self._update_rules()
        self.sides["L"].hl_gen = self.sides["R"].hl_gen = -1

    def _load_side(self, k, encoding=None):
        s = self.sides[k]
        try:
            big = bool(s.path) and os.path.exists(s.path) and os.path.getsize(s.path) > 8 * 1024 * 1024
            if big:
                pr = run_responsive(fileformats.prepare, s.path, self.cfg, encoding or s.forced_enc)
            else:
                pr = fileformats.prepare(s.path, self.cfg, encoding or s.forced_enc)
        except Exception as e:
            pr = fileformats.Prepared([], "UTF-8", "LF", False, None, True, None, f"Cannot read: {e}")
            toast(f"{os.path.basename(s.path)}: {e}", "error")
        s.enc, s.eol, s.mixed, s.mtime = pr.encoding, pr.eol, pr.mixed_eol, pr.mtime
        s.readonly, s.fmt, s.note, s.final_nl = pr.readonly, pr.fmt, pr.note, pr.final_newline
        s.meta_dirty = False
        pane = self.panes[k]
        pane.set_lines(pr.lines)
        pane.readonly = s.readonly
        s.gen += 1

    def _update_rules(self):
        if self.rules_pinned:
            return
        r = fileformats.rules_for(self.rules_base, self.sides["L"].fmt)
        rf = self.sides["R"].fmt
        if rf and rf is not self.sides["L"].fmt and rf.get("name") != (self.sides["L"].fmt or {}).get("name"):
            r = fileformats.rules_for(r, rf)
        self.rules = r

    def language(self):
        path = self.sides["L"].path or self.sides["R"].path
        fmt_lang = ""
        for k in ("L", "R"):
            f = self.sides[k].fmt
            if f and f.get("language"):
                fmt_lang = f["language"]
                break
        return highlight.language_for(path, self.lang_override or fmt_lang or None)

    # ============================================================ model
    def _rebuild(self):
        self._timer.stop()
        pl, pr_ = self.panes["L"], self.panes["R"]
        L, R = pl.lines, pr_.lines
        rules = self.rules
        tags = ("", "")
        if not rules.ignore_eol and self.sides["L"].eol != self.sides["R"].eol:
            tags = ("\0" + self.sides["L"].eol, "\0" + self.sides["R"].eol)
        if len(L) + len(R) > 200_000:                  # big files: keep the window alive while diffing
            ops = run_responsive(engine.diff_opcodes, L, R, False, False, rules, self.anchors, tags)
        else:
            ops = engine.diff_opcodes(L, R, rules=rules, anchors=self.anchors, eol_tags=tags)
        self.pairs, self.hunks = engine.text_pairs(L, R, ops, rules)
        n = len(self.pairs)
        show = [True] * n
        if self.mode:
            ctx = self.context if self.mode == 1 else 0
            show = [False] * n
            for i, p in enumerate(self.pairs):
                if p[2] not in ("eq", "unimp"):
                    for k in range(max(0, i - ctx), min(n, i + ctx + 1)):
                        show[k] = True
        self.disp = []
        prev_shown = True
        for i in range(n):
            if show[i]:
                if not prev_shown and self.disp:
                    self.disp.append(-1)
                self.disp.append(i)
            prev_shown = show[i]

        added = removed = modified = unimp = 0
        lrows, rrows, mm = [], [], []
        self.hunk_first, self.hunk_last = {}, {}
        c_del, c_add = QColor(theme.DIFF_DEL_BG), QColor(theme.DIFF_ADD_BG)
        c_sep, c_unimp = QColor(theme.LINE_SOFT), QColor(theme.SURFACE3)
        for d, pi in enumerate(self.disp):
            if pi < 0:
                lrows.append(ERow(None, "sep", bg=c_sep))
                rrows.append(ERow(None, "sep", bg=c_sep))
                continue
            li, ri, kind, h = self.pairs[pi]
            lr, rr = ERow(li, kind, h), ERow(ri, kind, h)
            if kind == "mod":
                lr.bg, lr.sign, rr.bg, rr.sign = c_del, "-", c_add, "+"
                modified += 1
            elif kind == "del":
                lr.bg, lr.sign = c_del, "-"
                rr.hatched = True
                removed += 1
            elif kind == "ins":
                rr.bg, rr.sign = c_add, "+"
                lr.hatched = True
                added += 1
            elif kind == "unimp":
                lr.bg = rr.bg = c_unimp
                unimp += 1
            lrows.append(lr)
            rrows.append(rr)
            if kind not in ("eq", "unimp"):
                self.hunk_first.setdefault(h, d)
                self.hunk_last[h] = d
                mm.append((d, {"del": theme.RED, "ins": "#2e9e55", "mod": "#d89a2b"}[kind]))

        # syntax colours
        lang = self.language()
        lang_key = lang.name if self.syntax else None
        for k, pane in self.panes.items():
            s = self.sides[k]
            if lang_key is None or len(pane.lines) > SYNTAX_LIMIT_LINES:
                pane.marks = []
                s.hl_gen = -1
            elif s.hl_gen != s.gen or self._lang_key != lang_key:
                pane.marks = highlight.highlight(pane.lines, lang)
                s.hl_gen = s.gen
        self._lang_key = lang_key

        heights = self._heights(lrows, L, R)
        self._span_cache = {}
        pl.span_provider = lambda r: self._spans("L", r)
        pr_.span_provider = lambda r: self._spans("R", r)
        pl.set_rows(lrows, heights)
        pr_.set_rows(rrows, heights)
        self.gutter.hunk_rows = dict(self.hunk_first) if self.layout_mode == "side" else {}
        self.gutter.update()
        self.mm.marks = mm
        self.mm.update()
        self._show_current_hunk()
        self.stats.setText(
            f"<span style='color:{theme.MUTED}'><b style='color:{theme.TEXT}'>{len(self.hunks)}</b> hunks</span>"
            f" &nbsp; <b style='color:{theme.GREEN}'>+{added} added</b>"
            f" &nbsp; <b style='color:{theme.RED}'>&minus;{removed} removed</b>"
            f" &nbsp; <b style='color:{theme.AMBER}'>~{modified} modified</b>"
            + (f" &nbsp; <span style='color:{theme.MUTED}'>{unimp} unimportant</span>" if unimp else ""))
        self._refresh_headers()
        self.s_rules.setText("Rules: " + self._rules_summary())
        self.s_note.setText(" · ".join(x for x in (self.sides["L"].note, self.sides["R"].note) if x))
        self.resultChanged.emit(f"{len(self.hunks)} hunks" if self.hunks else "identical")
        self._update_detail()
        self._update_buttons()

    def _rules_summary(self):
        r = self.rules
        bits = []
        if r.ws != "none":
            bits.append({"edges": "trim whitespace", "changes": "whitespace amount", "all": "all whitespace"}[r.ws])
        if r.ignore_case:
            bits.append("case")
        if r.ignore_eol:
            bits.append("line endings")
        if r.unimportant:
            bits.append(f"{len(r.unimportant)} unimportant pattern(s)")
        if r.replacements:
            bits.append(f"{len(r.replacements)} replacement(s)")
        if self.anchors:
            bits.append(f"{len(self.anchors)} manual alignment(s)")
        return ", ".join(bits) if bits else "none"

    def _heights(self, rows, L, R):
        pl, pr_ = self.panes["L"], self.panes["R"]
        if not self.wrap:
            pl.wrap_cols = pr_.wrap_cols = None
            return None
        cols = min(pl.natural_cols(), pr_.natural_cols())
        pl.wrap_cols = pr_.wrap_cols = cols
        return self._calc_heights(cols)

    def _calc_heights(self, cols):
        out = []
        L, R = self.panes["L"].lines, self.panes["R"].lines
        for pi in self.disp:
            if pi < 0:
                out.append(1)
                continue
            li, ri, _, _ = self.pairs[pi]
            m = 1
            for lines, i in ((L, li), (R, ri)):
                if i is not None and i < len(lines):
                    s = lines[i]
                    n = len(s.expandtabs(4)) if "\t" in s else len(s)
                    m = max(m, math.ceil(n / cols) if n else 1)
            out.append(m)
        return out

    def _maybe_rewrap(self):
        if self.wrap and self.panes["L"].rows:
            cols = min(self.panes["L"].natural_cols(), self.panes["R"].natural_cols())
            if cols != self.panes["L"].wrap_cols:
                for p in self.panes.values():
                    p.wrap_cols = cols
                h = self._calc_heights(cols)
                for p in self.panes.values():
                    p.set_heights(h)
                self.mm.update()

    def _spans(self, side, r):
        c = self._span_cache.get(r)
        if c is None:
            c = ([], [])
            try:
                pi = self.disp[r]
                li, ri, kind, _ = self.pairs[pi]
                if kind == "mod" and self.hl_level != "line":
                    a = self.panes["L"].lines[li].expandtabs(4)
                    b = self.panes["R"].lines[ri].expandtabs(4)
                    sa, sb = engine.word_spans(a, b, self.rules, chars=self.hl_level == "char")
                    c = ([(x, y, QColor(theme.DIFF_DEL_WORD)) for x, y in sa],
                         [(x, y, QColor(theme.DIFF_ADD_WORD)) for x, y in sb])
            except (IndexError, TypeError):
                pass
            self._span_cache[r] = c
        return c[0] if side == "L" else c[1]

    def _refresh_headers(self):
        for k in ("L", "R"):
            s, h = self.sides[k], self.headers[k]
            parts = os.path.normpath(s.path).split(os.sep) if s.path else []
            h.path.setText(s.title or (os.sep.join(parts[-3:]) if parts else "(none)"))
            h.path.setToolTip(s.path or "")
            t = time.strftime("%m/%d %H:%M", time.localtime(s.mtime)) if s.mtime else "new"
            h.meta.setText(f"{s.enc} · {s.eol} · {t}")
            h.badge.setVisible(self._side_modified(k))
            h.ro.setVisible(s.readonly)
            h.mixed.setVisible(s.mixed)

    def _side_modified(self, k):
        return self.panes[k].is_modified() or self.sides[k].meta_dirty

    def _update_buttons(self):
        self.b_save.setEnabled(any(self._side_modified(k) and not self.sides[k].readonly for k in "LR"))
        p = self.panes[self._focus]
        self.b_undo.setEnabled(p.can_undo())
        self.b_redo.setEnabled(p.can_redo())
        self.b_tl.setEnabled(not self.sides["R"].readonly)
        self.b_tr.setEnabled(not self.sides["L"].readonly)

    # ======================================================= interactions
    def _hsync(self, k, v):
        if self.layout_mode == "over":
            self.panes[k].horizontalScrollBar().setValue(v)

    def _set_focus(self, k):
        self._focus = k
        self._update_buttons()

    def _apply_layout(self):
        side = self.layout_mode == "side"
        self.split.setOrientation(Qt.Horizontal if side else Qt.Vertical)
        for w in (self.wraps["L"], self.gutter, self.wraps["R"]):
            w.setParent(None)
        self.split.addWidget(self.wraps["L"])
        if side:
            self.split.addWidget(self.gutter)
            self.gutter.show()
        else:
            self.gutter.hide()
        self.split.addWidget(self.wraps["R"])
        for i in range(self.split.count()):
            self.split.setStretchFactor(i, 0 if (side and i == 1) else 1)
        self.b_view.setIcon(theme.icon("layout_side" if side else "layout_over", theme.MUTED, 20))

    def set_layout(self, key):
        if key == self.layout_mode:
            return
        self.layout_mode = key
        self.cfg["text_layout"] = key
        self._apply_layout()
        self._rebuild()

    def set_wrap(self, on):
        self.wrap = on
        self.cfg["word_wrap"] = bool(on)
        for p in self.panes.values():
            p.wrap = on
        self._rebuild()

    def set_hl_level(self, key):
        self.hl_level = key
        self._span_cache = {}
        for p in self.panes.values():
            p.viewport().update()
        self._update_detail()

    def _toggle_syn(self, on):
        self.syntax = on
        self._rebuild()

    def _toggle_details(self, on):
        self.show_details = on
        self.det_scroll.setVisible(on)

    def _lang_changed(self, text):
        self.lang_override = None if text == "Auto" else text
        self._rebuild()

    def _set_mode(self, i):
        self.mode = i
        self.cur = -1
        self._rebuild()

    def _set_context(self, v):
        self.context = v
        if self.mode == 1:
            self._rebuild()

    def _on_edited(self, k):
        s = self.sides[k]
        s.gen += 1
        self._span_cache = {}
        self._refresh_headers()
        self._update_buttons()
        big = len(self.panes["L"].lines) + len(self.panes["R"].lines) > 30000
        self._timer.start(250 if big else 0)

    def _on_spliced(self, k, a, old_n, new_n):
        """Keep manual alignments attached to their lines while text is inserted / removed."""
        idx = 0 if k == "L" else 1
        delta = new_n - old_n
        out = []
        for pair in self.anchors:
            v = pair[idx]
            if v >= a + old_n:
                v += delta
            elif v >= a:
                continue
            p = list(pair)
            p[idx] = v
            out.append(tuple(p))
        self.anchors = out

    def _row_clicked(self, r):
        self.cur = r
        self.cur_hunk = self._hunk_of_row(r) if self._hunk_of_row(r) >= 0 else self.cur_hunk
        self._show_current_hunk()
        self._update_detail()

    def _cursor_moved(self, k, li, col):
        pane = self.panes[k]
        r = pane.row_of_line(li)
        if k == self._focus:
            self.cur = r
            h = self._hunk_of_row(r)
            if h >= 0:
                self.cur_hunk = h
                self._show_current_hunk()
            self._update_detail()
            self.s_pos.setText(f"Ln {li + 1}, Col {col + 1}")

    def _hunk_of_row(self, r):
        if not (0 <= r < len(self.disp)) or self.disp[r] < 0:
            return -1
        return self.pairs[self.disp[r]][3]

    def _current_hunk(self):
        if 0 <= self.cur_hunk < len(self.hunks):
            return self.cur_hunk
        h = self._hunk_of_row(self.cur)
        if h >= 0:
            return h
        best = -1
        for hh, d in self.hunk_first.items():
            if d <= self.cur and hh > best:
                best = hh
        return best if best >= 0 else (0 if self.hunks else -1)

    def _show_current_hunk(self):
        h = self.cur_hunk
        rng = (self.hunk_first[h], self.hunk_last[h]) if h in self.hunk_first else None
        for p in self.panes.values():
            p.current_rows = rng
            p.viewport().update()

    def goto_hunk(self, h):
        if not (0 <= h < len(self.hunks)) or h not in self.hunk_first:
            return
        self.cur_hunk = h
        r = self.hunk_first[h]
        self.cur = r
        for k, p in self.panes.items():
            p.ensure_visible_row(r, True)
            row = p.rows[r] if r < len(p.rows) else None
            if row is not None and row.li is not None:
                p.set_cursor(row.li, 0, scroll=False)
        self._show_current_hunk()
        self._update_detail()

    def step(self, d):
        if not self.hunks:
            return
        h = self._current_hunk()
        if self.cur < 0 and self.cur_hunk < 0:
            h = 0 if d > 0 else len(self.hunks) - 1
        else:
            h = max(0, min(len(self.hunks) - 1, h + d))
        self.goto_hunk(h)

    def _update_detail(self):
        r = self.cur
        if not (0 <= r < len(self.disp)) or self.disp[r] < 0:
            self.dh.setText("")
            self.dl.setText("")
            self.dr.setText("")
            self.s_hunk.setText(f"{len(self.hunks)} hunks")
            return
        li, ri, kind, h = self.pairs[self.disp[r]]
        L, R = self.panes["L"].lines, self.panes["R"].lines
        lt = L[li] if li is not None and li < len(L) else ""
        rt = R[ri] if ri is not None and ri < len(R) else ""
        ln = (li if li is not None else ri) + 1
        word = (re.findall(r"\w+", rt or lt) or [""])[0]
        self.dh.setText(f"LINE {ln} · {word}" + ("   (unimportant difference)" if kind == "unimp" else ""))
        if kind == "mod":
            a, b = engine.word_spans(lt, rt, self.rules, chars=True)
        else:
            a = b = []
        self.dl.setText(self._hl("L", lt, a, theme.DIFF_DEL_WORD))
        self.dr.setText(self._hl("R", rt, b, theme.DIFF_ADD_WORD))
        cur = self._current_hunk()
        self.s_hunk.setText(f"Hunk {cur + 1} of {len(self.hunks)}" if cur >= 0 else f"{len(self.hunks)} hunks")

    @staticmethod
    def _hl(side, text, spans, color):
        out, pos = [], 0
        for s, e in spans:
            out.append(html.escape(text[pos:s]).replace(" ", "&nbsp;"))
            out.append(f"<span style='background:{color}'>{html.escape(text[s:e]).replace(' ', '&nbsp;')}</span>")
            pos = e
        out.append(html.escape(text[pos:]).replace(" ", "&nbsp;"))
        return (f"<span style='color:{theme.MUTED}'>{side}</span>&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;"
                + "".join(out))

    # ============================================================== edits
    def _flush(self):
        if self._timer.isActive():
            self._rebuild()

    def _gutter_copy(self, hunk, direction):
        self.cur_hunk = hunk
        self.take(direction, hunk)

    def take(self, direction, hunk=None):
        """direction 'right': copy the left side of the hunk onto the right; 'left': the opposite."""
        self._flush()
        h = hunk if hunk is not None else self._current_hunk()
        if h is None or h < 0 or h >= len(self.hunks):
            return
        i1, i2, j1, j2 = self.hunks[h]
        dst = "R" if direction == "right" else "L"
        if self.sides[dst].readonly:
            toast("That side is read-only", "warn")
            return
        L, R = self.panes["L"].lines, self.panes["R"].lines
        if direction == "right":
            self.panes["R"].replace_lines(j1, j2, L[i1:i2])
        else:
            self.panes["L"].replace_lines(i1, i2, R[j1:j2])
        self._flush()
        if self.hunks:
            self.goto_hunk(min(h, len(self.hunks) - 1))

    def copy_line(self, direction, row):
        """Copy a single modified line pair across."""
        self._flush()
        if not (0 <= row < len(self.disp)) or self.disp[row] < 0:
            return
        li, ri, kind, _ = self.pairs[self.disp[row]]
        if kind != "mod":
            return self.take(direction, self._hunk_of_row(row))
        L, R = self.panes["L"].lines, self.panes["R"].lines
        if direction == "right":
            self.panes["R"].replace_lines(ri, ri + 1, [L[li]])
        else:
            self.panes["L"].replace_lines(li, li + 1, [R[ri]])

    def undo(self):
        self.panes[self._focus].undo_()

    def redo(self):
        self.panes[self._focus].redo_()

    def _context_menu(self, k, r, pos):
        pane = self.panes[k]
        m = QMenu(self)
        ro = self.sides[k].readonly
        a = m.addAction("Cut", pane.cut)
        a.setEnabled(bool(pane.selection()) and not ro)
        m.addAction("Copy", pane.copy).setEnabled(bool(pane.selection()))
        m.addAction("Paste", pane.paste).setEnabled(not ro)
        m.addAction("Select all", pane.select_all)
        m.addSeparator()
        m.addAction("Undo", pane.undo_).setEnabled(pane.can_undo())
        m.addAction("Redo", pane.redo_).setEnabled(pane.can_redo())
        h = self._hunk_of_row(r)
        if h >= 0:
            m.addSeparator()
            m.addAction("Copy difference to the right", lambda: self.take("right", h)).setEnabled(not self.sides["R"].readonly)
            m.addAction("Copy difference to the left", lambda: self.take("left", h)).setEnabled(not self.sides["L"].readonly)
            if self.pairs[self.disp[r]][2] == "mod":
                m.addAction("Copy this line across", lambda: self.copy_line("right" if k == "L" else "left", r))
        m.addSeparator()
        m.addAction("Align the cursor lines", self.align_cursors)
        if self.anchors:
            m.addAction("Clear manual alignments", self.clear_alignments)
        m.addAction("Find…", self.find)
        m.exec(pos)

    # ======================================================= alignment
    def align_cursors(self):
        li, ri = self.panes["L"].cur_li, self.panes["R"].cur_li
        if not self.panes["L"].lines or not self.panes["R"].lines:
            return
        self.anchors = [a for a in self.anchors if a[0] != li and a[1] != ri]
        self.anchors.append((li, ri))
        self.anchors.sort()
        self._rebuild()
        toast(f"Aligned left line {li + 1} with right line {ri + 1}")

    def clear_alignments(self):
        if self.anchors:
            self.anchors = []
            self._rebuild()
            toast("Manual alignments cleared")

    # ============================================================== rules
    def edit_rules(self):
        dlg = RulesDialog(self.rules, self)
        if dlg.exec():
            self.rules = dlg.rules()
            self.rules_pinned = True
            if dlg.save_default.isChecked():
                store_rules_in_config(self.cfg, self.rules)
                self.rules_base = self.rules.copy()
            self._rebuild()

    def show_patch(self):
        self._flush()
        fmt = lambda s: time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(s)) if s else ""
        PatchDialog(self.panes["L"].lines, self.panes["R"].lines, self.sides["L"].path, self.sides["R"].path,
                    self.rules, self.anchors, self, fmt(self.sides["L"].mtime), fmt(self.sides["R"].mtime)).exec()

    def report(self):
        from . import reports
        self._flush()
        path, flt = QFileDialog.getSaveFileName(self, "Save report", "text-report.html",
                                                "HTML (*.html);;Text (*.txt);;XML (*.xml)")
        if not path:
            return
        fmt = {"html": "html", "htm": "html", "txt": "text", "xml": "xml"}.get(path.rsplit(".", 1)[-1].lower(), "html")
        try:
            reports.write_text_report(path, fmt, self.sides["L"].path, self.sides["R"].path,
                                      self.panes["L"].lines, self.panes["R"].lines, self.rules, self.anchors,
                                      context=self.context if self.mode == 1 else (0 if self.mode == 2 else None))
        except OSError as e:
            QMessageBox.warning(self, "Report", str(e))
            return
        toast(f"Report saved to {os.path.basename(path)}")

    # ================================================= encodings / EOL
    def reload_with_encoding(self, k, label):
        if self._side_modified(k):
            if QMessageBox.question(self, "Reload", "Discard your edits on this side and reload the file?") != QMessageBox.Yes:
                return
        self.sides[k].forced_enc = label
        self._load_side(k, label)
        self._rebuild()

    def set_save_encoding(self, k, label):
        s = self.sides[k]
        if s.readonly:
            return
        s.enc = label
        s.meta_dirty = True
        self._refresh_headers()
        self._update_buttons()

    def set_eol(self, k, label):
        s = self.sides[k]
        if s.readonly:
            return
        s.eol, s.mixed, s.meta_dirty = label, False, True
        self._rebuild()

    # ================================================================ save
    def has_unsaved(self):
        return any(self._side_modified(k) and not self.sides[k].readonly for k in "LR")

    def save(self):
        if not self.has_unsaved():
            toast("Nothing to save", "warn")
            return
        for k in ("L", "R"):
            s = self.sides[k]
            if self._side_modified(k) and not s.readonly and s.path:
                self._write(k, s.path)
        self._refresh_headers()
        self._update_buttons()
        toast("Saved")

    def _write(self, k, path):
        s, pane = self.sides[k], self.panes[k]
        try:
            engine.write_text(path, pane.lines, s.enc, s.eol, s.final_nl)
        except OSError as e:
            QMessageBox.warning(self, "Save failed", f"{path}\n\n{e}")
            return False
        pane.mark_saved()
        s.meta_dirty = False
        try:
            s.mtime = os.path.getmtime(path)
        except OSError:
            pass
        return True

    def save_as(self, k):
        s = self.sides[k]
        path, _ = QFileDialog.getSaveFileName(self, "Save as", s.path)
        if path and self._write(k, path):
            s.path = path
            s.readonly = False
            self.panes[k].readonly = False
            self._refresh_headers()
            toast(f"Saved {os.path.basename(path)}")

    # ======================================================== find / goto
    def find(self):
        self.findbar.show()
        sel = ""
        for k in (self._focus, "L", "R"):
            sel = self.panes[k].selected_text()
            if sel and "\n" not in sel:
                break
            sel = ""
        if sel:
            self.find_edit.setText(sel)
        self.find_edit.setFocus()
        self.find_edit.selectAll()
        self._find_changed()

    def close_find(self):
        self.findbar.hide()
        for p in self.panes.values():
            p.find_term = ""
            p.viewport().update()
        self.panes[self._focus].setFocus()

    def _find_changed(self, *_):
        t = self.find_edit.text()
        for p in self.panes.values():
            p.find_term, p.find_case = t, self.find_case.isChecked()
            p.viewport().update()
        if t:
            total = sum(len(p.find_matches(i)) for p in self.panes.values() for i in range(len(p.lines)))
            self.find_info.setText(f"{total} match(es)" if total else "No matches")
        else:
            self.find_info.setText("")

    def find_next(self, d=1):
        t = self.find_edit.text()
        if not t:
            return self.find()
        self._flush()
        rows = len(self.disp)
        if not rows:
            return
        fp = self.panes[self._focus]
        cur_row = fp.row_of_line(fp.cur_li)
        side_idx = 0 if self._focus == "L" else 1
        sel = fp.selection()
        cur_key = (cur_row, side_idx, (sel[0][1] if sel and d < 0 else fp.cur_col) if d < 0 else
                   (sel[1][1] if sel else fp.cur_col))
        if d < 0 and sel:
            cur_key = (cur_row, side_idx, sel[0][1])
        best = None
        wrapped = False
        for off in range(rows + 1):
            r = (cur_row + d * off) % rows
            if off and (cur_row + d * off < 0 or cur_row + d * off >= rows):
                wrapped = True
            pi = self.disp[r]
            if pi < 0:
                continue
            li, ri, _, _ = self.pairs[pi]
            cands = []
            for si, (k, i) in enumerate((("L", li), ("R", ri))):
                if i is None:
                    continue
                for m in self.panes[k].find_matches(i):
                    key = (r, si, m[0])
                    if off == 0 and ((d > 0 and key <= cur_key) or (d < 0 and key >= cur_key)):
                        continue
                    if off == rows:        # back at the start row after a full lap
                        if (d > 0 and key > cur_key) or (d < 0 and key < cur_key):
                            continue
                    cands.append((key, k, i, m))
            if cands:
                best = min(cands) if d > 0 else max(cands)
                break
        if not best:
            toast(f"'{t}' not found", "warn")
            return
        key, k, i, m = best
        pane = self.panes[k]
        pane.setFocus()
        pane.anchor = (i, m[0])
        pane.cur_li, pane.cur_col = i, m[1]
        pane._moved()
        for p in self.panes.values():
            p.ensure_visible_row(key[0], False)
        self.cur = key[0]
        self._update_detail()
        if wrapped:
            toast("Search wrapped", "warn")

    def goto_line(self):
        pane = self.panes[self._focus]
        total = max(1, len(pane.lines))
        n, ok = QInputDialog.getInt(self, "Go to line", f"Line number in the {'left' if self._focus == 'L' else 'right'} file (1–{total}):",
                                    pane.cur_li + 1, 1, total)
        if ok:
            pane.set_cursor(n - 1, 0)
            pane.setFocus()
            r = pane.row_of_line(n - 1)
            for p in self.panes.values():
                p.ensure_visible_row(r, True)

    # ============================================================ swap etc.
    def swap_sides(self):
        a, b = self.sides["L"], self.sides["R"]
        keep = ("path", "enc", "eol", "mixed", "mtime", "readonly", "fmt", "note", "final_nl", "meta_dirty", "forced_enc")
        for attr in keep:
            va, vb = getattr(a, attr), getattr(b, attr)
            setattr(a, attr, vb)
            setattr(b, attr, va)
        a.gen += 1
        b.gen += 1
        pl, pr_ = self.panes["L"], self.panes["R"]
        for attr in ("lines", "undo", "redo", "_saved_token", "_last_key", "readonly"):
            va, vb = getattr(pl, attr), getattr(pr_, attr)
            setattr(pl, attr, vb)
            setattr(pr_, attr, va)
        pl._calc_maxcols()
        pr_._calc_maxcols()
        pl.set_cursor(0, 0)
        pr_.set_cursor(0, 0)
        self.anchors = [(j, i) for i, j in self.anchors]
        self.cur = self.cur_hunk = -1
        self._rebuild()
        if self.hunks:
            self.goto_hunk(0)

    def reload(self):
        if self.has_unsaved():
            if QMessageBox.question(self, "Reload", "Discard unsaved edits and reload both files from disk?") != QMessageBox.Yes:
                return
        self._load_all()
        self.cur = self.cur_hunk = -1
        self.anchors = []
        self._rebuild()
        if self.hunks:
            self.goto_hunk(0)
        toast("Reloaded from disk")

    def set_titles(self, left=None, right=None, base=None):
        for k, t in (("L", left), ("R", right)):
            if t:
                self.sides[k].title = t
        self._refresh_headers()

    def cancel_work(self):
        self._timer.stop()
