"""Folder synchronisation dialog: mode cards, preview of every planned action, backups, saved sessions."""
import os
import time

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QCheckBox, QDialog, QFileDialog, QFrame, QHBoxLayout,
                               QHeaderView, QInputDialog, QLabel, QMenu, QMessageBox, QProgressDialog, QPushButton,
                               QRadioButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from . import config, engine, fsops, platform_utils, theme
from .fsops import MODES, build_plan, plan_summary
from .widgets import Pill, sty, toast
from .workers import TaskWorker

MAX_ROWS = 3000


def fmt_size(n):
    if n is None:
        return "—"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u == "B" else f"{n:.1f} {u}"
        n /= 1024


def recycle(path):
    """Move to the Recycle Bin / Trash (kept for callers outside this module)."""
    return platform_utils.move_to_trash(path)


class ModeCard(QFrame):
    def __init__(self, key, title, desc, group):
        super().__init__()
        self.key = key
        self.setObjectName("mc")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(1)
        self.radio = QRadioButton(title)
        self.radio.setStyleSheet("font-weight:600; font-size:11pt;")
        group.addButton(self.radio)
        d = QLabel(desc)
        d.setWordWrap(True)
        d.setStyleSheet(f"color:{theme.MUTED}; margin-left:24px; font-size:9pt;")
        lay.addWidget(self.radio)
        lay.addWidget(d)
        self.radio.toggled.connect(self._style)
        self._style(False)
        self.setCursor(Qt.PointingHandCursor)

    def _style(self, on):
        self.setStyleSheet(
            f"#mc {{ background:{theme.ACCENT_SOFT if on else theme.PANEL}; border:{'1.5px solid ' + theme.ACCENT if on else '1px solid ' + theme.BORDER};"
            f" border-radius:8px; }}")

    def mousePressEvent(self, e):
        self.radio.setChecked(True)


class _Bridge(QObject):
    progress = Signal(int, int, str)


ACTION_STYLE = {"copy": ("Copy", theme.BLUE, theme.CHIP_BLUE), "overwrite": ("Overwrite", theme.AMBER, theme.CHIP_AMBER),
                "delete": ("Delete", theme.RED, theme.RED_BG), "mkdir": ("New folder", theme.BLUE, theme.CHIP_BLUE),
                "skip": ("Skip", theme.MUTED, theme.CHIP_GRAY)}


class SyncDialog(QDialog):
    def __init__(self, nodes, S, selected_rels, cfg, compare_time, parent=None, left_spec="", right_spec="",
                 compare_opts=None, preset=None):
        super().__init__(parent)
        self.nodes, self.S = nodes, S
        self.selected, self.cfg, self.compare_time = selected_rels, cfg, compare_time
        self.left_spec, self.right_spec, self.compare_opts = left_spec, right_spec, compare_opts or {}
        self.ran = False
        self.ops = []
        self.setWindowTitle("Synchronize folders")
        self.resize(1080, 740)
        self.setStyleSheet(f"QDialog {{ background:{theme.PANEL}; }}")
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        head = QFrame()
        hl = QHBoxLayout(head)
        hl.setContentsMargins(24, 16, 20, 12)
        ic = QLabel()
        ic.setPixmap(theme.icon("refresh", theme.ACCENT, 22).pixmap(22, 22))
        ic.setFixedSize(40, 40)
        ic.setAlignment(Qt.AlignCenter)
        ic.setStyleSheet(f"background:{theme.ACCENT_SOFT}; border-radius:10px;")
        hl.addWidget(ic)
        tl = QVBoxLayout()
        tl.setSpacing(0)
        t = QLabel("Synchronize folders")
        t.setStyleSheet("font-size:14pt; font-weight:700;")
        self.sub = QLabel()
        self.sub.setFont(theme.mono(10))
        self.sub.setStyleSheet(f"color:{theme.MUTED};")
        tl.addWidget(t)
        tl.addWidget(self.sub)
        hl.addLayout(tl, 1)
        self.b_sessions = QPushButton("Saved sync sessions")
        self.b_sessions.setProperty("plain", True)
        self.b_sessions.setMenu(self._sessions_menu())
        hl.addWidget(self.b_sessions)
        root.addWidget(head)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.BORDER};")
        root.addWidget(sep)

        body = QHBoxLayout()
        body.setSpacing(0)
        body.setContentsMargins(0, 0, 0, 0)
        left = QWidget()
        left.setFixedWidth(400)
        ll = QVBoxLayout(left)
        ll.setContentsMargins(24, 16, 24, 12)
        ll.setSpacing(6)
        lab = QLabel("SYNC MODE")
        lab.setStyleSheet(f"color:{theme.MUTED}; font-weight:700; font-size:9pt; letter-spacing:1px;")
        ll.addWidget(lab)
        self.group = QButtonGroup(self)
        self.cards = []
        for key, title, desc in MODES:
            c = ModeCard(key, title, desc, self.group)
            self.cards.append(c)
            ll.addWidget(c)
        self.cards[0].radio.setChecked(True)
        ll.addSpacing(6)
        lab2 = QLabel("SAFETY")
        lab2.setStyleSheet(lab.styleSheet())
        ll.addWidget(lab2)
        self.c_backup = QCheckBox("Back up overwritten and deleted files")
        self.c_backup.setChecked(bool(cfg["backup_overwritten"]))
        self.backup_dir = cfg["sync_backup_dir"]
        self.b_bdir = QPushButton()
        self.b_bdir.setProperty("plain", True)
        self.b_bdir.clicked.connect(self._pick_backup_dir)
        self._bdir_label()
        self.c_recycle = QCheckBox("Use Recycle Bin / Trash for deletes (local folders)")
        self.c_recycle.setChecked(bool(cfg["use_recycle_bin"]))
        self.c_sel = QCheckBox(f"Only selected items ({len(selected_rels)})")
        self.c_sel.setEnabled(bool(selected_rels))
        self.c_skip = QCheckBox("Hide skipped items in the preview")
        for c in (self.c_backup, self.b_bdir, self.c_recycle, self.c_sel, self.c_skip):
            ll.addWidget(c)
        ll.addStretch(1)
        body.addWidget(left)
        vs = QFrame()
        vs.setFixedWidth(1)
        vs.setStyleSheet(f"background:{theme.BORDER};")
        body.addWidget(vs)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(24, 16, 24, 12)
        rl.setSpacing(8)
        self.pills = QHBoxLayout()
        self.pills.setSpacing(8)
        rl.addLayout(self.pills)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Action", "", "File", "Size"])
        self.table.verticalHeader().hide()
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        self.table.setTextElideMode(Qt.ElideMiddle)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(0, QHeaderView.Fixed)
        h.setSectionResizeMode(1, QHeaderView.Fixed)
        h.setSectionResizeMode(2, QHeaderView.Stretch)
        h.setSectionResizeMode(3, QHeaderView.Fixed)
        h.setHighlightSections(False)
        h.setDefaultAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.table.setColumnWidth(0, 110)
        self.table.setColumnWidth(1, 36)
        self.table.setColumnWidth(3, 90)
        self.table.setStyleSheet(f"QTableWidget {{ border:1px solid {theme.BORDER}; border-radius:8px; }} QTableWidget::item {{ padding-right:12px; }}")
        rl.addWidget(self.table, 1)
        self.warn = QLabel()
        self.warn.setWordWrap(True)
        self.warn.setStyleSheet(f"background:{theme.AMBER_BG}; color:{theme.AMBER}; border:1px solid {theme.BORDER};"
                                f" border-radius:8px; padding:10px 14px;")
        rl.addWidget(self.warn)
        body.addWidget(right, 1)
        root.addLayout(body, 1)

        foot = QFrame()
        sty(foot, f"background:{theme.SURFACE}; border-top:1px solid {theme.BORDER};")
        fl = QHBoxLayout(foot)
        fl.setContentsMargins(24, 12, 24, 12)
        self.summary = QLabel()
        self.summary.setStyleSheet(f"color:{theme.MUTED};")
        fl.addWidget(self.summary, 1)
        b_save = QPushButton("Save session…")
        b_save.setProperty("plain", True)
        b_script = QPushButton("Export script…")
        b_script.setProperty("plain", True)
        b_cancel = QPushButton("Cancel")
        b_cancel.setProperty("plain", True)
        self.run_btn = QPushButton("Run sync")
        self.run_btn.setProperty("primary", True)
        b_save.clicked.connect(self.save_session)
        b_script.clicked.connect(self.save_script)
        b_cancel.clicked.connect(self.reject)
        self.run_btn.clicked.connect(self.run)
        for b in (b_save, b_script, b_cancel, self.run_btn):
            b.setMinimumHeight(38)
            fl.addWidget(b)
        root.addWidget(foot)

        if preset:
            self.apply_preset(preset)
        self.group.buttonToggled.connect(lambda *_: self.refresh())
        for c in (self.c_sel, self.c_skip, self.c_backup):
            c.toggled.connect(lambda *_: self.refresh())
        self.refresh()

    # ----------------------------------------------------------- sessions
    def _sessions_menu(self):
        m = QMenu(self)
        m.aboutToShow.connect(lambda: self._fill_sessions(m))
        return m

    def _fill_sessions(self, m):
        m.clear()
        items = self.cfg["sync_sessions"]
        if not items:
            m.addAction("No saved sync sessions yet").setEnabled(False)
        for s in items:
            sub = m.addMenu(s["name"])
            sub.addAction("Apply these settings", lambda s=s: self.apply_preset(s))
            sub.addAction("Delete", lambda s=s: (self.cfg["sync_sessions"].remove(s), self.cfg.save()))

    def preset(self):
        return dict(mode=self.mode(), backup=self.c_backup.isChecked(), backup_dir=self.backup_dir,
                    trash=self.c_recycle.isChecked())

    def apply_preset(self, s):
        for c in self.cards:
            if c.key == fsops.MODE_ALIASES.get(s.get("mode"), s.get("mode")):
                c.radio.setChecked(True)
        if "backup" in s:
            self.c_backup.setChecked(bool(s["backup"]))
        if "trash" in s:
            self.c_recycle.setChecked(bool(s["trash"]))
        self.backup_dir = s.get("backup_dir", self.backup_dir) or ""
        self._bdir_label()

    def save_session(self):
        base = lambda p: os.path.basename(p.rstrip("/\\")) or p
        name, ok = QInputDialog.getText(self, "Save sync session", "Name for this synchronisation:",
                                        text=f"{base(self.left_spec)} - {base(self.right_spec)}")
        if not ok or not name.strip():
            return
        from . import remote
        entry = dict(name=name.strip(), left=remote.split_secrets(self.left_spec)[0], right=remote.split_secrets(self.right_spec)[0],
                     options=self.compare_opts, created=time.time(), **self.preset())
        sessions = [s for s in self.cfg["sync_sessions"] if s["name"] != entry["name"]]
        sessions.append(entry)
        self.cfg["sync_sessions"] = sessions
        self.cfg.add_session(entry["name"], "sync", entry["left"], entry["right"], "", extra=dict(preset=self.preset(), options=self.compare_opts))
        toast(f"Sync session '{entry['name']}' saved")

    def _bdir_label(self):
        self.b_bdir.setText("Backup folder: " + (self.backup_dir if self.backup_dir else "inside the destination (.lavati-backup)"))
        self.b_bdir.setToolTip("Click to choose another place for backups; right-click to reset")
        self.b_bdir.setContextMenuPolicy(Qt.CustomContextMenu)
        try:
            self.b_bdir.customContextMenuRequested.disconnect()
        except (RuntimeError, TypeError):
            pass
        self.b_bdir.customContextMenuRequested.connect(lambda *_: (setattr(self, "backup_dir", ""), self._bdir_label()))

    def _pick_backup_dir(self):
        d = QFileDialog.getExistingDirectory(self, "Backup folder", self.backup_dir or "")
        if d:
            self.backup_dir = os.path.normpath(d)
            self._bdir_label()

    # ------------------------------------------------------------------
    def mode(self):
        for c in self.cards:
            if c.radio.isChecked():
                return c.key
        return "update_right"

    def plan(self):
        only = set(self.selected) if self.c_sel.isChecked() else None
        return build_plan(self.nodes, self.mode(), only)

    def refresh(self):
        mode = self.mode()
        ops = self.plan()
        self.ops = ops
        arrow = {"update_left": "<-", "mirror_left": "<-", "update_both": "<->"}.get(mode, "->")
        ln = os.path.basename(self.left_spec.rstrip("/\\")) or "left"
        rn = os.path.basename(self.right_spec.rstrip("/\\")) or "right"
        self.sub.setText(f"{ln} {arrow} {rn}")
        cnt, total = plan_summary(ops)
        while self.pills.count():
            it = self.pills.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        notes = {o["note"] for o in ops if o["action"] == "skip" and o["note"]}
        skip_note = f" ({notes.pop()})" if len(notes) == 1 else ""
        for text, fg, bg in ((f"Copy new · {cnt['copy']}", theme.BLUE, theme.CHIP_BLUE),
                             (f"Overwrite · {cnt['overwrite']}", theme.AMBER, theme.CHIP_AMBER),
                             (f"Delete · {cnt['delete']}", theme.RED, theme.RED_BG),
                             (f"Skip{skip_note} · {cnt['skip']}", theme.TEXT, theme.CHIP_GRAY)):
            p = Pill(text, fg, bg, bold=True)
            p.setStyleSheet(p.styleSheet().replace("border-radius:10px", "border-radius:8px"))
            self.pills.addWidget(p)
        self.pills.addStretch(1)

        order = {"copy": 0, "mkdir": 1, "overwrite": 2, "delete": 3, "skip": 4}
        shown = sorted(ops, key=lambda o: (order[o["action"]], o["rel"].lower()))
        if self.c_skip.isChecked():
            shown = [o for o in shown if o["action"] != "skip"]
        clipped = len(shown) > MAX_ROWS
        shown = shown[:MAX_ROWS]
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(shown))
        mono = theme.mono(9)
        for r, o in enumerate(shown):
            label, fg, bg = ACTION_STYLE[o["action"]]
            a = QTableWidgetItem(label)
            a.setForeground(QColor(fg))
            a.setBackground(QColor(bg))
            f = a.font()
            f.setBold(True)
            a.setFont(f)
            self.table.setItem(r, 0, a)
            d = QTableWidgetItem("<-" if o["dir"] == "L" and o["action"] != "skip" else ("->" if o["action"] != "skip" else ""))
            d.setTextAlignment(Qt.AlignCenter)
            d.setForeground(QColor(theme.MUTED))
            self.table.setItem(r, 1, d)
            it = QTableWidgetItem(o["rel"] + (f"   [{o['note']}]" if o["note"] else "") + ("/" if o["kind"] == "dir" else ""))
            it.setFont(mono)
            it.setToolTip(o["rel"])
            self.table.setItem(r, 2, it)
            s = QTableWidgetItem(fmt_size(o["size"]) if o["action"] in ("copy", "overwrite") else "—")
            s.setFont(mono)
            s.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            self.table.setItem(r, 3, s)
            self.table.setRowHeight(r, 30)
        self.table.setUpdatesEnabled(True)
        todo = [o for o in ops if o["action"] != "skip"]
        self.summary.setText(f"{len(todo)} operations · {fmt_size(total)} to copy"
                             + (f"   (showing first {MAX_ROWS:,} of {len(ops):,})" if clipped else ""))
        self.run_btn.setEnabled(bool(todo))
        edited = 0
        for o in ops:
            if o["action"] == "overwrite" and o["dst"] and not getattr(self.S.fs(o["dir"]), "slow", False):
                try:
                    if os.path.getmtime(o["dst"]) > self.compare_time:
                        edited += 1
                except OSError:
                    pass
        bk = " Backups will be kept." if self.c_backup.isChecked() else ""
        if edited:
            self.warn.setText(f"Warning: {edited} files changed after the last compare and will be overwritten.{bk}")
            self.warn.show()
        elif cnt["overwrite"] or cnt["delete"]:
            self.warn.setText(f"Warning: {cnt['overwrite']} files will be overwritten and {cnt['delete']} deleted.{bk}")
            self.warn.show()
        else:
            self.warn.hide()
        readonly = [s for s, fs in (("left", self.S.lfs), ("right", self.S.rfs)) if fs is not None and fs.readonly]
        if readonly and any(o["action"] != "skip" and ((o["dir"] == "L" and "left" in readonly) or (o["dir"] == "R" and "right" in readonly)) for o in ops):
            self.warn.setText(f"Warning: the {' and '.join(readonly)} side is read-only (archive, snapshot or Subversion): operations that write there will fail.")
            self.warn.show()

    # ------------------------------------------------------------------
    def save_script(self):
        style = "bat" if os.name == "nt" else "sh"
        text = fsops.plan_to_script(self.ops, self.S, self.mode(), style)
        if text is None:
            QMessageBox.information(self, "Export script", "Scripts can only be exported between two local folders.\n"
                                    "Save the session instead and run it with 'lavati --script' or the scripting page.")
            return
        os.makedirs(config.SCRIPTS_DIR, exist_ok=True)
        default = os.path.join(config.SCRIPTS_DIR, f"sync_{time.strftime('%Y%m%d_%H%M%S')}.{style}")
        path, _ = QFileDialog.getSaveFileName(self, "Save sync script", default, "Scripts (*.bat *.sh *.cmd)")
        if not path:
            return
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        toast(f"Script saved: {os.path.basename(path)}")

    def run(self):
        todo = [o for o in self.ops if o["action"] != "skip"]
        trash = self.c_recycle.isChecked()
        if any(o["action"] == "delete" for o in todo) and not trash and not self.c_backup.isChecked():
            if QMessageBox.question(self, "Permanent delete", "Deletes will be permanent (no Recycle Bin and no backup). Continue?") != QMessageBox.Yes:
                return
        S, backup, bdir, ops = self.S, self.c_backup.isChecked(), self.backup_dir, self.ops
        prog = QProgressDialog("Synchronizing…", "Cancel", 0, len(todo), self)
        prog.setWindowModality(Qt.WindowModal)
        prog.setMinimumDuration(0)
        prog.setWindowTitle("Synchronizing")
        bridge = _Bridge()
        bridge.progress.connect(lambda i, n, text: (prog.setMaximum(n), prog.setValue(i), prog.setLabelText(text)))
        self.run_btn.setEnabled(False)

        def go(ctx):
            return fsops.run_plan(ops, S, ctx, backup, bdir, trash,
                                  progress=lambda i, n, o: bridge.progress.emit(i, n, f"{o['action'].title()}: {o['rel']}"))
        w = TaskWorker(go, parent=self)
        self._worker = w
        prog.canceled.connect(w.cancel)

        def done(res):
            prog.close()
            count, errors = res
            self.ran = True
            if errors:
                QMessageBox.warning(self, "Completed with errors", "\n".join(errors[:15]) + (f"\n… and {len(errors) - 15} more" if len(errors) > 15 else ""))
            else:
                toast(f"Sync complete — {count} operations")
            self.accept()

        def failed(exc):
            prog.close()
            self.run_btn.setEnabled(True)
            if not isinstance(exc, engine.Cancelled):
                QMessageBox.warning(self, "Sync failed", str(exc))
            else:
                self.ran = True
                self.accept()
        w.done.connect(done)
        w.failed.connect(failed)
        w.start()
