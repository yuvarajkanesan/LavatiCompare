"""3-way merge view (ours / base / theirs -> output)."""
import os
import time

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMessageBox, QPushButton, QScrollArea, QSplitter, QTableWidget, QTableWidgetItem,
                               QVBoxLayout, QWidget)

from . import engine, foldermerge, theme, vfs
from .text_view import EditDialog
from .widgets import ToolbarAdapter, sty, toast, LinesWidget, Pill, Row, Segmented, tool_button, vsep
from .workers import TaskWorker



class Block(LinesWidget):
    """Non-scrolling line block sized to its content."""

    def __init__(self, rows):
        super().__init__(show_sign=False)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.set_rows(rows, keep_scroll=False)
        self.setFixedHeight(max(1, len(rows)) * self.rh)

    def wheelEvent(self, e):
        e.ignore()


class MergeView(QWidget):
    resultChanged = Signal(str)
    saved = Signal(str)

    def __init__(self, cfg, ours, base, theirs, output=None, origin="", parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.two_way = not base
        self.paths = dict(ours=ours, base=base or "", theirs=theirs)
        self.output = output or os.path.join(os.path.dirname(ours), os.path.basename(ours))
        self.origin = origin
        rd = lambda p: engine.split_lines(engine.read_text(p)[0]) if p and os.path.exists(p) else []
        self.O, self.B, self.T = rd(ours), rd(base), rd(theirs)
        self.eol = engine.read_text(ours)[2] if os.path.exists(ours) else "LF"
        self.enc = engine.read_text(ours)[1] if os.path.exists(ours) else "UTF-8"
        self.rules = engine.TextRules.from_config(cfg)
        self.segs = engine.merge2(self.O, self.T, self.rules) if self.two_way else engine.merge3(self.O, self.B, self.T, self.rules)
        self.conf = [s for s in self.segs if s.kind == "conflict"]
        self.cur = 0
        self._ui()
        self._rebuild()
        if self.conf:
            self.goto(0)

    # ---------------------------------------------------------------- UI
    def _ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QFrame()
        sty(bar, f"background:{theme.SURFACE}; border-bottom:1px solid {theme.BORDER};")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 8, 16, 8)
        bl.setSpacing(4)
        b1 = tool_button("Prev conflict", "up", "Previous conflict (F7)")
        b2 = tool_button("Next conflict", "down", "Next conflict (F8)")
        b3 = tool_button("Auto-merge", "check", "Resolve whitespace-only conflicts automatically")
        b4 = tool_button("Reset", "refresh", "Clear all conflict choices")
        lname, rname = ("Left", "Right") if self.two_way else ("Ours", "Theirs")
        b5 = tool_button("All " + lname.lower(), "left", "Resolve every open conflict with " + lname.lower())
        b6 = tool_button("All " + rname.lower(), "right", "Resolve every open conflict with " + rname.lower())
        b1.clicked.connect(lambda: self.step(-1))
        b2.clicked.connect(lambda: self.step(1))
        b3.clicked.connect(self.auto_merge)
        b4.clicked.connect(self.reset)
        b5.clicked.connect(lambda: self.resolve_all("ours"))
        b6.clicked.connect(lambda: self.resolve_all("theirs"))
        bl.addWidget(b1)
        bl.addWidget(b2)
        bl.addWidget(vsep())
        bl.addWidget(b3)
        bl.addWidget(b5)
        bl.addWidget(b6)
        bl.addWidget(b4)
        bl.addStretch(1)
        self.p_conf = Pill("", theme.RED, theme.RED_BG)
        self.p_auto = Pill("", theme.GREEN, theme.GREEN_BG)
        bl.addWidget(self.p_conf)
        bl.addSpacing(6)
        bl.addWidget(self.p_auto)
        bl.addSpacing(10)
        self.save_btn = QPushButton("Save merged")
        self.save_btn.setProperty("primary", True)
        self.save_btn.setMinimumHeight(36)
        self.save_btn.clicked.connect(self.save)
        bl.addWidget(self.save_btn)
        ToolbarAdapter(bar)
        root.addWidget(bar)

        split = QSplitter(Qt.Vertical)
        split.setHandleWidth(1)
        top = QWidget()
        tl = QHBoxLayout(top)
        tl.setContentsMargins(0, 0, 0, 0)
        tl.setSpacing(1)
        self.views = {}
        self.meta_labels = {}
        if self.two_way:
            specs = (("ours", "Left", theme.BLUE_BG, theme.BLUE, "first file"),
                     ("theirs", "Right", theme.PURPLE_BG, theme.PURPLE, "second file"))
        else:
            specs = (("ours", "Ours", theme.BLUE_BG, theme.BLUE, "local"),
                     ("base", "Base", theme.PANEL, theme.TEXT, "common ancestor"),
                     ("theirs", "Theirs", theme.PURPLE_BG, theme.PURPLE, "remote"))
        for key, title, bg, col, hint in specs:
            col_w = QWidget()
            cl = QVBoxLayout(col_w)
            cl.setContentsMargins(0, 0, 0, 0)
            cl.setSpacing(0)
            h = QFrame()
            sty(h, f"background:{bg}; border-bottom:1px solid {theme.BORDER};")
            hl = QHBoxLayout(h)
            hl.setContentsMargins(16, 8, 16, 8)
            t = QLabel(title)
            t.setStyleSheet(f"color:{col}; font-weight:700; font-size:11pt;")
            m = QLabel(f"{os.path.basename(self.paths[key])} · {hint}")
            self.meta_labels[key] = m
            m.setFont(theme.mono(9))
            m.setStyleSheet(f"color:{theme.MUTED};")
            hl.addWidget(t)
            hl.addStretch(1)
            hl.addWidget(m)
            cl.addWidget(h)
            v = LinesWidget(show_sign=False)
            self.views[key] = v
            cl.addWidget(v, 1)
            tl.addWidget(col_w, 1)
        split.addWidget(top)

        bottom = QWidget()
        bl2 = QVBoxLayout(bottom)
        bl2.setContentsMargins(0, 0, 0, 0)
        bl2.setSpacing(0)
        oh = QFrame()
        sty(oh, f"background:{theme.PANEL}; border-top:1px solid {theme.BORDER}; border-bottom:1px solid {theme.BORDER};")
        ohl = QHBoxLayout(oh)
        ohl.setContentsMargins(16, 8, 16, 8)
        o1 = QLabel("Output")
        o1.setStyleSheet("font-weight:700;")
        self.o_path = QLabel(self.output)
        self.o_path.setFont(theme.mono(9))
        self.o_path.setStyleSheet(f"color:{theme.MUTED};")
        ohl.addWidget(o1)
        ohl.addWidget(self.o_path)
        ohl.addStretch(1)
        bl2.addWidget(oh)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.NoFrame)
        self.scroll.setStyleSheet(f"background:{theme.PANEL};")
        bl2.addWidget(self.scroll, 1)
        split.addWidget(bottom)
        split.setSizes([260, 520])
        root.addWidget(split, 1)

        foot = QFrame()
        sty(foot, f"background:{theme.TABBAR};")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(20, 6, 20, 6)
        for txt, col in (("Ours change", theme.BLUE), ("Theirs change", theme.PURPLE), ("Conflict", theme.RED)):
            sq = QLabel()
            sq.setFixedSize(12, 12)
            sq.setStyleSheet(f"background:{col}; border-radius:2px;")
            l = QLabel(txt)
            l.setStyleSheet(f"color:{theme.MUTED}; font-size:9pt;")
            fl.addWidget(sq)
            fl.addWidget(l)
            fl.addSpacing(10)
        fl.addStretch(1)
        self.origin_lbl = QLabel((f"Launched from: {self.origin}  -  " if self.origin else "")
                                 + ("Two-way merge" if self.two_way else "Three-way merge"))
        self.origin_lbl.setStyleSheet(f"color:{theme.MUTED}; font-size:9pt;")
        fl.addWidget(self.origin_lbl)
        root.addWidget(foot)

    # ------------------------------------------------------------- state
    def unresolved(self):
        return [s for s in self.conf if s.resolution is None]

    def _rebuild(self):
        # top panes
        def mk(lines, marks):
            rows = [Row(str(i + 1), t) for i, t in enumerate(lines)]
            for rng, bg, bar in marks:
                for i in range(rng[0], min(rng[1], len(rows))):
                    rows[i].bg = bg
                    rows[i].bar = bar
            return rows
        om, bm, tm = [], [], []
        for i, s in enumerate(self.segs):
            if s.kind == "conflict":
                red = QColor(theme.RED)
                om.append((s.ours_range, QColor(theme.CONFLICT_BG), red))
                tm.append((s.theirs_range, QColor(theme.CONFLICT_BG), red))
                bm.append((s.base_range, QColor(theme.CONFLICT_BG), red))
            elif s.kind == "auto":
                if s.side in ("ours", "both"):
                    om.append((s.ours_range, QColor(theme.OURS_BG), None))
                if s.side in ("theirs", "both"):
                    tm.append((s.theirs_range, QColor(theme.THEIRS_BG), None))
        self.views["ours"].set_rows(mk(self.O, om))
        if "base" in self.views:
            self.views["base"].set_rows(mk(self.B, bm))
        self.views["theirs"].set_rows(mk(self.T, tm))

        # output
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lineno = 1
        ci = 0
        self.conf_widgets = []
        for s in self.segs:
            if s.kind == "auto":
                out = engine.segment_output(s)
                lay.addWidget(self._auto_widget(s, lineno, out))
                lineno += len(out)
            elif s.kind == "text":
                rows = [Row(str(lineno + i), t) for i, t in enumerate(s.lines)]
                lineno += len(rows)
                if rows:
                    lay.addWidget(Block(rows))
            else:
                out = engine.segment_output(s)
                fr = self._conflict_widget(s, ci, lineno, out)
                self.conf_widgets.append(fr)
                lay.addWidget(fr)
                lineno += len(out) if out is not None else 0
                ci += 1
        lay.addStretch(1)
        self.scroll.setWidget(w)
        n = len(self.unresolved())
        auto = sum(1 for s in self.segs if s.kind == "auto")
        self.p_conf.set(f"{n} conflict{'s' if n != 1 else ''} left" if n else "All conflicts resolved",
                        theme.RED if n else theme.GREEN, theme.RED_BG if n else theme.GREEN_BG)
        self.p_auto.setText(f"{auto} auto-merged")
        self.save_btn.setEnabled(n == 0)
        self.resultChanged.emit(f"{n} conflicts" if n else "resolved")

    def _conflict_widget(self, s, idx, lineno, out):
        fr = QFrame()
        fr.setObjectName("cf")
        resolved = s.resolution is not None
        col, bg = (theme.GREEN, theme.GREEN_BG) if resolved else (theme.RED, theme.RED_BG)
        fr.setStyleSheet(f"#cf {{ background:{bg}; border:1.5px solid {col}; border-radius:10px; }}")
        lay = QVBoxLayout(fr)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(14, 10, 12, 8)
        n = max(len(s.ours), len(s.theirs), 1)
        if resolved:
            names = ("left", "right") if self.two_way else ("ours", "theirs")
            label = {"ours": names[0].title(), "theirs": names[1].title(), "both": "Both (" + names[0] + " first)",
                     "manual": "Edited manually", "base": "Base"}.get(s.resolution, s.resolution)
            t = QLabel(f"Conflict {idx + 1} of {len(self.conf)} · resolved with {label}")
        else:
            t = QLabel(f"Conflict {idx + 1} of {len(self.conf)} · lines {lineno}–{lineno + n - 1}")
        t.setStyleSheet(f"color:{col}; font-weight:700; background:transparent; border:none;")
        head.addWidget(t, 1)
        a, bn = ("Left", "Right") if self.two_way else ("Ours", "Theirs")
        for text, res, accent in (("Use " + a, "ours", theme.BLUE), ("Use " + bn, "theirs", theme.PURPLE),
                                  ("Both (" + a.lower() + " first)", "both", None), ("Edit manually", "manual", None)):
            b = QPushButton(text)
            b.setProperty("plain", True)
            if accent:
                b.setStyleSheet(f"color:{accent}; font-weight:600; border:1.5px solid {accent}; background:{theme.PANEL};")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, seg=s, r=res: self.resolve(seg, r))
            head.addWidget(b)
        if resolved:
            u = QPushButton("Undo")
            u.setProperty("plain", True)
            u.clicked.connect(lambda _=False, seg=s: self.resolve(seg, None))
            head.addWidget(u)
        lay.addLayout(head)
        if resolved:
            rows = [Row(str(lineno + i), t) for i, t in enumerate(out or [])]
            if rows:
                lay.addWidget(Block(rows))
        else:
            rows = []
            tags = ("L", "R") if self.two_way else ("O", "T")
            for tag, lines, bgc in ((tags[0], s.ours, QColor(theme.OURS_BG)), (tags[1], s.theirs, QColor(theme.THEIRS_BG))):
                first = True
                for t in lines or [""]:
                    r = Row(tag if first else "", t, bg=bgc)
                    r.num = tag if first else ""
                    first = False
                    rows.append(r)
            blk = Block(rows)
            lay.addWidget(blk)
        fr.setContentsMargins(0, 0, 0, 0)
        wrap = QWidget()
        wl = QVBoxLayout(wrap)
        wl.setContentsMargins(78, 6, 14, 6)
        wl.addWidget(fr)
        wrap.setStyleSheet(f"background:{theme.PANEL};")
        wrap._frame = fr
        return wrap

    def _auto_widget(self, s, lineno, out):
        reverted = s.resolution is not None and s.resolution != s.side
        who = {"ours": "left" if self.two_way else "ours", "theirs": "right" if self.two_way else "theirs",
               "both": "both sides"}[s.side]
        bg = QColor(theme.PANEL) if reverted else QColor({"ours": theme.OURS_BG, "theirs": theme.THEIRS_BG, "both": theme.OURS_BG}[s.side])
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        head = QHBoxLayout()
        head.setContentsMargins(78, 2, 14, 0)
        lab = QLabel("Reverted to the base text" if reverted else "Merged automatically from " + who)
        lab.setStyleSheet(f"color:{theme.MUTED}; font-size:8pt; background:transparent;")
        head.addWidget(lab)
        head.addStretch(1)
        b = QPushButton("Keep merged change" if reverted else "Use text from base")
        b.setProperty("plain", True)
        b.setCursor(Qt.PointingHandCursor)
        b.setStyleSheet("font-size:8pt; padding:1px 8px;")
        b.clicked.connect(lambda _=False, seg=s: self.toggle_auto(seg))
        head.addWidget(b)
        lay.addLayout(head)
        rows = [Row(str(lineno + i), t, bg=bg) for i, t in enumerate(out)]
        if rows:
            lay.addWidget(Block(rows))
        return w

    def toggle_auto(self, seg):
        seg.resolution = None if (seg.resolution is not None and seg.resolution != seg.side) else "base"
        keep = self.cur
        self._rebuild()
        self.cur = keep

    def resolve_all(self, res):
        for s in self.conf:
            if s.resolution is None:
                s.resolution = res
        self._rebuild()

    def resolve(self, seg, res):
        if res == "manual":
            base = engine.segment_output(seg) if seg.resolution else seg.ours
            dlg = EditDialog("Edit conflict manually", base or [], self)
            if not dlg.exec():
                return
            seg.manual = dlg.lines()
        seg.resolution = res
        keep = self.cur
        self._rebuild()
        self.cur = keep

    def auto_merge(self):
        n = 0
        for s in self.conf:
            if s.resolution is None and [" ".join(x.split()) for x in s.ours] == [" ".join(x.split()) for x in s.theirs]:
                s.resolution = "ours"
                n += 1
        self._rebuild()
        if not n:
            toast("No further conflicts can be resolved automatically", "warn")

    def reset(self):
        for s in self.conf:
            s.resolution = None
            s.manual = None
        self._rebuild()

    def step(self, d):
        if not self.conf:
            return
        un = [i for i, s in enumerate(self.conf) if s.resolution is None] or list(range(len(self.conf)))
        if d > 0:
            nxt = [i for i in un if i > self.cur]
            self.goto(nxt[0] if nxt else un[0])
        else:
            prv = [i for i in un if i < self.cur]
            self.goto(prv[-1] if prv else un[-1])

    def goto(self, i):
        self.cur = i
        s = self.conf[i]
        w = self.conf_widgets[i]
        self.scroll.ensureWidgetVisible(w, 0, 60)
        self.views["ours"].ensure_visible(s.ours_range[0], True)
        self.views["theirs"].ensure_visible(s.theirs_range[0], True)
        if "base" in self.views:
            self.views["base"].ensure_visible(s.base_range[0], True)

    def set_titles(self, left=None, right=None, base=None):
        for key, t in (("ours", left), ("theirs", right), ("base", base)):
            if t and key in self.meta_labels:
                self.meta_labels[key].setText(t)

    def output_lines(self):
        out = []
        for s in self.segs:
            lines = engine.segment_output(s)
            out += lines if lines is not None else []
        return out

    def save(self):
        if self.unresolved():
            QMessageBox.warning(self, "Save merged", "Resolve all conflicts first.")
            return
        path = self.output
        if not os.path.isabs(path) or not os.path.isdir(os.path.dirname(path)):
            path, _ = QFileDialog.getSaveFileName(self, "Save merged file", path)
            if not path:
                return
        try:
            engine.write_text(path, self.output_lines(), self.enc, self.eol)
        except OSError as e:
            QMessageBox.warning(self, "Save failed", str(e))
            return
        self.output = path
        self.o_path.setText(path)
        self.saved.emit(path)
        toast(f"Merged result saved to {os.path.basename(path)}")


# ===================================================================== folder merge
RES_LABEL = {"keep": "Unchanged", "ours": "Ours", "theirs": "Theirs", "base": "Base", "delete": "Delete",
             "merged": "Merged", "skip": "Skip", None: "Conflict"}


class FolderMergeView(QWidget):
    """Two- or three-way merge of whole folders."""
    resultChanged = Signal(str)
    mergeRequested = Signal(object, str, str, str, str)      # entry, ours, base, theirs, output file
    saved = Signal(str)

    def __init__(self, cfg, ours, base, theirs, output=None, parent=None):
        super().__init__(parent)
        self.cfg = cfg
        self.specs = dict(ours=ours, base=base or "", theirs=theirs)
        self.two_way = not base
        self.output = output or ("" if ours.lower().startswith("git:") else ours)
        self.S = None
        self.entries = []
        self.rows = []
        self.mode = 0
        self.worker = None
        self._ui()
        self.scan()

    def _ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QFrame()
        sty(bar, f"background:{theme.SURFACE}; border-bottom:1px solid {theme.BORDER};")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 6, 16, 6)
        bl.setSpacing(3)
        self.seg = Segmented(["All", "Needs a decision", "Changes", "Unchanged"])
        self.seg.changed.connect(self._set_mode)
        bl.addWidget(self.seg)
        bl.addWidget(vsep())
        a, b = ("Left", "Right") if self.two_way else ("Ours", "Theirs")
        buttons = [(a, "left", "ours", "Use the " + a.lower() + " version for the selected files"),
                   (b, "right", "theirs", "Use the " + b.lower() + " version for the selected files")]
        if not self.two_way:
            buttons.append(("Base", "doc", "base", "Use the common ancestor version"))
        buttons += [("Newer", "clock", "newer", "Use whichever file is newer"),
                    ("Delete", "trash", "delete", "Leave the file out of the result"),
                    ("Reset", "refresh", None, "Clear the decision for the selected files")]
        for text, icon, res, tip in buttons:
            btn = tool_button(text, icon, tip)
            btn.clicked.connect(lambda _=False, r=res: self.resolve_selected(r))
            bl.addWidget(btn)
        bl.addWidget(vsep())
        self.b_text = tool_button("Merge text", "merge", "Open the selected conflicting text file in the text merge view")
        self.b_text.clicked.connect(self.merge_text_selected)
        bl.addWidget(self.b_text)
        bl.addStretch(1)
        self.p_conf = Pill("", theme.RED, theme.RED_BG)
        self.p_auto = Pill("", theme.GREEN, theme.GREEN_BG)
        bl.addWidget(self.p_conf)
        bl.addSpacing(6)
        bl.addWidget(self.p_auto)
        ToolbarAdapter(bar)
        root.addWidget(bar)

        paths = QFrame()
        sty(paths, f"background:{theme.PANEL}; border-bottom:1px solid {theme.BORDER};")
        pl = QHBoxLayout(paths)
        pl.setContentsMargins(16, 8, 16, 8)
        for lab, key in (("OURS" if not self.two_way else "LEFT", "ours"), ("BASE", "base"),
                         ("THEIRS" if not self.two_way else "RIGHT", "theirs")):
            if key == "base" and self.two_way:
                continue
            l = QLabel(lab)
            l.setStyleSheet(f"color:{theme.MUTED}; font-weight:700; font-size:8pt; letter-spacing:1px;")
            v = QLabel(self.specs[key])
            v.setFont(theme.mono(9))
            v.setToolTip(self.specs[key])
            pl.addWidget(l)
            pl.addWidget(v, 1)
        root.addWidget(paths)

        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["Path", "Ours" if not self.two_way else "Left", "Base",
                                              "Theirs" if not self.two_way else "Right", "Result", "Why"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = self.table.horizontalHeader()
        h.setHighlightSections(False)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        for c, w in ((1, 150), (2, 150), (3, 150), (4, 110), (5, 230)):
            h.setSectionResizeMode(c, QHeaderView.Fixed)
            self.table.setColumnWidth(c, w)
        if self.two_way:
            self.table.setColumnHidden(2, True)
        self.table.cellDoubleClicked.connect(lambda r, c: self.merge_text_selected())
        self.table.setStyleSheet(f"QTableWidget::item {{ padding:4px 8px; border-bottom:1px solid {theme.LINE_SOFT}; }}"
                                 f"QTableWidget::item:selected {{ background:{theme.ACCENT_SOFT}; color:{theme.TEXT}; }}")
        root.addWidget(self.table, 1)
        self.busy = QLabel("Scanning folders…")
        self.busy.setStyleSheet(f"padding:8px 18px; color:{theme.MUTED};")
        root.addWidget(self.busy)

        foot = QFrame()
        sty(foot, f"background:{theme.SURFACE}; border-top:1px solid {theme.BORDER};")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(18, 8, 18, 8)
        lab = QLabel("OUTPUT")
        lab.setStyleSheet(f"color:{theme.MUTED}; font-weight:700; font-size:8pt; letter-spacing:1px;")
        self.out_edit = QLineEdit(self.output)
        self.out_edit.setProperty("mono", True)
        self.out_edit.textChanged.connect(lambda t: setattr(self, "output", t.strip()))
        bb = QPushButton("Browse")
        bb.clicked.connect(self._browse_out)
        self.write_btn = QPushButton("Write merged folder")
        self.write_btn.setProperty("primary", True)
        self.write_btn.setMinimumHeight(36)
        self.write_btn.clicked.connect(self.write)
        fl.addWidget(lab)
        fl.addWidget(self.out_edit, 1)
        fl.addWidget(bb)
        fl.addWidget(self.write_btn)
        root.addWidget(foot)

    def _browse_out(self):
        d = QFileDialog.getExistingDirectory(self, "Output folder", self.output)
        if d:
            self.out_edit.setText(os.path.normpath(d))

    # ----------------------------------------------------------------- data
    def scan(self):
        specs = dict(self.specs)
        patterns = list(self.cfg["ignore_patterns"])
        ci = bool(self.cfg["ignore_case_names"])

        def go(ctx):
            o = vfs.open_location(specs["ours"])
            b = vfs.open_location(specs["base"]) if specs["base"] else (None, None)
            t = vfs.open_location(specs["theirs"])
            S = foldermerge.Sides3(o[0], o[1], b[0], b[1], t[0], t[1])
            return S, foldermerge.scan(S, patterns, ctx, ci)
        self.busy.setText("Scanning folders…")
        self.busy.show()
        w = TaskWorker(go, parent=self)
        w.done.connect(self._scanned)
        w.failed.connect(lambda e: (self.busy.setText(f"Scan failed: {e}"), toast(str(e), "error")))
        self.worker = w
        w.start()

    def reload(self):
        self.scan()

    def cancel_work(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(3000)

    def _scanned(self, res):
        self.busy.hide()
        self.S, self.entries = res
        self.render()

    @staticmethod
    def _size(n):
        for u in ("B", "KB", "MB", "GB"):
            if n < 1024 or u == "GB":
                return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
            n /= 1024

    def _side_text(self, e, which):
        ent = getattr(e, which)
        if ent is None:
            return "missing" if (which != "base" or not self.two_way) else ""
        t = time.strftime("%m/%d %H:%M", time.localtime(ent.mtime)) if ent.mtime else ""
        return f"{self._size(ent.size)} · {t}" if t else self._size(ent.size)

    def _set_mode(self, i):
        self.mode = i
        self.render()

    def render(self):
        keep_sel = {self.rows[r].rel for r in {i.row() for i in self.table.selectedIndexes()} if r < len(self.rows)}
        want = {0: lambda e: True, 1: lambda e: e.unresolved(), 2: lambda e: e.action != "keep",
                3: lambda e: e.action == "keep"}[self.mode]
        self.rows = [e for e in self.entries if want(e)]
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self.rows))
        mono = theme.mono(9)
        for r, e in enumerate(self.rows):
            res = e.effective()
            bg = {None: theme.RED_BG, "merged": theme.GREEN_BG, "ours": theme.BLUE_BG, "theirs": theme.PURPLE_BG,
                  "delete": theme.AMBER_BG}.get(res)
            why = ("your decision" + (f" (auto: {e.why})" if e.why else "")) if e.resolution else e.why
            vals = [e.rel, self._side_text(e, "ours"), self._side_text(e, "base"), self._side_text(e, "theirs"),
                    RES_LABEL.get(res, res), why]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if bg:
                    it.setBackground(QColor(bg))
                if c in (1, 2, 3):
                    it.setFont(mono)
                    if v == "missing":
                        it.setForeground(QColor(theme.MUTED))
                if c == 4:
                    f = it.font()
                    f.setBold(True)
                    it.setFont(f)
                    it.setForeground(QColor(theme.RED if res is None else theme.TEXT))
                self.table.setItem(r, c, it)
            self.table.setRowHeight(r, 34)
            if e.rel in keep_sel:
                self.table.selectRow(r)
        self.table.setUpdatesEnabled(True)
        c = foldermerge.summary(self.entries)
        self.p_conf.set(f"{c['conflict']} need a decision" if c["conflict"] else "All decided",
                        theme.RED if c["conflict"] else theme.GREEN, theme.RED_BG if c["conflict"] else theme.GREEN_BG)
        self.p_auto.setText(f"{c['merged']} merged automatically")
        self.write_btn.setEnabled(c["conflict"] == 0 and bool(self.entries))
        self.resultChanged.emit(f"{c['conflict']} conflicts" if c["conflict"] else "resolved")

    def _selected(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.rows[r] for r in rows if r < len(self.rows)]

    def resolve_selected(self, res):
        for e in self._selected():
            if res is None:
                e.resolution = None
            elif res == "newer":
                e.resolution = foldermerge.pick_newer(e)
            elif res == "base" and e.base is None:
                continue
            else:
                e.resolution = res
        self.render()

    def merge_text_selected(self):
        sel = self._selected()
        if len(sel) != 1:
            toast("Select a single conflicting text file", "warn")
            return
        e = sel[0]
        if not e.text_ok:
            toast("Only text files that exist on both sides can be merged line by line", "warn")
            return
        try:
            def lp(fs, p):
                return fs.local_path(p) if (fs is not None and p) else ""
            o, t = lp(self.S.ofs, e.opath), lp(self.S.tfs, e.tpath)
            b = lp(self.S.bfs, e.bpath)
        except (OSError, vfs.VFSError) as ex:
            toast(str(ex), "error")
            return
        out = vfs.new_temp_path(os.path.basename(e.rel))
        self.mergeRequested.emit(e, o, b, t, out)

    def set_merged(self, entry, path):
        entry.merged_path = path
        entry.resolution = "merged"
        self.render()

    def write(self):
        c = foldermerge.summary(self.entries)
        if c["conflict"]:
            QMessageBox.warning(self, "Write", "Decide every conflict first.")
            return
        if not self.output.strip():
            QMessageBox.warning(self, "Output", "Choose the output folder first.")
            return
        try:
            out_fs, out_root = vfs.open_location(self.output)
        except Exception as ex:
            QMessageBox.warning(self, "Output", str(ex))
            return
        if out_fs.readonly:
            QMessageBox.warning(self, "Output", "The output location is read-only.")
            return
        if QMessageBox.question(self, "Write merged folder", f"Write the merged result to\n{self.output}\n\n"
                                "Existing files with the same name are replaced.") != QMessageBox.Yes:
            return
        entries, S = self.entries, self.S
        w = TaskWorker(lambda ctx: foldermerge.write_output(entries, S, out_fs, out_root, ctx), parent=self)

        def done(res):
            n, errs = res
            if errs:
                QMessageBox.warning(self, "Completed with errors", "\n".join(errs[:15]))
            else:
                toast(f"Merged folder written ({n} files)")
                self.saved.emit(self.output)
        w.done.connect(done)
        w.failed.connect(lambda ex: QMessageBox.warning(self, "Write failed", str(ex)))
        self._write_worker = w
        w.start()

    def has_unsaved(self):
        return False
