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
from lavati.text_view import TextCompareView  # noqa: E402

HAVE_GIT = bool(gitvfs.git_path()) and not gitvfs.check_git()


def git(repo, *args, author="Ann"):
    env = dict(os.environ, GIT_AUTHOR_NAME=author, GIT_AUTHOR_EMAIL="a@x", GIT_COMMITTER_NAME=author, GIT_COMMITTER_EMAIL="a@x",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    r = subprocess.run(["git", "-C", repo, "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def wait(cond, timeout=10):
    end = time.time() + timeout
    while time.time() < end:
        app.processEvents()
        if cond():
            return True
        time.sleep(0.02)
    return False


@unittest.skipUnless(HAVE_GIT, "git 2.30+ not available")
class GitLensTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="lavati-lens-")
        cls.r = r = os.path.join(cls.d, "repo")
        os.makedirs(r)
        git(r, "init", "-q", "-b", "main")
        cls.f = os.path.join(r, "a.txt")
        open(cls.f, "w").write("alpha\nbeta\ngamma\n")
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", "first", author="Ann")
        open(cls.f, "w").write("alpha\nBETA changed\ngamma\ndelta\n")
        git(r, "commit", "-q", "-am", "second: change beta, add delta", author="Bob")
        cls.cfg = config.Config(os.path.join(cls.d, "cfg.json"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.d, ignore_errors=True)

    def other(self, text):
        p = os.path.join(self.d, "other.txt")
        open(p, "w").write(text)
        return p

    def test_blame_authors_per_line(self):
        repo, rel = gitvfs.locate(self.f)
        b = gitvfs.blame(repo, rel)
        self.assertEqual([x["author"] for x in b], ["Ann", "Bob", "Ann", "Bob"])
        self.assertIn("second", b[1]["summary"])
        b2 = gitvfs.blame(repo, rel, contents="alpha\nBETA changed\nEDITED\ndelta\n")
        self.assertEqual(b2[2]["author"], "Not committed yet")

    def test_untracked_and_outside_files_have_no_git_info(self):
        self.assertIsNone(gitvfs.locate(self.other("x\n")))

    def test_history_and_versions(self):
        repo, rel = gitvfs.locate(self.f)
        hist = gitvfs.file_history(repo, rel)
        self.assertEqual([h[4][:6] for h in hist], ["second", "first"])
        self.assertEqual(gitvfs.file_at(repo, hist[1][0], rel).replace(b"\r\n", b"\n"), b"alpha\nbeta\ngamma\n")
        self.assertIsNone(gitvfs.file_at(repo, hist[1][0], "nope.txt"))
        self.assertIn("BETA", gitvfs.line_history(repo, rel, 2, 2))
        self.assertIn("Bob", gitvfs.show_commit(repo, hist[0][0]))
        with self.assertRaises(gitvfs.GitError):
            gitvfs.show_commit(repo, "--output=x")

    def test_view_blame_column(self):
        v = TextCompareView(self.cfg, self.f, self.other("alpha\nbeta\n"))
        v.resize(1300, 600)
        v.show()
        self.assertTrue(v.b_git.isVisibleTo(v))
        v.toggle_blame(True)
        self.assertTrue(wait(lambda: v.panes["L"].blame is not None))
        pane = v.panes["L"]
        self.assertGreater(pane.blame_w, 0)
        self.assertEqual(pane.blame[1]["author"], "Bob")
        self.assertFalse(pane.grab().isNull())                       # paints without errors
        self.assertIsNone(v.panes["R"].blame)                        # the other file is not in git
        v.toggle_blame(False)
        self.assertEqual(pane.blame_w, 0)
        v.close()

    def test_blame_follows_edits(self):
        v = TextCompareView(self.cfg, self.f, self.other("alpha\n"))
        v.show()
        v.toggle_blame(True)
        self.assertTrue(wait(lambda: v.panes["L"].blame is not None))
        v.panes["L"].replace_lines(0, 1, ["brand new first line"])
        self.assertTrue(wait(lambda: v.panes["L"].blame and v.panes["L"].blame[0]["author"] == "Not committed yet", 6))
        v.close()

    def test_combine_keeps_both_versions(self):
        a = os.path.join(self.d, "ca.txt")
        b = os.path.join(self.d, "cb.txt")
        open(a, "w").write("top\nLEFT line\nbottom\n")
        open(b, "w").write("top\nRIGHT line\nbottom\n")
        v = TextCompareView(self.cfg, a, b)
        v.show()
        v.combine("L", 0)
        wait(lambda: False, 0.3)
        self.assertEqual(v.panes["L"].lines, ["top", "LEFT line", "RIGHT line", "bottom"])
        self.assertEqual(v.panes["R"].lines, v.panes["L"].lines)
        v.close()
        v = TextCompareView(self.cfg, a, b)
        v.show()
        v.combine("R", 0)
        self.assertEqual(v.panes["R"].lines, ["top", "RIGHT line", "LEFT line", "bottom"])
        v.close()

    def test_combine_all_and_readonly(self):
        a = os.path.join(self.d, "ma.txt")
        b = os.path.join(self.d, "mb.txt")
        open(a, "w").write("1\nL1\n2\n3\nL2\n4\n")
        open(b, "w").write("1\nR1\n2\n3\nR2\n4\n")
        v = TextCompareView(self.cfg, a, b)
        v.show()
        import lavati.text_view as tv
        orig = tv.QMessageBox.question
        tv.QMessageBox.question = staticmethod(lambda *a, **k: tv.QMessageBox.Yes)
        try:
            v.combine_all("L")
        finally:
            tv.QMessageBox.question = orig
        self.assertEqual(v.panes["L"].lines, ["1", "L1", "R1", "2", "3", "L2", "R2", "4"])
        self.assertEqual(v.panes["R"].lines, v.panes["L"].lines)
        v.close()


if __name__ == "__main__":
    unittest.main()


@unittest.skipUnless(HAVE_GIT, "git 2.30+ not available")
class QuickCompareTests(unittest.TestCase):
    def test_quick_presets(self):
        from lavati.git_view import GitCompareDialog
        d = tempfile.mkdtemp(prefix="lavati-quick-")
        try:
            git(d, "init", "-q", "-b", "main")
            with open(os.path.join(d, "f.txt"), "w") as fh:
                fh.write("one\n")
            git(d, "add", "-A")
            git(d, "commit", "-q", "-m", "c1")
            top = gitvfs.find_repo(d)
            for (l, r), want in (((2, 0), ("HEAD", "WORKTREE")), ((2, 1), ("HEAD", "INDEX")), ((1, 0), ("INDEX", "WORKTREE"))):
                dlg = GitCompareDialog(None, d)
                dlg._preset(l, r)
                self.assertEqual(dlg.result_specs, (gitvfs.make_spec(top, want[0]), gitvfs.make_spec(top, want[1])))
        finally:
            shutil.rmtree(d, ignore_errors=True)


@unittest.skipUnless(HAVE_GIT, "git 2.30+ not available")
class RemoteToLocalTests(unittest.TestCase):
    """Compare a remote branch with the checked-out working tree and copy differences into the local files."""

    def setUp(self):
        from lavati.folder_view import FolderCompareView
        self.FolderCompareView = FolderCompareView
        self.d = tempfile.mkdtemp(prefix="lavati-remote-")
        self.r = r = os.path.join(self.d, "repo")
        os.makedirs(r)
        git(r, "init", "-q", "-b", "main")
        for name, text in (("a.txt", "one\ntwo\n"), ("keep.txt", "same\n")):
            with open(os.path.join(r, name), "w", newline="") as f:
                f.write(text)
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", "base")
        git(r, "checkout", "-q", "-b", "feat")
        with open(os.path.join(r, "a.txt"), "w", newline="") as f:
            f.write("one\nTWO from remote\nthree\n")
        with open(os.path.join(r, "new.txt"), "w", newline="") as f:
            f.write("only on remote\n")
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", "remote work")
        git(r, "update-ref", "refs/remotes/origin/feat", "feat")          # pretend this branch lives on the remote
        git(r, "checkout", "-q", "main")                                 # the local checkout
        self.cfg = config.Config(os.path.join(self.d, "cfg.json"))

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_copy_remote_differences_into_local_checkout(self):
        import lavati.folder_view as fv
        left, right = gitvfs.make_spec(self.r, "origin/feat"), gitvfs.make_spec(self.r, "WORKTREE")
        v = self.FolderCompareView(self.cfg, left, right)
        v.show()
        self.assertTrue(wait(lambda: v.generation > 0, 15))
        v.tree.selectAll()
        orig = fv.QMessageBox.question
        fv.QMessageBox.question = staticmethod(lambda *a, **k: fv.QMessageBox.Yes)
        try:
            v.copy_selected("R")                                         # remote (left) -> local checkout (right)
            g = v.generation
            self.assertTrue(wait(lambda: v.generation > g, 15))
        finally:
            fv.QMessageBox.question = orig
        with open(os.path.join(self.r, "a.txt"), newline="") as f:
            self.assertEqual(f.read(), "one\nTWO from remote\nthree\n")
        self.assertTrue(os.path.exists(os.path.join(self.r, "new.txt")))
        v.close()

    def test_text_compare_copies_line_into_checked_out_file(self):
        """Opening a file pair from a git comparison: the remote side is read-only, the checked-out file is editable and saves."""
        remote_file = os.path.join(self.d, "remote_a.txt")
        with open(remote_file, "wb") as f:
            f.write(gitvfs.file_at(self.r, "origin/feat", "a.txt"))
        local = os.path.join(self.r, "a.txt")
        v = TextCompareView(self.cfg, remote_file, local)
        v.show()
        v.sides["L"].readonly = True
        v.panes["L"].readonly = True
        v.take("right", 0)
        v._flush()
        v.save()
        with open(local, newline="") as f:
            self.assertIn("TWO from remote", f.read())
        v.close()
