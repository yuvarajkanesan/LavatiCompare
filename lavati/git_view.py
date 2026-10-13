"""Git browser dialogs: pick two revisions of a repository (or a merge base) and compare them as folders."""
import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFrame,
                               QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from . import gitvfs, theme
from .widgets import pick_folder

SOURCES = [("worktree", "Working tree"), ("index", "Staged changes (index)"), ("head", "HEAD"), ("branch", "Branch"),
           ("remote", "Remote branch"), ("tag", "Tag"), ("commit", "Commit / revision"), ("stash", "Stash")]


def download_git_with_progress(parent):
    """Fetch a portable Git in the background with a progress bar. Returns True on success."""
    from PySide6.QtWidgets import QProgressDialog
    from .workers import TaskWorker
    dlg = QProgressDialog("Downloading Git\u2026", "Cancel", 0, 100, parent)
    dlg.setWindowTitle("Download Git")
    dlg.setWindowModality(Qt.WindowModal)
    dlg.setMinimumDuration(0)
    result = {}

    def work(ctx):
        def prog(done, total):
            if ctx.cancel:
                raise gitvfs.GitError("Cancelled")
            if total:
                result["pct"] = int(done * 100 / total)
        return gitvfs.download_git(prog)
    w = TaskWorker(work, parent=parent)
    w.done.connect(lambda r: result.update(ok=True))
    w.failed.connect(lambda e: result.update(err=str(e)))
    dlg.canceled.connect(w.cancel)
    w.start()
    from PySide6.QtWidgets import QApplication
    import time
    while w.isRunning():
        dlg.setValue(result.get("pct", 0))
        QApplication.processEvents()
        time.sleep(0.05)
    QApplication.processEvents()
    dlg.close()
    if result.get("err"):
        QMessageBox.warning(parent, "Download Git", result["err"])
    return bool(result.get("ok"))


class HistoryDialog(QDialog):
    """Commit list; pick one commit (compared with its parent) or two commits."""

    def __init__(self, repo, rev="HEAD", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Commit history")
        self.resize(900, 560)
        self.repo = repo
        self.picked = []
        lay = QVBoxLayout(self)
        row = QHBoxLayout()
        self.rev = QLineEdit(rev)
        self.rev.setPlaceholderText("branch, tag or revision, e.g. main or v2.0..main")
        self.rev.returnPressed.connect(self.load)
        b = QPushButton("Show")
        b.clicked.connect(self.load)
        row.addWidget(QLabel("Revision"))
        row.addWidget(self.rev, 1)
        row.addWidget(b)
        lay.addLayout(row)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Commit", "Message", "Author", "Date"])
        self.table.verticalHeader().hide()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        h = self.table.horizontalHeader()
        h.setSectionResizeMode(1, QHeaderView.Stretch)
        self.table.setColumnWidth(0, 90)
        self.table.setColumnWidth(2, 170)
        self.table.setColumnWidth(3, 160)
        lay.addWidget(self.table, 1)
        self.info = QLabel("Select one commit to compare it with its parent, or two commits to compare them.")
        self.info.setStyleSheet(f"color:{theme.MUTED};")
        lay.addWidget(self.info)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Compare")
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        lay.addWidget(bb)
        self.table.doubleClicked.connect(lambda *_: self._ok())
        self.commits = []
        self.load()

    def load(self):
        try:
            self.commits = gitvfs.log(self.repo, self.rev.text().strip() or "HEAD", 1000)
        except gitvfs.GitError as e:
            self.info.setText(str(e))
            return
        self.table.setRowCount(len(self.commits))
        for r, (sha, short, author, date, subject, parents) in enumerate(self.commits):
            for c, v in enumerate((short, subject, author, date[:16].replace("T", " "))):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setFont(theme.mono(9))
                self.table.setItem(r, c, it)
        if not self.commits:
            self.info.setText("No commits found for that revision.")

    def _ok(self):
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows or len(rows) > 2:
            QMessageBox.information(self, "History", "Select one commit (compared with its parent) or two commits.")
            return
        c = [self.commits[r] for r in rows]
        if len(c) == 1:
            parent = c[0][5][0] if c[0][5] else gitvfs.EMPTY_TREE
            self.picked = [parent, c[0][0]]
        else:
            older, newer = (c[1], c[0]) if rows[0] < rows[1] else (c[0], c[1])        # list is newest first
            self.picked = [older[0], newer[0]]
        self.accept()


class GitCompareDialog(QDialog):
    """Choose a repository and what to compare: working tree, index, HEAD, branches, tags, commits, stashes."""

    def __init__(self, parent=None, start_path=""):
        super().__init__(parent)
        self.setWindowTitle("Compare Git revisions")
        self.setMinimumWidth(760)
        self.result_specs = None
        self.three_way = False
        self.refs = {}
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 18)
        lay.setSpacing(8)
        t = QLabel("Git browser")
        t.setStyleSheet("font-size:15pt; font-weight:700;")
        lay.addWidget(t)
        self.note = QLabel("Compare any two states of a repository without checking anything out. "
                           "Changed files are found by comparing Git object ids, so even large repositories open quickly.")
        self.note.setWordWrap(True)
        self.note.setStyleSheet(f"color:{theme.MUTED};")
        lay.addWidget(self.note)
        row = QHBoxLayout()
        self.repo = QLineEdit(gitvfs.find_repo(start_path) if start_path else "")
        self.repo.setProperty("mono", True)
        self.repo.setPlaceholderText("Repository folder (any folder inside it works)")
        self.repo.editingFinished.connect(self._repo_changed)
        b = QPushButton("Browse")
        b.clicked.connect(self._browse)
        row.addWidget(QLabel("Repository"))
        row.addWidget(self.repo, 1)
        row.addWidget(b)
        lay.addLayout(row)
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.b_dl = QPushButton("Download Git")
        self.b_dl.setVisible(False)
        self.b_dl.clicked.connect(self._download)
        lay.addWidget(self.b_dl, 0, Qt.AlignLeft)
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        self.src, self.val, self.hist = {}, {}, {}
        for i, (side, title) in enumerate((("L", "LEFT"), ("R", "RIGHT"))):
            lab = QLabel(title)
            lab.setStyleSheet(f"color:{theme.MUTED}; font-weight:700; font-size:8pt; letter-spacing:1px;")
            src = QComboBox()
            for key, label in SOURCES:
                src.addItem(label, key)
            val = QComboBox()
            val.setEditable(True)
            val.setMinimumWidth(260)
            hb = QPushButton("History…")
            hb.clicked.connect(lambda _=False, s=side: self._history(s))
            src.currentIndexChanged.connect(lambda _=0, s=side: self._fill(s))
            grid.addWidget(lab, 0, i * 3, 1, 3)
            grid.addWidget(src, 1, i * 3)
            grid.addWidget(val, 1, i * 3 + 1)
            grid.addWidget(hb, 1, i * 3 + 2)
            self.src[side], self.val[side], self.hist[side] = src, val, hb
        self.src["L"].setCurrentIndex(2)
        self.src["R"].setCurrentIndex(0)
        lay.addLayout(grid)
        self.c_renames = QCheckBox("Detect renamed files (git -M)")
        self.c_renames.setChecked(True)
        lay.addWidget(self.c_renames)
        self.c_base = QCheckBox("Use the merge base of the two sides as the left side  (what a merge of right into left would change)")
        lay.addWidget(self.c_base)
        quick = QHBoxLayout()
        ql = QLabel("Quick compare")
        ql.setStyleSheet(f"color:{theme.MUTED}; font-weight:700; font-size:8pt; letter-spacing:1px;")
        quick.addWidget(ql)
        for text, tip, left, right in (
                ("Local changes vs last commit", "Everything you changed since the last commit (working tree against HEAD)", 2, 0),
                ("Staged vs last commit", "What the next commit will contain (index against HEAD)", 2, 1),
                ("Local changes vs staged", "Changes you have not staged yet (working tree against the index)", 1, 0)):
            qb = QPushButton(text)
            qb.setToolTip(tip)
            qb.clicked.connect(lambda _=False, l=left, r=right: self._preset(l, r))
            quick.addWidget(qb)
        quick.addStretch(1)
        lay.addLayout(quick)
        sep = QFrame()
        sep.setFixedHeight(1)
        sep.setStyleSheet(f"background:{theme.BORDER};")
        lay.addWidget(sep)
        row = QHBoxLayout()
        self.b_quick = QPushButton("Compare a commit with its parent…")
        self.b_quick.clicked.connect(self._commit_vs_parent)
        self.b_merge = QPushButton("Three-way folder merge (left, merge base, right)")
        self.b_merge.clicked.connect(lambda: self._go(True))
        c = QPushButton("Cancel")
        c.clicked.connect(self.reject)
        ok = QPushButton("Compare")
        ok.setProperty("primary", True)
        ok.setMinimumHeight(38)
        ok.clicked.connect(lambda: self._go(False))
        row.addWidget(self.b_quick)
        row.addWidget(self.b_merge)
        row.addStretch(1)
        row.addWidget(c)
        row.addWidget(ok)
        lay.addLayout(row)
        self._repo_changed()

    # ---------------------------------------------------------------- state
    def _download(self):
        download_git_with_progress(self)
        self._repo_changed()

    def _browse(self):
        d = pick_folder(self, "Repository folder", self.repo.text())
        if d:
            self.repo.setText(os.path.normpath(d))
            self._repo_changed()

    def _repo_changed(self):
        text = self.repo.text().strip()
        msg = gitvfs.check_git()
        self.b_dl.setVisible(bool(msg) and gitvfs.can_download_git())
        if msg:
            self.status.setText(f"<span style='color:{theme.RED}'>{msg}</span>")
            return
        top = gitvfs.find_repo(text) if text else ""
        if not top:
            self.status.setText(f"<span style='color:{theme.MUTED}'>{'Not inside a Git repository.' if text else ''}</span>")
            self.refs = {}
        else:
            if top != text:
                self.repo.setText(top)
            v = gitvfs.version()
            br = gitvfs.current_branch(top)
            self.status.setText(f"<span style='color:{theme.GREEN}'>Repository found</span> · "
                                + (f"on branch <b>{br}</b>" if br else "<b>detached HEAD</b>") + f" · git {v[0]}.{v[1]}.{v[2]}")
            try:
                self.refs = gitvfs.list_refs(top)
            except gitvfs.GitError as e:
                self.refs = {}
                self.status.setText(str(e))
        for s in "LR":
            self._fill(s)

    def _fill(self, side):
        kind = self.src[side].currentData()
        val = self.val[side]
        val.clear()
        items = {"branch": self.refs.get("branches", []), "remote": self.refs.get("remotes", []),
                 "tag": self.refs.get("tags", []), "stash": [s.split()[0] for s in self.refs.get("stashes", [])]}.get(kind, [])
        val.addItems(items)
        fixed = kind in ("worktree", "index", "head")
        val.setEnabled(not fixed)
        self.hist[side].setEnabled(kind in ("commit", "branch", "remote", "tag", "head"))
        if kind == "commit":
            val.setEditText("HEAD~1")
        elif kind == "branch":
            cur = gitvfs.current_branch(self.repo.text()) if self.refs else ""
            if cur in items:
                val.setCurrentText(cur)

    def rev_of(self, side):
        kind = self.src[side].currentData()
        if kind == "worktree":
            return "WORKTREE"
        if kind == "index":
            return "INDEX"
        if kind == "head":
            return "HEAD"
        return self.val[side].currentText().strip() or "HEAD"

    def _history(self, side):
        repo = self.repo.text().strip()
        if not repo:
            return
        kind = self.src[side].currentData()
        start = self.val[side].currentText().strip() if kind in ("branch", "remote", "tag") else "HEAD"
        dlg = HistoryDialog(repo, start or "HEAD", self)
        if dlg.exec() and dlg.picked:
            older, newer = dlg.picked
            for s, rev in (("L", older), ("R", newer)):
                self.src[s].setCurrentIndex(self.src[s].findData("commit"))
                self.val[s].setEditText(rev)

    def _commit_vs_parent(self):
        self._history("R")

    def _preset(self, left_idx, right_idx):
        """Set both sides (indexes into SOURCES) and compare straight away."""
        self.c_base.setChecked(False)
        self.src["L"].setCurrentIndex(left_idx)
        self.src["R"].setCurrentIndex(right_idx)
        self._go(False)

    def _go(self, three_way):
        repo = self.repo.text().strip()
        top = gitvfs.find_repo(repo) if repo else ""
        if not top:
            QMessageBox.warning(self, "Git", "Choose a folder inside a Git repository first.")
            return
        a, b = self.rev_of("L"), self.rev_of("R")
        try:
            for rev in (a, b):
                if rev not in gitvfs.SPECIAL and not gitvfs.resolve(top, rev):
                    raise gitvfs.GitError(f"'{rev}' is not a known branch, tag or commit.")
            base = None
            if three_way or self.c_base.isChecked():
                for rev in (a, b):
                    if rev in gitvfs.SPECIAL:
                        raise gitvfs.GitError("A merge base needs two commits, branches or tags (not the working tree or index).")
                base = gitvfs.merge_base(top, a, b)
        except gitvfs.GitError as e:
            QMessageBox.warning(self, "Git", str(e))
            return
        self.three_way = three_way
        if three_way:
            self.result_specs = (gitvfs.make_spec(top, a), gitvfs.make_spec(top, base), gitvfs.make_spec(top, b))
        else:
            left = base if self.c_base.isChecked() else a
            self.result_specs = (gitvfs.make_spec(top, left), gitvfs.make_spec(top, b))
        self.detect_renames = self.c_renames.isChecked()
        self.accept()
