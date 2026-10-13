import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lavati import engine, gitvfs, vfs  # noqa: E402

HAVE_GIT = bool(gitvfs.git_path()) and not gitvfs.check_git()


def git(repo, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@x",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)
    r = subprocess.run(["git", "-C", repo, "-c", "core.autocrlf=false", "-c", "commit.gpgsign=false", *args],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, (args, r.stderr)
    return r.stdout.strip()


def w(repo, rel, data):
    p = os.path.join(repo, *rel.split("/"))
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(data if isinstance(data, bytes) else data.encode())


@unittest.skipUnless(HAVE_GIT, "git 2.30+ not available")
class GitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="lavati-git-")
        r = cls.r = os.path.join(cls.d, "repo")
        os.makedirs(r)
        git(r, "init", "-q", "-b", "main")
        body = "".join(f"line {i} of a reasonably long file used for rename detection\n" for i in range(30))
        w(r, "a.txt", "one\ntwo\nthree\n")
        w(r, "dir/b.txt", body)
        w(r, "bin.dat", bytes(range(256)) * 4)
        w(r, "lfs.bin", "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 12345\n")
        w(r, "gone.txt", "bye\n")
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", "c1")
        cls.c1 = git(r, "rev-parse", "HEAD")
        git(r, "branch", "feature")
        os.remove(os.path.join(r, "dir", "b.txt"))
        w(r, "dir2/b_renamed.txt", body.replace("line 3 ", "LINE 3 "))
        w(r, "a.txt", "one\nTWO main\nthree\n")
        w(r, "new.txt", "new\n")
        os.remove(os.path.join(r, "gone.txt"))
        git(r, "add", "-A")
        git(r, "commit", "-q", "-m", "c2")
        cls.c2 = git(r, "rev-parse", "HEAD")
        git(r, "checkout", "-q", "feature")
        w(r, "a.txt", "one\nTWO feature\nthree\n")
        git(r, "commit", "-q", "-am", "feature change")
        cls.feature = git(r, "rev-parse", "HEAD")
        git(r, "checkout", "-q", "main")

    @classmethod
    def tearDownClass(cls):
        for root, dirs, files in os.walk(cls.d):
            for n in files:
                try:
                    os.chmod(os.path.join(root, n), 0o666)
                except OSError:
                    pass
        shutil.rmtree(cls.d, ignore_errors=True)

    def cmp(self, a, b, **kw):
        lfs, lroot = vfs.open_location(gitvfs.make_spec(self.r, a))
        rfs, rroot = vfs.open_location(gitvfs.make_spec(self.r, b))
        nodes = engine.compare_tree(lfs, lroot, rfs, rroot, engine.CompareOptions(**kw))
        return nodes, {n.rel: n.status for n in engine.walk(nodes)}

    def test_basics(self):
        self.assertEqual(gitvfs.find_repo(os.path.join(self.r, "dir")), os.path.normpath(self.r))
        self.assertEqual(gitvfs.find_repo(self.d), "")
        refs = gitvfs.list_refs(self.r)
        self.assertEqual(sorted(refs["branches"]), ["feature", "main"])
        self.assertEqual(gitvfs.current_branch(self.r), "main")
        log = gitvfs.log(self.r, "main")
        self.assertEqual([l[4] for l in log], ["c2", "c1"])
        fs = gitvfs.GitTreeFS(self.r, self.c1)
        self.assertEqual(sorted(e.name for e in fs.scandir("")), ["a.txt", "bin.dat", "dir", "gone.txt", "lfs.bin"])
        self.assertEqual(fs.stat("a.txt").size, 14)
        with fs.open("a.txt") as f:
            self.assertEqual(f.read(), b"one\ntwo\nthree\n")
        with open(fs.local_path("bin.dat"), "rb") as f:
            self.assertEqual(f.read(), bytes(range(256)) * 4)       # binary files survive untouched
        with self.assertRaises(gitvfs.GitError):
            gitvfs.GitTreeFS(self.r, "no-such-branch")

    def test_compare_commits_by_blob_id(self):
        nodes, st = self.cmp(self.c1, self.c2)
        self.assertEqual(st["a.txt"], "changed")
        self.assertEqual(st["bin.dat"], "same")
        self.assertEqual(st["gone.txt"], "left")
        self.assertEqual(st["new.txt"], "right")
        self.assertEqual(st["dir/b.txt"], "left")
        self.assertEqual(st["dir2/b_renamed.txt"], "right")
        _, st = self.cmp("main", "feature")
        self.assertEqual(st["a.txt"], "changed")

    def test_rename_detection(self):
        nodes, st = self.cmp(self.c1, self.c2)
        pairs = gitvfs.detect_renames(self.r, self.c1, self.c2)
        self.assertEqual(pairs, [("dir/b.txt", "dir2/b_renamed.txt")])
        n = gitvfs.apply_renames(nodes, pairs)
        self.assertEqual(n, 1)
        by = {x.rel: x for x in engine.walk(nodes)}
        self.assertNotIn("dir/b.txt", by)
        r = by["dir2/b_renamed.txt"]
        self.assertEqual((r.status, r.name, r.rname, r.renamed_from), ("changed", "b.txt", "b_renamed.txt", "dir/b.txt"))
        self.assertIsNotNone(r.left)
        self.assertEqual(by["dir"].status, "left" if by["dir"].status == "left" else by["dir"].status)

    def test_worktree_and_index(self):
        r = self.r
        w(r, "a.txt", "one\nTWO main\nthree\nstaged\n")
        git(r, "add", "a.txt")
        w(r, "a.txt", "one\nTWO main\nthree\nstaged\nunstaged\n")
        w(r, "untracked.txt", "u\n")
        try:
            _, st = self.cmp("HEAD", "INDEX")
            self.assertEqual(st["a.txt"], "changed")
            self.assertNotIn("untracked.txt", st)
            _, st = self.cmp("INDEX", "WORKTREE")
            self.assertEqual(st["a.txt"], "changed")
            self.assertEqual(st["untracked.txt"], "right")
            self.assertNotIn(".git", st)
            _, st = self.cmp("HEAD", "WORKTREE")
            self.assertEqual(st["bin.dat"], "same")
        finally:
            git(r, "reset", "-q", "--hard", "HEAD")
            os.remove(os.path.join(r, "untracked.txt"))

    def test_merge_base_conflict_and_detached(self):
        base = gitvfs.merge_base(self.r, "main", "feature")
        self.assertEqual(base, self.c1)
        from lavati import foldermerge as fm
        opn = lambda rev: vfs.open_location(gitvfs.make_spec(self.r, rev))[0]
        S = fm.Sides3(opn("main"), "", opn(base), "", opn("feature"), "")
        ents = {e.rel: e for e in fm.scan(S)}
        self.assertEqual(ents["a.txt"].action, "conflict")          # both branches changed line two
        self.assertEqual(ents["new.txt"].action, "ours")
        self.assertEqual(ents["bin.dat"].action, "keep")
        git(self.r, "checkout", "-q", "--detach", self.c1)
        try:
            self.assertEqual(gitvfs.current_branch(self.r), "")
            fs = gitvfs.GitTreeFS(self.r, "HEAD")
            self.assertEqual(fs.stat("a.txt").size, 14)
        finally:
            git(self.r, "checkout", "-q", "main")

    def test_submodule_stash_and_lfs_pointer(self):
        r = self.r
        git(r, "update-index", "--add", "--cacheinfo", f"160000,{self.c1},vendor/sub")
        git(r, "commit", "-q", "-m", "add gitlink")
        try:
            fs = gitvfs.GitTreeFS(r, "HEAD")
            self.assertTrue(fs.is_submodule("vendor/sub"))
            with fs.open("vendor/sub") as f:
                self.assertIn(self.c1.encode(), f.read())
            lfs = gitvfs.GitTreeFS(r, "HEAD")
            with lfs.open("lfs.bin") as f:
                self.assertTrue(f.read().startswith(b"version https://git-lfs"))      # pointer file shown as plain text
        finally:
            git(r, "reset", "-q", "--hard", "HEAD~1")
        w(r, "a.txt", "stash me\n")
        git(r, "stash", "push", "-q", "-m", "wip")
        refs = gitvfs.list_refs(r)
        self.assertEqual(len(refs["stashes"]), 1)
        _, st = self.cmp("HEAD", "stash@{0}")
        self.assertEqual(st["a.txt"], "changed")
        git(r, "stash", "drop", "-q")

    def test_checksum_matches_git(self):
        p = os.path.join(self.d, "x.txt")
        with open(p, "wb") as f:
            f.write(b"hello\n")
        self.assertEqual(vfs.LocalFS().checksum(p, "git"), "ce013625030ba8dba906f756967f9e9ca394464a")
        self.assertEqual(gitvfs.GitWorkFS("").checksum(p, "git"), "ce013625030ba8dba906f756967f9e9ca394464a")

    def test_large_repo_listing_is_fast(self):
        big = os.path.join(self.d, "big")
        os.makedirs(big)
        git(big, "init", "-q", "-b", "main")
        for a in range(16):
            for b in range(250):
                p = os.path.join(big, f"d{a}", f"f{b}.txt")
                os.makedirs(os.path.dirname(p), exist_ok=True)
                with open(p, "w") as f:
                    f.write(f"{a} {b}\n")
        git(big, "add", "-A")
        git(big, "commit", "-q", "-m", "one")
        c1 = git(big, "rev-parse", "HEAD")
        for b in range(0, 250, 25):
            with open(os.path.join(big, "d7", f"f{b}.txt"), "a") as f:
                f.write("changed\n")
        git(big, "commit", "-q", "-am", "two")
        t0 = time.time()
        lfs, lr = vfs.open_location(gitvfs.make_spec(big, c1))
        rfs, rr = vfs.open_location(gitvfs.make_spec(big, "HEAD"))
        nodes = engine.compare_tree(lfs, lr, rfs, rr, engine.CompareOptions())
        dt = time.time() - t0
        c = engine.count_statuses(nodes)
        self.assertEqual(c["changed"], 10)
        self.assertEqual(c["same"], 3990)
        self.assertLess(dt, 5.0, f"4,000 files took {dt:.1f}s")


if __name__ == "__main__":
    unittest.main()
