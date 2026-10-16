"""Git extras for the folder comparison: branch summary (ahead/behind, commits, merge preview, graph), fetch, restore,
stage, and filtering by author or commit message. FolderCompareView mixes in GitFolderMixin."""
import os
import threading
import time

from PySide6.QtWidgets import (QAbstractItemView, QDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel, QMessageBox,
                               QPlainTextEdit, QPushButton, QSplitter, QTableWidget, QTableWidgetItem, QTabWidget, QVBoxLayout,
                               QWidget)
from PySide6.QtCore import Qt

from . import config, engine, gitvfs, theme
from .gitlens import TextViewerDialog
from .widgets import toast
from .workers import run_responsive


def plan_merge(repo, a, b):
    """(ours, base, theirs, output, label) for a three-way folder merge of these two sides, or None when not possible."""
    spec = gitvfs.make_spec
    special = gitvfs.SPECIAL
    if "INDEX" in (a, b):
        return None
    if "WORKTREE" in (a, b):
        other = b if a == "WORKTREE" else a
        if other in special:
            return None
        base = gitvfs.merge_base(repo, "HEAD", other)
        return spec(repo, "WORKTREE"), spec(repo, base), spec(repo, other), repo, "Merge into my working tree…"
    base = gitvfs.merge_base(repo, a, b)
    return spec(repo, b), spec(repo, base), spec(repo, a), None, "Three-way merge (left into right)…"


def _ago(t):
    if not t:
        return "never"
    d = time.time() - t
    for secs, unit in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if d >= secs:
            n = int(d // secs)
            return f"{n} {unit}{'s' if n != 1 else ''} ago"
    return "just now"


class GitSummaryDialog(QDialog):
    """How two revisions relate: ahead/behind, the commits on each side, a dry-run merge and the commit graph."""

    def __init__(self, repo, a, b, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Git summary")
        self.resize(940, 640)
        self.repo, self.a, self.b = repo, a, b
        self.merge_request = None
        lay = QVBoxLayout(self)
        self.head = QLabel()
        self.head.setTextFormat(Qt.RichText)
        self.head.setWordWrap(True)
        lay.addWidget(self.head)
        row = QHBoxLayout()
        self.fetched = QLabel()
        self.fetched.setStyleSheet(f"color:{theme.MUTED};")
        self.b_fetch = QPushButton("Fetch from remote")
        self.b_fetch.setToolTip("git fetch --all --prune: update the remote branches before looking at them")
        self.b_fetch.clicked.connect(self._fetch)
        row.addWidget(self.fetched, 1)
        row.addWidget(self.b_fetch)
        lay.addLayout(row)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        # commits
        split = QSplitter(Qt.Vertical)
        self.t_left, self.l_left = self._table()
        self.t_right, self.l_right = self._table()
        for lab, tab in ((self.l_left, self.t_left), (self.l_right, self.t_right)):
            w = QWidget()
            v = QVBoxLayout(w)
            v.setContentsMargins(0, 0, 0, 0)
            v.addWidget(lab)
            v.addWidget(tab, 1)
            split.addWidget(w)
        self.tabs.addTab(split, "Commits")
        # merge preview
        mp = QWidget()
        v = QVBoxLayout(mp)
        self.merge_text = QPlainTextEdit()
        self.merge_text.setReadOnly(True)
        self.merge_text.setFont(theme.mono(10))
        v.addWidget(self.merge_text, 1)
        self.b_merge = QPushButton()
        self.b_merge.setProperty("primary", True)
        self.b_merge.clicked.connect(self._merge)
        v.addWidget(self.b_merge, 0, Qt.AlignLeft)
        self.tabs.addTab(mp, "Merge preview")
        # graph
        self.graph = QPlainTextEdit()
        self.graph.setReadOnly(True)
        self.graph.setFont(theme.mono(10))
        self.graph.setLineWrapMode(QPlainTextEdit.NoWrap)
        self.tabs.addTab(self.graph, "Graph")
        close = QPushButton("Close")
        close.clicked.connect(self.reject)
        lay.addWidget(close, 0, Qt.AlignRight)
        self.reload()

    def _table(self):
        lab = QLabel()
        lab.setStyleSheet("font-weight:700;")
        t = QTableWidget(0, 4)
        t.setHorizontalHeaderLabels(["Commit", "Message", "Author", "Date"])
        t.verticalHeader().hide()
        t.setEditTriggers(QAbstractItemView.NoEditTriggers)
        t.setSelectionBehavior(QAbstractItemView.SelectRows)
        t.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        t.setColumnWidth(0, 80)
        t.setColumnWidth(2, 160)
        t.setColumnWidth(3, 140)
        return t, lab

    @staticmethod
    def _fill(table, commits):
        table.setRowCount(len(commits))
        for r, (sha, short, author, date, subject, parents) in enumerate(commits):
            for c, v in enumerate((short, subject, author, date[:16].replace("T", " "))):
                table.setItem(r, c, QTableWidgetItem(v))

    def _name(self, rev):
        return {"WORKTREE": "working tree", "INDEX": "staged changes"}.get(rev, rev)

    def reload(self):
        repo, a, b = self.repo, self.a, self.b
        try:
            ahead, behind = gitvfs.ahead_behind(repo, a, b)
            only_a = gitvfs.commits_only_in(repo, a, b)
            only_b = gitvfs.commits_only_in(repo, b, a)
        except gitvfs.GitError as e:
            self.head.setText(f"<span style='color:{theme.RED}'>{e}</span>")
            return
        na, nb = self._name(a), self._name(b)
        wt = []
        if "WORKTREE" in (a, b) or "INDEX" in (a, b):
            wt.append("<br><span style='color:%s'>The working tree and staged changes sit on top of HEAD, so only commits are counted here; "
                      "uncommitted edits show in the file comparison.</span>" % theme.MUTED)
        try:
            subs = max(gitvfs.count_submodules(repo, a), gitvfs.count_submodules(repo, b))
        except gitvfs.GitError:
            subs = 0
        if subs:
            wt.append(f"<br><span style='color:{theme.AMBER}'>{subs} submodule(s): they appear as a single commit-id entry; "
                      "the files inside them are not compared (open the submodule's own folder for that).</span>")
        self.head.setText(f"<b>LEFT</b> {na} &nbsp;&nbsp; <b>RIGHT</b> {nb}<br>"
                          f"LEFT has <b>{ahead}</b> commit(s) that RIGHT does not, RIGHT has <b>{behind}</b> that LEFT does not." + "".join(wt))
        self.fetched.setText(f"Remote branches last fetched: {_ago(gitvfs.last_fetch_time(repo))}")
        self.l_left.setText(f"Only in LEFT ({na}): {len(only_a)}")
        self.l_right.setText(f"Only in RIGHT ({nb}): {len(only_b)}")
        self._fill(self.t_left, only_a)
        self._fill(self.t_right, only_b)
        try:
            pv = gitvfs.merge_preview(repo, a, b)
            if pv["clean"]:
                txt = "A merge of these two would apply cleanly: no conflicting files."
            else:
                txt = f"{len(pv['conflicts'])} file(s) would conflict:\n\n" + "\n".join("  " + c for c in pv["conflicts"])
            if pv["messages"]:
                txt += "\n\n" + pv["messages"]
        except gitvfs.GitError as e:
            txt = str(e)
        self.merge_text.setPlainText(txt)
        try:
            self.plan = plan_merge(repo, a, b)
        except gitvfs.GitError as e:
            self.plan = None
            txt += f"\n\n{e}"
            self.merge_text.setPlainText(txt)
        self.b_merge.setVisible(self.plan is not None)
        if self.plan:
            self.b_merge.setText(self.plan[4])
        self.graph.setPlainText(gitvfs.graph_text(repo))

    def _fetch(self):
        try:
            run_responsive(gitvfs.fetch, self.repo)
        except gitvfs.GitError as e:
            QMessageBox.warning(self, "Fetch", str(e))
            return
        toast("Fetched. Re-open the comparison to see the new remote state.")
        self.reload()

    def _merge(self):
        if self.plan:
            self.merge_request = self.plan[:4]
            self.accept()


class GitFolderMixin:
    """Mixed into FolderCompareView. Needs signals gitWho(object) and openMerge(str, str, str, str)."""

    def _gf_detect(self):
        self.git_repo, self.git_revs = "", None
        self.git_filter = ""
        self.git_who = {}
        if gitvfs.is_git_spec(self.left) or gitvfs.is_git_spec(self.right):
            ra = gitvfs.parse_spec(self.left) if gitvfs.is_git_spec(self.left) else None
            rb = gitvfs.parse_spec(self.right) if gitvfs.is_git_spec(self.right) else None
            repo = (ra or rb)[0]
            self.git_repo = repo
            self.git_revs = (ra[1] if ra else "HEAD", rb[1] if rb else "HEAD")

    def _gf_connect(self):
        self.gitWho.connect(self._gf_who_ready)
        self.loaded.connect(self._gf_load_who)

    def _gf_load_who(self):
        if not self.git_repo:
            return
        repo, (a, b) = self.git_repo, self.git_revs

        def work():
            try:
                who = gitvfs.changed_files(repo, a, b)
            except Exception:
                who = {}
            try:
                self.gitWho.emit(who)
            except RuntimeError:                       # tab closed meanwhile
                pass
        threading.Thread(target=work, daemon=True).start()

    def _gf_who_ready(self, who):
        self.git_who = who
        for rel, it in list(self.items.items()):
            n = it.data(0, Qt.UserRole)
            self._git_tip(it, n)

    def _git_tip(self, it, n):
        w = self.git_who.get(n.rel) if self.git_repo and not n.is_dir else None
        if not w:
            return
        authors = ", ".join(dict.fromkeys(x[0] for x in w))
        tip = f"Changed by {authors}\n" + "\n".join(f"{x[2]}  {x[1]}  ({x[0]})" for x in w[:6]) + ("\n…" if len(w) > 6 else "")
        for c in (0, 4):
            it.setToolTip(c, tip)

    def _match_git(self, n):
        if not self.git_filter or n.is_dir:
            return True
        f = self.git_filter.lower()
        return any(f in a.lower() or f in s.lower() for a, s, _ in self.git_who.get(n.rel, ()))

    # ------------------------------------------------------------ actions
    def git_summary(self):
        a, b = self.git_revs
        dlg = GitSummaryDialog(self.git_repo, a, b, self)
        if dlg.exec() and dlg.merge_request:
            self.openMerge.emit(*[x or "" for x in dlg.merge_request])

    def git_fetch(self):
        try:
            run_responsive(gitvfs.fetch, self.git_repo)
        except gitvfs.GitError as e:
            QMessageBox.warning(self, "Fetch", str(e))
            return
        toast("Fetched from the remote")
        self.refresh()

    def git_graph(self):
        TextViewerDialog("Commit graph", gitvfs.graph_text(self.git_repo, 600), self).exec()

    def git_filter_dialog(self):
        text, ok = QInputDialog.getText(self, "Filter by author or message",
                                        "Show only files touched by commits whose author or message contains:",
                                        text=self.git_filter)
        if not ok:
            return
        self.git_filter = text.strip()
        self._populate()
        toast(f"Showing files changed by '{self.git_filter}'" if self.git_filter else "Filter cleared")

    def _gf_files(self):
        rels = []
        for n in self._sel_pruned():
            for f in ([n] if not n.is_dir else engine.flatten([n])):
                if not f.is_dir:
                    rels.append(f.rel)
        return sorted(set(rels))

    def _gf_need_worktree(self):
        if not self.git_revs or "WORKTREE" not in self.git_revs:
            toast("This needs the working tree on one side", "warn")
            return False
        return True

    def git_restore(self, rev):
        if not self._gf_need_worktree():
            return
        rels = self._gf_files()
        if not rels:
            QMessageBox.information(self, "Restore", "Select one or more files first.")
            return
        name = "the last commit (HEAD)" if rev == "HEAD" else rev
        if QMessageBox.question(self, "Restore", f"Put {len(rels)} file(s) back to how they are in {name}?\n\n"
                                "Your current version of each file is backed up first. Nothing is committed.") != QMessageBox.Yes:
            return
        bdir = os.path.join(self.cfg["sync_backup_dir"] or os.path.join(config.APP_DIR, "backups"), time.strftime("restore-%Y%m%d-%H%M%S"))
        try:
            n = gitvfs.restore_paths(self.git_repo, rev, rels, bdir)
        except (gitvfs.GitError, OSError) as e:
            QMessageBox.warning(self, "Restore", str(e))
            return
        toast(f"{n} file(s) restored; backups in {bdir}")
        self.refresh()

    def git_stage(self, on=True):
        if not self._gf_need_worktree():
            return
        rels = self._gf_files()
        if not rels:
            QMessageBox.information(self, "Stage", "Select one or more files first.")
            return
        try:
            (gitvfs.stage_paths if on else gitvfs.unstage_paths)(self.git_repo, rels)
        except gitvfs.GitError as e:
            QMessageBox.warning(self, "Git", str(e))
            return
        toast(f"{len(rels)} file(s) {'staged' if on else 'unstaged'}")

    def git_menu(self, menu):
        menu.addAction("Summary: commits, ahead/behind, merge preview…", self.git_summary)
        menu.addAction("Commit graph…", self.git_graph)
        menu.addAction("Fetch from the remote and refresh", self.git_fetch)
        menu.addSeparator()
        menu.addAction("Filter by author or commit message…", self.git_filter_dialog)
        menu.addAction("Clear the author / message filter", lambda: (setattr(self, "git_filter", ""), self._populate()))
        menu.addSeparator()
        menu.addAction("Restore selected files to the last commit (discard local changes)", lambda: self.git_restore("HEAD"))
        menu.addAction("Stage selected files", lambda: self.git_stage(True))
        menu.addAction("Unstage selected files", lambda: self.git_stage(False))
