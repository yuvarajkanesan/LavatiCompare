import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lavati import engine, fsops, vfs  # noqa: E402

T = 1_700_000_000


def w(path, data, mtime=T):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(data)
    os.utime(path, (mtime, mtime))


def rd(path):
    with open(path) as f:
        return f.read()


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.L, self.R = os.path.join(self.d, "L"), os.path.join(self.d, "R")
        w(f"{self.L}/same.txt", "s")
        w(f"{self.R}/same.txt", "s")
        w(f"{self.L}/newer.txt", "L-new!", T + 100)
        w(f"{self.R}/newer.txt", "old", T)
        w(f"{self.L}/older.txt", "old", T)
        w(f"{self.R}/older.txt", "R-new!", T + 100)
        w(f"{self.L}/lonly.txt", "l")
        w(f"{self.R}/ronly.txt", "r")
        w(f"{self.L}/ldir/a.txt", "a")
        w(f"{self.R}/rdir/b.txt", "b")
        os.makedirs(f"{self.L}/emptyL")
        os.makedirs(f"{self.R}/emptyR")
        w(f"{self.L}/conflict.txt", "xx", T)
        w(f"{self.R}/conflict.txt", "yy", T)
        self.S = fsops.Sides(vfs.LocalFS(), self.L, vfs.LocalFS(), self.R)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def nodes(self, **kw):
        opts = engine.CompareOptions(method="binary", **kw)
        return engine.compare_tree(self.S.lfs, self.L, self.S.rfs, self.R, opts)

    def plan(self, mode):
        return fsops.build_plan(self.nodes(), mode)

    def actions(self, ops):
        return {(o["rel"], o["action"], o["dir"]) for o in ops}

    def test_update_right(self):
        a = self.actions(self.plan("update_right"))
        self.assertIn(("lonly.txt", "copy", "R"), a)
        self.assertIn(("newer.txt", "overwrite", "R"), a)
        self.assertIn(("older.txt", "skip", "R"), a)
        self.assertIn(("conflict.txt", "skip", "R"), a)
        self.assertIn(("ldir/a.txt", "copy", "R"), a)
        self.assertIn(("emptyL", "mkdir", "R"), a)
        self.assertFalse(any(x[1] == "delete" for x in a))
        self.assertNotIn(("ronly.txt", "copy", "L"), a)

    def test_update_left(self):
        a = self.actions(self.plan("update_left"))
        self.assertIn(("ronly.txt", "copy", "L"), a)
        self.assertIn(("older.txt", "overwrite", "L"), a)
        self.assertIn(("newer.txt", "skip", "L"), a)
        self.assertIn(("emptyR", "mkdir", "L"), a)

    def test_update_both(self):
        a = self.actions(self.plan("update_both"))
        self.assertIn(("lonly.txt", "copy", "R"), a)
        self.assertIn(("ronly.txt", "copy", "L"), a)
        self.assertIn(("newer.txt", "overwrite", "R"), a)
        self.assertIn(("older.txt", "overwrite", "L"), a)

    def test_mirror_right_executes(self):
        ops = self.plan("mirror_right")
        a = self.actions(ops)
        self.assertIn(("ronly.txt", "delete", "R"), a)
        self.assertIn(("rdir", "delete", "R"), a)
        self.assertIn(("conflict.txt", "overwrite", "R"), a)
        self.assertNotIn(("rdir/b.txt", "delete", "R"), a)         # whole folder removed in one step
        done, errors = fsops.run_plan(ops, self.S, backup=True, trash=False, stamp="T1")
        self.assertEqual(errors, [])
        # right now equals left
        nodes = self.nodes()
        self.assertTrue(all(n.status == "same" for n in engine.walk(nodes) if not n.is_dir), [(n.rel, n.status) for n in engine.walk(nodes)])
        self.assertFalse(os.path.exists(f"{self.R}/rdir"))
        self.assertFalse(os.path.exists(f"{self.R}/emptyR"))
        self.assertTrue(os.path.isdir(f"{self.R}/emptyL"))
        # overwritten / deleted files were backed up
        self.assertEqual(rd(f"{self.R}/.lavati-backup/T1/newer.txt"), "old")
        self.assertEqual(rd(f"{self.R}/.lavati-backup/T1/ronly.txt"), "r")
        self.assertEqual(rd(f"{self.R}/.lavati-backup/T1/rdir/b.txt"), "b")
        # the backup folder never shows up as a difference
        self.assertTrue(all(".lavati-backup" not in n.rel for n in engine.walk(nodes)))

    def test_mirror_left_executes(self):
        ops = self.plan("mirror_left")
        done, errors = fsops.run_plan(ops, self.S, backup=False, trash=False)
        self.assertEqual(errors, [])
        self.assertFalse(os.path.exists(f"{self.L}/lonly.txt"))
        self.assertEqual(rd(f"{self.L}/newer.txt"), "old")
        self.assertEqual(rd(f"{self.L}/rdir/b.txt"), "b")

    def test_custom_backup_dir_and_selection(self):
        bdir = os.path.join(self.d, "bk")
        nodes = self.nodes()
        ops = fsops.build_plan(nodes, "update_right", only={"newer.txt"})
        self.assertEqual([o["rel"] for o in ops], ["newer.txt"])
        fsops.run_plan(ops, self.S, backup=True, backup_dir=bdir, trash=False, stamp="S")
        self.assertEqual(rd(f"{bdir}/S/newer.txt"), "old")
        self.assertEqual(rd(f"{self.R}/newer.txt"), "L-new!")
        self.assertEqual(os.path.getmtime(f"{self.R}/newer.txt"), T + 100)       # timestamp preserved

    def test_script_export(self):
        ops = self.plan("update_right")
        sh = fsops.plan_to_script(ops, self.S, "update_right", style="sh")
        self.assertIn("cp -p", sh)
        bat = fsops.plan_to_script(ops, self.S, "update_right", style="bat")
        self.assertIn("copy /Y", bat)

    def test_ops(self):
        nodes = {n.rel: n for n in engine.walk(self.nodes())}
        done, err = fsops.copy_nodes([nodes["lonly.txt"], nodes["ldir"]], "R", self.S)
        self.assertEqual(err, [])
        self.assertEqual(rd(f"{self.R}/lonly.txt"), "l")
        self.assertEqual(rd(f"{self.R}/ldir/a.txt"), "a")
        # move
        done, err = fsops.copy_nodes([nodes["ronly.txt"]], "L", self.S, move=True)
        self.assertEqual(err, [])
        self.assertTrue(os.path.exists(f"{self.L}/ronly.txt"))
        self.assertFalse(os.path.exists(f"{self.R}/ronly.txt"))
        # rename
        self.assertEqual(fsops.rename_node(nodes["same.txt"], "L", "renamed.txt", self.S), "")
        self.assertTrue(os.path.exists(f"{self.L}/renamed.txt"))
        self.assertIn("exists", fsops.rename_node(nodes["newer.txt"], "L", "older.txt", self.S))
        self.assertTrue(fsops.rename_node(nodes["newer.txt"], "L", "a/b", self.S))
        # touch
        done, err = fsops.touch_nodes([nodes["conflict.txt"]], "R", self.S, "value", 1_600_000_000)
        self.assertEqual(os.path.getmtime(f"{self.R}/conflict.txt"), 1_600_000_000)
        fsops.touch_nodes([nodes["conflict.txt"]], "R", self.S, "other")
        self.assertEqual(os.path.getmtime(f"{self.R}/conflict.txt"), T)
        # delete (permanent)
        done, err = fsops.delete_nodes([nodes["conflict.txt"]], "R", self.S, trash=False)
        self.assertFalse(os.path.exists(f"{self.R}/conflict.txt"))
        # new folder
        self.assertEqual(fsops.make_folder("L", self.L, "newdir", self.S), "")
        self.assertTrue(os.path.isdir(f"{self.L}/newdir"))

    def test_readonly_sides_refused(self):
        zp = os.path.join(self.d, "a.zip")
        import zipfile
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("x.txt", "x")
        afs, _ = vfs.open_location(zp)
        S = fsops.Sides(self.S.lfs, self.L, afs, "")
        nodes = engine.compare_tree(S.lfs, self.L, S.rfs, "", engine.CompareOptions())
        n = [x for x in nodes if x.rel == "lonly.txt"][0]
        done, err = fsops.copy_nodes([n], "R", S)
        self.assertEqual(done, 0)
        self.assertIn("read-only", err[0])

    def test_aligned_copy_uses_existing_name(self):
        w(f"{self.L}/a_v1.txt", "one", T + 50)
        w(f"{self.R}/a_v2.txt", "two", T)
        nodes = {n.rel: n for n in engine.walk(self.nodes(aligns=[["a_v1.txt", "a_v2.txt"]]))}
        n = nodes["a_v1.txt"]
        self.assertEqual(n.status, "newer")
        fsops.copy_nodes([n], "R", self.S)
        self.assertEqual(rd(f"{self.R}/a_v2.txt"), "one")
        self.assertFalse(os.path.exists(f"{self.R}/a_v1.txt"))


if __name__ == "__main__":
    unittest.main()
