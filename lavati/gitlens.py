"""GitLens-style features for Text Compare: per-line blame, file history, commit viewer, line history and
'compare with an earlier version'. The view mixes in GitLensMixin; the dialogs live here too."""
import os
import threading

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QLabel, QMenu, QMessageBox,
                               QPlainTextEdit, QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout)

from . import gitvfs, theme, vfs
from .widgets import toast


def engine_decode(data):
    from . import engine
    return engine.decode_bytes(data)


class TextViewerDialog(QDialog):
    """Read-only monospace text with optional extra buttons: [(label, callback)]."""

    def __init__(self, title, text, parent=None, actions=()):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(860, 560)
        lay = QVBoxLayout(self)
        t = QPlainTextEdit(text)
        t.setReadOnly(True)
        t.setFont(theme.mono(10))
        t.setLineWrapMode(QPlainTextEdit.NoWrap)
        lay.addWidget(t, 1)
        row = QHBoxLayout()
        for label, cb in actions:
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, cb=cb: (self.accept(), cb()))
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("Close")
        close.setProperty("primary", True)
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)


class FileHistoryDialog(QDialog):
    """Every commit that touched one file. Pick a commit and compare it with the current file or its predecessor."""

    def __init__(self, repo, rel, parent=None, on_compare=None, on_show=None):
        super().__init__(parent)
        self.setWindowTitle(f"File history - {rel}")
        self.resize(940, 540)
        self.repo, self.rel = repo, rel
        self.on_compare, self.on_show = on_compare, on_show
        lay = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Commit", "Message", "Author", "Date"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(2, 170)
        self.table.setColumnWidth(3, 150)
        lay.addWidget(self.table, 1)
        self.info = QLabel("Select a commit, or two commits to compare them with each other.")
        self.info.setStyleSheet(f"color:{theme.MUTED};")
        lay.addWidget(self.info)
        row = QHBoxLayout()
        self.b_cur = QPushButton("Compare with current file")
        self.b_prev = QPushButton("Compare with previous version")
        self.b_two = QPushButton("Compare the two selected")
        self.b_show = QPushButton("Show commit")
        for b, cb in ((self.b_cur, self._cur), (self.b_prev, self._prev), (self.b_two, self._two), (self.b_show, self._show)):
            b.clicked.connect(cb)
            row.addWidget(b)
        row.addStretch(1)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.table.itemSelectionChanged.connect(self._sel)
        self.table.doubleClicked.connect(lambda *_: self._cur())
        try:
            self.commits = gitvfs.file_history(repo, rel)
        except gitvfs.GitError as e:
            self.commits = []
            self.info.setText(str(e))
        self.table.setRowCount(len(self.commits))
        for r, (sha, short, author, date, subject, parents) in enumerate(self.commits):
            for c, v in enumerate((short, subject, author, date[:16].replace("T", " "))):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setFont(theme.mono(9))
                self.table.setItem(r, c, it)
        if self.commits:
            self.table.selectRow(0)
        self._sel()

    def _rows(self):
        return sorted({i.row() for i in self.table.selectedIndexes()})

    def _sel(self):
        n = len(self._rows())
        self.b_cur.setEnabled(n == 1)
        self.b_prev.setEnabled(n == 1)
        self.b_show.setEnabled(n == 1)
        self.b_two.setEnabled(n == 2)

    def _cur(self):
        r = self._rows()
        if len(r) == 1 and self.on_compare:
            self.on_compare(self.commits[r[0]][0], "WORKTREE")

    def _prev(self):
        r = self._rows()
        if len(r) == 1 and self.on_compare:
            c = self.commits[r[0]]
            self.on_compare(c[5][0] if c[5] else None, c[0])

    def _two(self):
        r = self._rows()
        if len(r) == 2 and self.on_compare:
            older, newer = self.commits[r[1]][0], self.commits[r[0]][0]          # list is newest first
            self.on_compare(older, newer)

    def _show(self):
        r = self._rows()
        if len(r) == 1 and self.on_show:
            self.on_show(self.commits[r[0]][0])


class GitLensMixin:
    """Mixed into TextCompareView. Needs: self.panes, self.sides, self._focus, a `blameReady` Signal(str, int, object)."""

    def _gl_init(self):
        self.git_info = {"L": None, "R": None}
        self.stage_info = {"L": None, "R": None}
        self.blame_on = False
        self._blame_gen = {"L": 0, "R": 0}
        self._blame_timer = QTimer(self)
        self._blame_timer.setSingleShot(True)
        self._blame_timer.setInterval(450)
        self._blame_timer.timeout.connect(self._blame_refresh)
        self.blameReady.connect(self._blame_arrived)
        for k in ("L", "R"):
            self.panes[k].blameClicked.connect(lambda li, k=k: self.open_commit(k, li))

    def _gl_detect(self):
        """Find out which sides are tracked files of a git repository (cheap: only runs git when it is available)."""
        for k in ("L", "R"):
            s = self.sides[k]
            info = None
            try:
                if s.path and not s.readonly:
                    info = gitvfs.locate(s.path)
            except Exception:
                info = None
            self.git_info[k] = info
        self.b_git.setVisible(any(self.git_info.values()) or any(self.stage_info.values()))
        for k in ("L", "R"):
            try:
                if self.sides[k].path and not self.sides[k].readonly and os.path.getsize(self.sides[k].path) < 1024:
                    with open(self.sides[k].path, "rb") as f:
                        if gitvfs.is_lfs_pointer(f.read()):
                            toast("This file is a Git LFS pointer, not the real content. Install git-lfs to compare the real file.", "warn")
            except OSError:
                pass
        if self.blame_on:
            self._blame_refresh()

    # ------------------------------------------------------------ blame
    def toggle_blame(self, on):
        self.blame_on = bool(on)
        if on and not any(self.git_info.values()):
            toast("Neither file is a tracked file in a git repository", "warn")
            self.blame_on = False
        self._blame_refresh()

    def _blame_refresh(self):
        for k in ("L", "R"):
            info = self.git_info[k]
            pane = self.panes[k]
            if not (self.blame_on and info):
                self._blame_gen[k] += 1
                pane.set_blame(None)
                continue
            self._blame_gen[k] += 1
            gen = self._blame_gen[k]
            contents = ("\n".join(pane.lines) + "\n") if pane.is_modified() else None
            repo, rel = info

            def work(k=k, gen=gen, contents=contents, repo=repo, rel=rel):
                try:
                    data = gitvfs.blame(repo, rel, contents)
                except Exception as e:
                    data = e
                try:
                    self.blameReady.emit(k, gen, data)
                except RuntimeError:             # the tab was closed meanwhile
                    pass
            threading.Thread(target=work, daemon=True).start()

    def _blame_arrived(self, k, gen, data):
        if gen != self._blame_gen[k] or not self.blame_on:
            return
        if isinstance(data, Exception):
            toast(f"Blame is not available: {data}", "warn")
            self.panes[k].set_blame(None)
            return
        n = len(self.panes[k].lines)
        self.panes[k].set_blame((list(data) + [None] * n)[:n])

    def _blame_edited(self):
        if self.blame_on:
            self._blame_timer.start()

    # ------------------------------------------------------------ commits and history
    def open_commit(self, k, li):
        info = self.git_info[k]
        b = self.panes[k].blame[li] if self.panes[k].blame and li < len(self.panes[k].blame) else None
        if not info or not b:
            return
        if set(b["sha"]) == {"0"}:
            toast("This line has local changes that are not committed yet", "info")
            return
        self._show_commit(k, b["sha"])

    def _show_commit(self, k, sha):
        repo, rel = self.git_info[k]
        try:
            text = gitvfs.show_commit(repo, sha)
        except gitvfs.GitError as e:
            QMessageBox.warning(self, "Commit", str(e))
            return
        acts = [("Compare file with previous version", lambda: self._compare_versions(k, "PARENT", sha)),
                ("Compare this version with current file", lambda: self._compare_versions(k, sha, "WORKTREE"))]
        TextViewerDialog(f"Commit {sha[:8]}", text, self, acts).exec()

    def file_history(self, k=None):
        k = k or self._focus
        info = self.git_info[k]
        if not info:
            toast("This file is not tracked by git", "warn")
            return
        FileHistoryDialog(info[0], info[1], self,
                          on_compare=lambda a, b, k=k: self._compare_versions(k, a, b),
                          on_show=lambda sha, k=k: self._show_commit(k, sha)).exec()

    def line_history(self, k=None):
        k = k or self._focus
        info = self.git_info[k]
        if not info:
            toast("This file is not tracked by git", "warn")
            return
        pane = self.panes[k]
        sel = pane.selection()
        a, b = (sel[0][0], sel[1][0]) if sel else (pane.cur_li, pane.cur_li)
        try:
            text = gitvfs.line_history(info[0], info[1], a + 1, b + 1) or "No earlier commits changed these lines."
        except gitvfs.GitError as e:
            text = str(e)
        TextViewerDialog(f"History of lines {a + 1}-{b + 1} - {info[1]}", text, self).exec()

    def compare_with_version(self, rev, k=None):
        """Compare the current file on side k with its committed ('HEAD') or staged ('INDEX') version."""
        k = k or self._focus
        if not self.git_info[k]:
            toast("This file is not tracked by git", "warn")
            return
        self._compare_versions(k, rev, "WORKTREE")

    def _version_file(self, k, rev):
        """(local path, existed) for the file as of `rev` ('WORKTREE' = the file on disk). Missing files give an empty copy."""
        repo, rel = self.git_info[k]
        if rev == "WORKTREE":
            return self.sides[k].path, True
        data = gitvfs.file_at(repo, rev, rel) if rev else None
        p = vfs.new_temp_path(os.path.basename(rel))
        with open(p, "wb") as f:
            f.write(data or b"")
        return p, data is not None

    def _compare_versions(self, k, a, b):
        repo, rel = self.git_info[k]
        try:
            if a == "PARENT":
                parents = gitvfs.run(repo, "rev-list", "--parents", "-n", "1", gitvfs.safe_rev(b), text=True).split()
                a = parents[1] if len(parents) > 1 else None
            pa, ea = self._version_file(k, a)
            pb, eb = self._version_file(k, b)
        except gitvfs.GitError as e:
            QMessageBox.warning(self, "Compare", str(e))
            return
        win = self.window()
        if not hasattr(win, "open_compare"):
            return

        def label(r, existed):
            name = "working file" if r == "WORKTREE" else ("staged" if r == "INDEX" else (r[:8] if r else "no earlier version"))
            return f"{rel} @ {name}" + ("" if existed else " (did not exist)")
        v = win.open_compare("text", pa, pb)
        if v is not None and hasattr(v, "set_titles"):
            v.set_titles(label(a, ea), label(b, eb))
        if v is not None and hasattr(v, "stage_info") and a in ("INDEX", "HEAD"):
            # the left side is an editable copy of the staged / committed file: copy differences into it, then stage it
            v.stage_info["L"] = (repo, rel)
            v.b_git.setVisible(True)

    def _git_menu(self):
        m = QMenu(self)
        act = m.addAction("Show blame (who changed each line)")
        act.setCheckable(True)
        act.setChecked(self.blame_on)
        act.toggled.connect(self.toggle_blame)
        m.addSeparator()
        m.addAction("File history…", self.file_history)
        m.addAction("History of the selected lines…", self.line_history)
        m.addSeparator()
        m.addAction("Compare local changes with the last commit (HEAD)", lambda: self.compare_with_version("HEAD"))
        m.addAction("Compare local changes with the staged version", lambda: self.compare_with_version("INDEX"))
        m.addSeparator()
        m.addAction("Replace this side with the last committed version (undoable)", lambda: self.restore_side("HEAD"))
        m.addAction("Stage this side's text as the file for the next commit", self.stage_side)
        return m

    def restore_side(self, rev="HEAD", k=None):
        """Load the committed version of the file into the pane (an edit you can undo and save, or leave unsaved)."""
        k = k or self._focus
        info = self.git_info.get(k) or (self.stage_info.get(k) if hasattr(self, "stage_info") else None)
        if not info:
            toast("This file is not tracked by git", "warn")
            return
        if self.sides[k].readonly:
            toast("That side is read-only", "warn")
            return
        data = gitvfs.file_at(info[0], rev, info[1])
        if data is None:
            toast("The file does not exist in that version", "warn")
            return
        text, _enc, _eol = engine_decode(data)
        pane = self.panes[k]
        pane.replace_lines(0, len(pane.lines), text.splitlines())
        toast("Replaced with the committed version. Ctrl+Z undoes it; Save writes it.")

    def stage_side(self, k=None):
        """git add for the text shown in this pane, even when it was never saved: stage only the differences you copied in."""
        k = k or self._focus
        info = self.git_info.get(k) or (self.stage_info.get(k) if hasattr(self, "stage_info") else None)
        if not info:
            toast("This file is not tracked by git", "warn")
            return
        pane, s = self.panes[k], self.sides[k]
        nl = {"CRLF": "\r\n", "CR": "\r"}.get(s.eol, "\n")
        text = nl.join(pane.lines) + (nl if pane.lines and s.final_nl else "")
        try:
            gitvfs.stage_blob(info[0], info[1], text.encode("utf-8", "replace"))
        except gitvfs.GitError as e:
            QMessageBox.warning(self, "Stage", str(e))
            return
        toast(f"Staged {info[1]} (the working file on disk is unchanged)")
