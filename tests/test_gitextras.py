"""Fetch, ahead/behind, merge preview, restore/stage, author filter, summary dialog, LFS."""
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
from lavati import config, gitvfs  # noqa: E402

HAVE_GIT = bool(gitvfs.git_path()) and not gitvfs.check_git()


def git(repo, *args, author="Ann"):
    env = dict(os.environ, GIT_AUTHOR_NAME=author, GIT_AUTHOR_EMAIL="a@x", GIT_COMMITTER_NAME=author, GIT_COMMITTER_EMAIL="a@x",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    r = subprocess.run(["git", "-C", repo, "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def put(repo, rel, text):
    p = os.path.join(repo, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", newline="") as f:
        f.write(text)


def get(repo, rel):
    with open(os.path.join(repo, *rel.split("/")), newline="") as f:
        return f.read()


def wait(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


@unittest.skipUnless(HAVE_GIT, "git 2.30+ not available")
class RemoteWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = d = tempfile.mkdtemp(prefix="lavati-extras-")
        seed = os.path.join(d, "seed")
        os.makedirs(seed)
        git(seed, "init", "-q", "-b", "main")
        put(seed, "a.txt", "one\ntwo\nthree\n")
        put(seed, "b.txt", "bee\n")
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", "base")
        cls.origin = os.path.join(d, "origin.git")
        subprocess.run(["git", "-c", "core.autocrlf=false", "clone", "-q", "--bare", seed, cls.origin], check=True, capture_output=True)
        cls.work = os.path.join(d, "work")
        subprocess.run(["git", "-c", "core.autocrlf=false", "clone", "-q", cls.origin, cls.work], check=True, capture_output=True)
        # a teammate pushes to origin: edits a.txt, adds c.txt
        mate = os.path.join(d, "mate")
        subprocess.run(["git", "-c", "core.autocrlf=false", "clone", "-q", cls.origin, mate], check=True, capture_output=True)
        put(mate, "a.txt", "one\nTWO by bob\nthree\n")
        put(mate, "c.txt", "new file\n")
        git(mate, "add", "-A")
        git(mate, "commit", "-q", "-m", "bob edits a and adds c", author="Bob")
        git(mate, "push", "-q", "origin", "main")
        # local work in the clone (same line of a.txt, so a merge conflicts)
        put(cls.work, "a.txt", "one\nTWO by ann\nthree\n")
        git(cls.work, "commit", "-q", "-am", "ann edits a", author="Ann")
        cls.cfg = config.Config(os.path.join(d, "cfg.json"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def test_1_fetch_then_ahead_behind(self):
        self.assertEqual(gitvfs.ahead_behind(self.work, "HEAD", "origin/main"), (1, 0))      # stale until fetched: the remote's new commit is not known yet
        gitvfs.fetch(self.work)
        self.assertGreater(gitvfs.last_fetch_time(self.work), 0)
        self.assertEqual(gitvfs.ahead_behind(self.work, "HEAD", "origin/main"), (1, 1))
        mine = gitvfs.commits_only_in(self.work, "HEAD", "origin/main")
        theirs = gitvfs.commits_only_in(self.work, "origin/main", "HEAD")
        self.assertEqual([c[4] for c in mine], ["ann edits a"])
        self.assertEqual([c[4] for c in theirs], ["bob edits a and adds c"])

    def test_2_merge_preview_reports_conflicts(self):
        gitvfs.fetch(self.work)
        pv = gitvfs.merge_preview(self.work, "HEAD", "origin/main")
        self.assertFalse(pv["clean"])
        self.assertEqual(pv["conflicts"], ["a.txt"])
        self.assertTrue(gitvfs.merge_preview(self.work, "HEAD", "HEAD")["clean"])

    def test_3_who_changed_files_and_filter(self):
        gitvfs.fetch(self.work)
        who = gitvfs.changed_files(self.work, "HEAD", "origin/main")
        self.assertEqual({a for a, _s, _h in who["a.txt"]}, {"Ann", "Bob"})
        self.assertEqual([a for a, _s, _h in who["c.txt"]], ["Bob"])
        from lavati.folder_view import FolderCompareView
        v = FolderCompareView(self.cfg, gitvfs.make_spec(self.work, "origin/main"), gitvfs.make_spec(self.work, "WORKTREE"))
        v.show()
        self.assertTrue(wait(lambda: v.generation > 0, 15))
        self.assertTrue(wait(lambda: bool(v.git_who), 10))
        v.filter_key = "all"
        v.git_filter = "bob"
        v._populate()
        shown = set(v.items)
        self.assertIn("c.txt", shown)
        self.assertNotIn("b.txt", shown)                       # nobody in the range touched it
        self.assertIn("Bob", v.items["c.txt"].toolTip(0))
        v.close()

    def test_4_summary_dialog_and_merge_plan(self):
        from lavati.gitsummary import GitSummaryDialog, plan_merge
        gitvfs.fetch(self.work)
        dlg = GitSummaryDialog(self.work, "origin/main", "WORKTREE")
        self.assertEqual(dlg.t_left.rowCount(), 1)
        self.assertEqual(dlg.t_right.rowCount(), 1)
        self.assertIn("a.txt", dlg.merge_text.toPlainText())
        self.assertIn("ann edits a", dlg.graph.toPlainText())
        ours, base, theirs, out, label = plan_merge(self.work, "origin/main", "WORKTREE")
        self.assertEqual((ours, theirs), (gitvfs.make_spec(self.work, "WORKTREE"), gitvfs.make_spec(self.work, "origin/main")))
        self.assertEqual(out, self.work)
        self.assertIn("working tree", label)
        self.assertIsNone(plan_merge(self.work, "INDEX", "HEAD"))
        dlg._merge()
        self.assertIsNotNone(dlg.merge_request)


@unittest.skipUnless(HAVE_GIT, "git 2.30+ not available")
class LocalFileActionTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="lavati-act-")
        git(self.d, "init", "-q", "-b", "main")
        put(self.d, "f.txt", "v1\nsame\n")
        put(self.d, "sub/g.txt", "g1\n")
        git(self.d, "add", "-A")
        git(self.d, "commit", "-q", "-m", "c1")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_restore_with_backup_and_removal(self):
        put(self.d, "f.txt", "LOCAL EDIT\n")
        put(self.d, "extra.txt", "untracked\n")
        bk = os.path.join(self.d, "..", "bk-" + os.path.basename(self.d))
        n = gitvfs.restore_paths(self.d, "HEAD", ["f.txt", "extra.txt"], bk)
        self.assertEqual(n, 2)
        self.assertEqual(get(self.d, "f.txt"), "v1\nsame\n")
        self.assertFalse(os.path.exists(os.path.join(self.d, "extra.txt")))       # did not exist in HEAD
        self.assertEqual(get(bk, "f.txt"), "LOCAL EDIT\n")
        shutil.rmtree(bk, ignore_errors=True)
        with self.assertRaises(gitvfs.GitError):
            gitvfs.restore_paths(self.d, "HEAD", ["../outside.txt"])

    def test_stage_unstage_and_stage_blob(self):
        put(self.d, "f.txt", "v2\nsame\n")
        gitvfs.stage_paths(self.d, ["f.txt"])
        self.assertEqual(git(self.d, "diff", "--cached", "--name-only"), "f.txt")
        gitvfs.unstage_paths(self.d, ["f.txt"])
        self.assertEqual(git(self.d, "diff", "--cached", "--name-only"), "")
        # stage different text than the working file holds (only the differences copied across)
        gitvfs.stage_blob(self.d, "f.txt", b"v1\nSTAGED ONLY\n")
        self.assertEqual(git(self.d, "show", ":f.txt"), "v1\nSTAGED ONLY")
        self.assertEqual(get(self.d, "f.txt"), "v2\nsame\n")                      # working file untouched
        with self.assertRaises(gitvfs.GitError):
            gitvfs.stage_paths(self.d, [".git/config"])

    def test_lfs_and_submodule_helpers(self):
        self.assertTrue(gitvfs.is_lfs_pointer(b"version https://git-lfs.github.com/spec/v1\noid sha256:ab\nsize 3\n"))
        self.assertFalse(gitvfs.is_lfs_pointer(b"hello"))
        self.assertEqual(gitvfs.count_submodules(self.d, "HEAD"), 0)
        sha = git(self.d, "rev-parse", "HEAD")
        git(self.d, "update-index", "--add", "--cacheinfo", f"160000,{sha},vendor/lib")
        git(self.d, "commit", "-q", "-m", "gitlink")
        self.assertEqual(gitvfs.count_submodules(self.d, "HEAD"), 1)

    def test_text_view_restore_and_stage_side(self):
        from lavati.text_view import TextCompareView
        cfg = config.Config(os.path.join(self.d, "cfg.json"))
        f = os.path.join(self.d, "f.txt")
        put(self.d, "f.txt", "EDITED\nsame\n")
        other = os.path.join(self.d, "other.txt")
        put(self.d, "other.txt", "x\n")
        v = TextCompareView(cfg, f, other)
        v.show()
        v.restore_side("HEAD", "L")
        v._flush()
        self.assertEqual(v.panes["L"].lines, ["v1", "same"])
        self.assertEqual(get(self.d, "f.txt"), "EDITED\nsame\n")                  # nothing written until you save
        v.panes["L"].replace_lines(0, 1, ["v1 plus"])
        v.stage_side("L")
        self.assertEqual(git(self.d, "show", ":f.txt"), "v1 plus\nsame")
        v.close()


if __name__ == "__main__":
    unittest.main()
