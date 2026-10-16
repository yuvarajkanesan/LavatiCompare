import os
import shutil
import sys
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
from lavati import config, engine  # noqa: E402
from lavati.text_view import TextCompareView  # noqa: E402


class TextViewTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg = config.Config(os.path.join(self.d, "cfg.json"))
        self.cfg["ignore_whitespace"] = False

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def f(self, name, text, enc="utf-8"):
        p = os.path.join(self.d, name)
        with open(p, "wb") as fh:
            fh.write(text.encode(enc))
        return p

    def view(self, a, b, **kw):
        v = TextCompareView(self.cfg, self.f("a.py", a), self.f("b.py", b), **kw)
        v.resize(1200, 700)
        v.show()
        return v

    def test_basic_diff_and_take(self):
        v = self.view("one\ntwo\nthree\nfour\n", "one\nTWO\nthree\nfour\nfive\n")
        self.assertEqual(len(v.hunks), 2)
        self.assertEqual(len(v.panes["L"].rows), len(v.panes["R"].rows))
        v.goto_hunk(0)
        v.take("right")                                   # left "two" -> right
        self.assertEqual(v.panes["R"].lines[1], "two")
        self.assertEqual(len(v.hunks), 1)
        self.assertTrue(v.has_unsaved())
        v.undo() if v._focus == "R" else v.panes["R"].undo_()
        v._flush()
        self.assertEqual(v.panes["R"].lines[1], "TWO")
        self.assertEqual(len(v.hunks), 2)

    def test_edit_in_place_rediffs_and_saves(self):
        v = self.view("alpha\nbeta\n", "alpha\ngamma\n")
        self.assertEqual(len(v.hunks), 1)
        pane = v.panes["R"]
        pane.setFocus()
        pane.set_cursor(1, 0)
        pane.anchor = (1, 0)
        pane.cur_li, pane.cur_col = 1, 5
        QTest.keyClicks(pane, "beta")
        v._flush()
        self.assertEqual(len(v.hunks), 0)
        self.assertEqual(pane.lines, ["alpha", "beta"])
        self.assertTrue(v.has_unsaved())
        v.save()
        with open(v.sides["R"].path, "rb") as fh:
            self.assertEqual(fh.read(), b"alpha\nbeta\n")
        self.assertFalse(v.has_unsaved())

    def test_encoding_eol_preserved(self):
        v = self.view("a\r\nb\r\n", "a\nb\n")
        self.assertEqual(v.sides["L"].eol, "CRLF")
        self.assertEqual(v.sides["R"].eol, "LF")
        self.assertEqual(len(v.hunks), 0)                 # line endings ignored by default
        v.rules.ignore_eol = False
        v._rebuild()
        self.assertGreater(len(v.hunks), 0)
        v.rules.ignore_eol = True
        v._rebuild()
        v.panes["L"].insert_text("x")
        v.save()
        with open(v.sides["L"].path, "rb") as fh:
            self.assertEqual(fh.read(), b"xa\r\nb\r\n")

    def test_utf16_roundtrip(self):
        v = self.view("héllo\n", "hello\n")
        p = self.f("u16.txt", "café\n", "utf-16")
        v2 = TextCompareView(self.cfg, p, self.f("u8.txt", "cafe\n"))
        self.assertEqual(v2.sides["L"].enc, "UTF-16 LE")
        v2.panes["L"].insert_text("!")
        v2.save()
        with open(p, "rb") as fh:
            raw = fh.read()
        self.assertTrue(raw.startswith(b"\xff\xfe"))
        self.assertEqual(raw.decode("utf-16"), "!café\n")

    def test_modes_wrap_layout_syntax(self):
        a = "\n".join(f"line {i}" for i in range(60)) + "\n"
        b = a.replace("line 30", "line thirty " * 40)
        v = self.view(a, b)
        v.seg.group.button(1).click()                     # diffs + context
        self.assertLess(len(v.disp), 60)
        v.seg.group.button(2).click()
        self.assertEqual(sum(1 for d in v.disp if d >= 0), 1)
        v.seg.group.button(0).click()
        v.set_wrap(True)
        self.assertIsNotNone(v.panes["L"].heights)
        self.assertGreater(max(v.panes["L"].heights), 1)
        v.set_layout("over")
        self.assertEqual(v.split.orientation(), Qt.Vertical)
        v.set_layout("side")
        v.panes["L"].grab()
        v.panes["R"].grab()
        v.set_wrap(False)

    def test_unimportant_and_rules_dialog_state(self):
        v = self.view("x = 1  # one\ny = 2\n", "x = 1  # two\ny = 2\n")
        self.assertEqual(len(v.hunks), 1)
        v.rules = v.rules.copy(unimportant=[engine.UNIMPORTANT_PRESETS["Script comments (# ...)"]])
        v._rebuild()
        self.assertEqual(len(v.hunks), 0)
        self.assertIn("unimportant", v.stats.text())

    def test_alignment_and_patch(self):
        v = self.view("a\nb\nc\n", "x\nb\nc\ny\n")
        v.panes["L"].set_cursor(0, 0)
        v.panes["R"].set_cursor(3, 0)
        v.align_cursors()
        self.assertEqual(v.anchors, [(0, 3)])
        pairs = [(p[0], p[1]) for p in v.pairs]
        self.assertIn((0, 3), pairs)
        v.clear_alignments()
        self.assertEqual(v.anchors, [])
        out = engine.unified_diff(v.panes["L"].lines, v.panes["R"].lines, "a", "b")
        self.assertTrue(out[0].startswith("---"))

    def test_find_and_swap_and_report(self):
        v = self.view("needle here\nnothing\n", "nothing\nneedle there\n")
        v.find_edit.setText("needle")
        v.find_next(1)
        self.assertIn(v.panes["L"].selected_text() + v.panes["R"].selected_text(), ("needle",))
        v.find_next(1)
        sel = v.panes["L"].selected_text() + v.panes["R"].selected_text()
        self.assertEqual(sel, "needle")
        left_before = list(v.panes["L"].lines)
        v.swap_sides()
        self.assertEqual(v.panes["R"].lines, left_before)
        from lavati import reports
        for fmt in ("html", "text", "xml"):
            out = os.path.join(self.d, "r." + fmt)
            reports.write_text_report(out, fmt, "a", "b", v.panes["L"].lines, v.panes["R"].lines, v.rules)
            self.assertGreater(os.path.getsize(out), 100)
        import xml.dom.minidom
        xml.dom.minidom.parse(os.path.join(self.d, "r.xml"))

    def test_empty_and_missing_files(self):
        v = TextCompareView(self.cfg, self.f("e.txt", ""), os.path.join(self.d, "missing.txt"))
        self.assertEqual(v.panes["L"].lines, [])
        self.assertEqual(len(v.hunks), 0)
        pane = v.panes["L"]
        QTest.keyClicks(pane, "hi")
        QTest.keyClick(pane, Qt.Key_Return)
        QTest.keyClicks(pane, "there")
        v._flush()
        self.assertEqual(pane.lines, ["hi", "there"])
        for _ in range(3):
            pane.undo_()
        self.assertEqual(pane.lines, [])


if __name__ == "__main__":
    unittest.main()


class PastedTextTests(unittest.TestCase):
    def test_compare_without_files(self):
        d = tempfile.mkdtemp()
        try:
            cfg = config.Config(os.path.join(d, "cfg.json"))
            v = TextCompareView(cfg, "", "")
            v.show()
            v.panes["L"].replace_lines(0, 0, ["alpha", "beta", "gamma"])
            v.panes["R"].replace_lines(0, 0, ["alpha", "BETA", "gamma", "delta"])
            v._flush()
            self.assertEqual(len(v.hunks), 2)
            self.assertFalse(v.has_unsaved())            # a scratch pad never nags about saving
            v.combine("L", 0)
            self.assertIn("beta", v.panes["R"].lines)
            v.close()
        finally:
            shutil.rmtree(d, ignore_errors=True)
