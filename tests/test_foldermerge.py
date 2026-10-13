import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lavati import engine, foldermerge as fm, vfs  # noqa: E402


def w(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="") as f:
        f.write(data)


def rd(path):
    with open(path, newline="") as f:
        return f.read()


class FolderMergeTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        j = lambda n: os.path.join(self.d, n)
        self.O, self.B, self.T, self.OUT = j("ours"), j("base"), j("theirs"), j("out")
        # unchanged
        for r in (self.O, self.B, self.T):
            w(f"{r}/same.txt", "same\n")
        # changed only in theirs / ours
        w(f"{self.B}/t_only.txt", "a\n"); w(f"{self.O}/t_only.txt", "a\n"); w(f"{self.T}/t_only.txt", "A\n")
        w(f"{self.B}/o_only.txt", "a\n"); w(f"{self.O}/o_only.txt", "A\n"); w(f"{self.T}/o_only.txt", "a\n")
        # text changed on both sides in different places -> auto merge
        w(f"{self.B}/auto.txt", "1\n2\n3\n4\n5\n"); w(f"{self.O}/auto.txt", "ONE\n2\n3\n4\n5\n"); w(f"{self.T}/auto.txt", "1\n2\n3\n4\nFIVE\n")
        # same line changed differently -> conflict
        w(f"{self.B}/clash.txt", "x\n"); w(f"{self.O}/clash.txt", "o\n"); w(f"{self.T}/clash.txt", "t\n")
        # added on one side only
        w(f"{self.O}/new/o.txt", "o\n"); w(f"{self.T}/new/t.txt", "t\n")
        # deleted in theirs (unchanged in ours) / deleted in ours but changed in theirs
        w(f"{self.B}/gone.txt", "g\n"); w(f"{self.O}/gone.txt", "g\n")
        w(f"{self.B}/gone2.txt", "g\n"); w(f"{self.T}/gone2.txt", "G\n")
        # binary conflict
        for r, b in ((self.B, b"\x00\x01"), (self.O, b"\x00\x02"), (self.T, b"\x00\x03")):
            with open(f"{r}/bin.dat", "wb") as f:
                f.write(b)
        self.S = fm.Sides3(vfs.LocalFS(), self.O, vfs.LocalFS(), self.B, vfs.LocalFS(), self.T)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_three_way(self):
        ents = {e.rel: e for e in fm.scan(self.S)}
        self.assertEqual(ents["same.txt"].action, "keep")
        self.assertEqual(ents["t_only.txt"].action, "theirs")
        self.assertEqual(ents["o_only.txt"].action, "ours")
        self.assertEqual(ents["auto.txt"].action, "merged")
        self.assertEqual(rd(ents["auto.txt"].merged_path), "ONE\n2\n3\n4\nFIVE\n")
        self.assertEqual(ents["clash.txt"].action, "conflict")
        self.assertTrue(ents["clash.txt"].text_ok)
        self.assertEqual(ents["bin.dat"].action, "conflict")
        self.assertFalse(ents["bin.dat"].text_ok)
        self.assertEqual(ents["new/o.txt"].action, "ours")
        self.assertEqual(ents["new/t.txt"].action, "theirs")
        self.assertEqual(ents["gone.txt"].action, "delete")
        self.assertEqual(ents["gone2.txt"].action, "conflict")
        self.assertEqual(fm.summary(list(ents.values()))["conflict"], 3)

    def test_write_requires_resolution_then_output(self):
        ents = fm.scan(self.S)
        done, errs = fm.write_output(ents, self.S, vfs.LocalFS(), self.OUT)
        self.assertEqual(done, 0)
        self.assertIn("conflict", errs[0])
        by = {e.rel: e for e in ents}
        by["clash.txt"].resolution = "theirs"
        by["bin.dat"].resolution = "ours"
        by["gone2.txt"].resolution = "delete"
        done, errs = fm.write_output(ents, self.S, vfs.LocalFS(), self.OUT)
        self.assertEqual(errs, [])
        self.assertEqual(rd(f"{self.OUT}/clash.txt"), "t\n")
        self.assertEqual(rd(f"{self.OUT}/auto.txt"), "ONE\n2\n3\n4\nFIVE\n")
        self.assertEqual(rd(f"{self.OUT}/t_only.txt"), "A\n")
        self.assertEqual(rd(f"{self.OUT}/o_only.txt"), "A\n")
        self.assertEqual(rd(f"{self.OUT}/new/o.txt"), "o\n")
        self.assertEqual(rd(f"{self.OUT}/new/t.txt"), "t\n")
        self.assertFalse(os.path.exists(f"{self.OUT}/gone.txt"))
        self.assertFalse(os.path.exists(f"{self.OUT}/gone2.txt"))
        with open(f"{self.OUT}/bin.dat", "rb") as f:
            self.assertEqual(f.read(), b"\x00\x02")

    def test_in_place_output_is_ours(self):
        ents = fm.scan(self.S)
        for e in ents:
            if e.unresolved():
                e.resolution = "ours"
        done, errs = fm.write_output(ents, self.S, vfs.LocalFS(), self.O)
        self.assertEqual(errs, [])
        self.assertEqual(rd(f"{self.O}/t_only.txt"), "A\n")          # taken from theirs
        self.assertEqual(rd(f"{self.O}/clash.txt"), "o\n")           # ours stays
        self.assertFalse(os.path.exists(f"{self.O}/gone.txt"))

    def test_two_way(self):
        S = fm.Sides3(vfs.LocalFS(), self.O, None, None, vfs.LocalFS(), self.T)
        ents = {e.rel: e for e in fm.scan(S)}
        self.assertEqual(ents["same.txt"].action, "keep")
        self.assertEqual(ents["t_only.txt"].action, "conflict")
        self.assertEqual(ents["new/o.txt"].action, "ours")
        self.assertEqual(ents["new/t.txt"].action, "theirs")
        self.assertIn(fm.pick_newer(ents["t_only.txt"]), ("ours", "theirs"))
        self.assertEqual(fm.pick_newer(ents["new/t.txt"]), "theirs")


if __name__ == "__main__":
    unittest.main()
