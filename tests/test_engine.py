import gzip
import os
import shutil
import sys
import tarfile
import tempfile
import unittest
import zipfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lavati import engine, vfs  # noqa: E402


def w(path, data, mtime=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    with open(path, mode, **({} if isinstance(data, bytes) else {"newline": ""})) as f:
        f.write(data)
    if mtime:
        os.utime(path, (mtime, mtime))


class FolderCompare(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.L, self.R = os.path.join(self.d, "L"), os.path.join(self.d, "R")
        t = 1_700_000_000
        w(f"{self.L}/same.txt", "x", t)
        w(f"{self.R}/same.txt", "x", t)
        w(f"{self.L}/newer.txt", "aa", t + 100)
        w(f"{self.R}/newer.txt", "a", t)
        w(f"{self.L}/older.txt", "a", t)
        w(f"{self.R}/older.txt", "aa", t + 100)
        w(f"{self.L}/lonly.txt", "l", t)
        w(f"{self.R}/ronly.txt", "r", t)
        w(f"{self.L}/sub/in.txt", "1", t)
        w(f"{self.R}/sub/in.txt", "1", t)

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def st(self, **kw):
        opts = engine.CompareOptions(**kw)
        nodes = engine.compare_tree(vfs.LocalFS(self.L), self.L, vfs.LocalFS(self.R), self.R, opts)
        return {n.rel: n.status for n in engine.walk(nodes)}

    def test_statuses(self):
        s = self.st()
        self.assertEqual(s["same.txt"], "same")
        self.assertEqual(s["newer.txt"], "newer")
        self.assertEqual(s["older.txt"], "older")
        self.assertEqual(s["lonly.txt"], "left")
        self.assertEqual(s["ronly.txt"], "right")
        self.assertEqual(s["sub"], "same")

    def test_methods(self):
        t = 1_700_000_000
        w(f"{self.L}/same.txt", "y", t + 500)       # same size, different time and content
        self.assertEqual(self.st(method="size_time")["same.txt"], "newer")
        self.assertEqual(self.st(method="crc")["same.txt"], "newer")
        self.assertEqual(self.st(method="binary")["same.txt"], "newer")
        w(f"{self.L}/same.txt", "x", t + 500)       # same content, different time
        self.assertEqual(self.st(method="size_time")["same.txt"], "newer")
        self.assertEqual(self.st(method="crc")["same.txt"], "same")
        self.assertEqual(self.st(method="binary")["same.txt"], "same")

    def test_rules_method(self):
        w(f"{self.L}/r.txt", "a  b\r\nc\r\n", 1_700_000_000)
        w(f"{self.R}/r.txt", "a b\nc\n", 1_700_000_000)
        self.assertEqual(self.st(method="binary")["r.txt"], "changed")
        rules = engine.TextRules(ws="changes", ignore_eol=True)
        self.assertEqual(self.st(method="rules", rules=rules)["r.txt"], "same")
        rules = engine.TextRules(ws="changes", ignore_eol=False)
        self.assertNotEqual(self.st(method="rules", rules=rules)["r.txt"], "same")

    def test_include_exclude(self):
        w(f"{self.L}/a.kt", "1")
        w(f"{self.R}/b.java", "2")
        s = self.st(include=["*.kt"])
        self.assertIn("a.kt", s)
        self.assertNotIn("b.java", s)
        self.assertNotIn("sub", s)
        s = self.st(patterns=["sub/", "*.kt"])
        self.assertNotIn("sub", s)
        self.assertNotIn("a.kt", s)

    def test_aligns_and_case(self):
        w(f"{self.L}/a_v1.txt", "q", 1_700_000_000)
        w(f"{self.R}/a_v2.txt", "q", 1_700_000_000)
        s = self.st()
        self.assertEqual(s["a_v1.txt"], "left")
        s = self.st(aligns=[["a_v1.txt", "a_v2.txt"]])
        self.assertEqual(s["a_v1.txt"], "same")
        self.assertNotIn("a_v2.txt", s)
        w(f"{self.L}/CaSe.txt", "q", 1_700_000_000)
        w(f"{self.R}/case.txt", "q", 1_700_000_000)
        s = self.st(ignore_case_names=True)
        self.assertEqual(s.get("CaSe.txt"), "same")

    def test_archives(self):
        for side, content in ((self.L, b"one"), (self.R, b"two")):
            with zipfile.ZipFile(f"{side}/pack.zip", "w") as z:
                z.writestr("inner/f.txt", content)
                z.writestr("same.txt", "s")
        s = self.st(archives=True, method="crc")
        self.assertEqual(s["pack.zip"], "changed")
        self.assertIn(s["pack.zip/inner/f.txt"], ("changed", "newer", "older"))
        self.assertEqual(s["pack.zip/same.txt"], "same")
        self.assertEqual(self.st(archives=False, method="crc")["pack.zip"], "changed")

    def test_archive_as_root(self):
        zp = os.path.join(self.d, "a.zip")
        with zipfile.ZipFile(zp, "w") as z:
            z.writestr("d/x.txt", "hello")
        fs, root = vfs.open_location(zp)
        self.assertIsInstance(fs, vfs.ArchiveFS)
        self.assertEqual([e.name for e in fs.scandir(root)], ["d"])
        with fs.open("d/x.txt") as f:
            self.assertEqual(f.read(), b"hello")
        self.assertTrue(os.path.exists(fs.local_path("d/x.txt")))
        tp = os.path.join(self.d, "a.tar.gz")
        with tarfile.open(tp, "w:gz") as t:
            t.add(self.L, arcname="top")
        fs2, _ = vfs.open_location(tp)
        self.assertTrue(any(e.name == "top" for e in fs2.scandir("")))
        gp = os.path.join(self.d, "data.txt.gz")
        with gzip.open(gp, "wb") as g:
            g.write(b"zzz")
        fs3, _ = vfs.open_location(gp)
        self.assertEqual([e.name for e in fs3.scandir("")], ["data.txt"])
        self.assertEqual(fs3.stat("data.txt").size, 3)

    def test_snapshot(self):
        out = os.path.join(self.d, "s.lsnap")
        n = vfs.capture_snapshot(vfs.LocalFS(self.L), self.L, out)
        self.assertGreater(n, 3)
        snap = vfs.SnapshotFS(out)
        w(f"{self.L}/newer.txt", "changed-content!", 1_700_000_100)
        opts = engine.CompareOptions(method="crc")
        nodes = engine.compare_tree(snap, "", vfs.LocalFS(self.L), self.L, opts)
        s = {n.rel: n.status for n in engine.walk(nodes)}
        self.assertEqual(s["same.txt"], "same")
        self.assertNotEqual(s["newer.txt"], "same")

    def test_transfer(self):
        fs = vfs.LocalFS()
        vfs.transfer_tree(fs, self.L, fs, os.path.join(self.d, "copy"))
        self.assertTrue(os.path.exists(os.path.join(self.d, "copy", "sub", "in.txt")))

    def test_flatten(self):
        nodes = engine.compare_tree(vfs.LocalFS(self.L), self.L, vfs.LocalFS(self.R), self.R, engine.CompareOptions())
        flat = engine.flatten(nodes)
        self.assertIn("sub/in.txt", [n.rel for n in flat])
        self.assertTrue(all(not n.is_dir for n in flat))


class TextTests(unittest.TestCase):
    def test_encodings(self):
        for label in ("UTF-8", "UTF-8 BOM", "UTF-16 LE", "UTF-16 BE", "UTF-16 LE (no BOM)", "UTF-32 LE", "ANSI"):
            text = "héllo wörld\n"
            raw = engine.encode_text(text, label)
            t, lab, eol = engine.decode_bytes(raw)
            self.assertEqual(t, text, label)
            self.assertEqual(lab, label)

    def test_eol(self):
        self.assertEqual(engine.detect_eol("a\r\nb\r\n"), ("CRLF", False))
        self.assertEqual(engine.detect_eol("a\nb\n"), ("LF", False))
        self.assertEqual(engine.detect_eol("a\rb\r"), ("CR", False))
        self.assertTrue(engine.detect_eol("a\r\nb\nc\n")[1])
        self.assertEqual(engine.split_lines("a\r\nb\rc\nd\n"), ["a", "b", "c", "d"])
        self.assertEqual(engine.split_lines("a\x0cb\n"), ["a\x0cb"])

    def test_rules(self):
        a = ["Foo  bar", "x // c1", "T 2024-01-01 10:00:00"]
        b = ["foo bar", "x // other", "T 2025-02-02 11:11:11"]
        self.assertNotEqual(engine.diff_opcodes(a, b), [("equal", 0, 3, 0, 3)])
        r = engine.TextRules(ws="changes", ignore_case=True,
                             unimportant=[engine.UNIMPORTANT_PRESETS["C-style line comments (// ...)"],
                                          engine.UNIMPORTANT_PRESETS["ISO timestamps (2024-01-31 12:00:00)"]])
        ops = engine.diff_opcodes(a, b, rules=r)
        self.assertEqual(ops, [("equal", 0, 3, 0, 3)])
        pairs, hunks = engine.text_pairs(a, b, ops, r)
        self.assertEqual([p[2] for p in pairs], ["unimp"] * 3)
        r2 = engine.TextRules(replacements=[(r"\d+", "N")])
        self.assertEqual(engine.diff_opcodes(["a1"], ["a22"], rules=r2), [("equal", 0, 1, 0, 1)])

    def test_diff_and_anchors(self):
        a = ["1", "2", "3", "4"]
        b = ["1", "x", "3", "4", "5"]
        ops = engine.diff_opcodes(a, b)
        self.assertEqual(ops[0], ("equal", 0, 1, 0, 1))
        self.assertIn(("replace", 1, 2, 1, 2), ops)
        self.assertIn(("insert", 4, 4, 4, 5), ops)
        ops = engine.diff_opcodes(a, b, anchors=[(1, 4)])
        self.assertTrue(any(o[0] == "replace" and o[1] == 1 and o[3] == 4 for o in ops))
        # opcodes must cover both sequences completely and contiguously
        i = j = 0
        for tag, i1, i2, j1, j2 in ops:
            self.assertEqual((i1, j1), (i, j))
            i, j = i2, j2
        self.assertEqual((i, j), (len(a), len(b)))

    def test_spans(self):
        sa, sb = engine.word_spans("foo bar baz", "foo qux baz")
        self.assertEqual(sa, [(4, 7)])
        self.assertEqual(sb, [(4, 7)])
        sa, sb = engine.word_spans("hello", "hallo", chars=True)
        self.assertEqual(sa, [(1, 2)])
        self.assertEqual(sb, [(1, 2)])
        r = engine.TextRules(ws="all")
        self.assertEqual(engine.word_spans("a b c", "abc", rules=r), ([], []))

    def test_unified(self):
        a = list("abcdefghij")
        b = list(a)
        b[4] = "E"
        out = engine.unified_diff(a, b, "L", "R", context=2)
        self.assertEqual(out[0], "--- L")
        self.assertIn("@@ -3,5 +3,5 @@", out)
        self.assertIn("-e", out)
        self.assertIn("+E", out)
        self.assertEqual(engine.unified_diff(a, a), [])

    def test_merge(self):
        base = ["a", "b", "c", "d"]
        segs = engine.merge3(["a", "B", "c", "d"], base, ["a", "b", "c", "D"])
        self.assertEqual([l for s in segs for l in engine.segment_output(s)], ["a", "B", "c", "D"])
        segs = engine.merge3(["a", "X", "c"], ["a", "b", "c"], ["a", "Y", "c"])
        self.assertEqual([s.kind for s in segs], ["text", "conflict", "text"])
        self.assertIsNone(engine.segment_output(segs[1]))
        segs[1].resolution = "both"
        self.assertEqual(engine.segment_output(segs[1]), ["X", "Y"])
        segs = engine.merge2(["a", "b"], ["a", "c"])
        self.assertEqual([s.kind for s in segs], ["text", "conflict"])
        # reject an automatic change by choosing the base text
        segs = engine.merge3(["a", "B"], ["a", "b"], ["a", "b"])
        self.assertEqual(segs[1].kind, "auto")
        segs[1].resolution = "base"
        self.assertEqual(engine.segment_output(segs[1]), ["b"])

    def test_classify3(self):
        self.assertEqual(engine.classify3("x", "x", "y")[0], "theirs")
        self.assertEqual(engine.classify3("y", "x", "x")[0], "ours")
        self.assertEqual(engine.classify3("y", "x", "z")[0], "conflict")
        self.assertEqual(engine.classify3("y", "y", None)[0], "delete")
        self.assertEqual(engine.classify3(None, None, "n")[0], "theirs")


class TableTests(unittest.TestCase):
    def test_html_and_csv(self):
        d = tempfile.mkdtemp()
        p = os.path.join(d, "t.html")
        with open(p, "w") as f:
            f.write("<table><tr><th>id</th><th>v</th></tr><tr><td>1</td><td>a</td></tr></table>"
                    "<table><tr><th>k</th></tr><tr><td>z</td></tr></table>")
        self.assertEqual(engine.load_table(p), (["id", "v"], [["1", "a"]]))
        self.assertEqual(engine.load_table(p, "Table 2"), (["k"], [["z"]]))
        self.assertEqual(len(engine.list_table_sources(p)), 2)
        c = os.path.join(d, "t.csv")
        with open(c, "w") as f:
            f.write("id,id,x\n1,2,3\n")
        h, b = engine.load_table(c)
        self.assertEqual(h, ["id", "id.1", "x"])
        shutil.rmtree(d, ignore_errors=True)

    def test_xlsx_sheets(self):
        import openpyxl
        d = tempfile.mkdtemp()
        p = os.path.join(d, "b.xlsx")
        wb = openpyxl.Workbook()
        wb.active.title = "One"
        wb.active.append(["id", "v"])
        wb.active.append([1, 2])
        ws = wb.create_sheet("Two")
        ws.append(["k"])
        ws.append(["z"])
        wb.save(p)
        self.assertEqual(engine.list_table_sources(p), ["One", "Two"])
        self.assertEqual(engine.load_table(p, "Two"), (["k"], [["z"]]))
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
