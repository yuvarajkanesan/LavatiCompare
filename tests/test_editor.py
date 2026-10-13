import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QGuiApplication  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
from lavati import editor, theme  # noqa: E402


def make(lines, **kw):
    p = editor.EditorPane()
    p.resize(600, 300)
    p.set_lines(lines)
    p.set_rows([editor.ERow(i) for i in range(len(p.lines))])
    p.show()
    p.setFocus()
    return p


class EditorTests(unittest.TestCase):
    def type(self, p, text):
        QTest.keyClicks(p, text)

    def test_typing_and_undo_redo(self):
        p = make(["hello", "world"])
        p.set_cursor(0, 5)
        self.type(p, " there")
        self.assertEqual(p.lines[0], "hello there")
        self.assertTrue(p.is_modified())
        p.undo_()
        self.assertEqual(p.lines[0], "hello")        # typing coalesced into one undo step
        p.redo_()
        self.assertEqual(p.lines[0], "hello there")
        p.mark_saved()
        self.assertFalse(p.is_modified())
        self.type(p, "!")
        self.assertTrue(p.is_modified())
        p.undo_()
        self.assertFalse(p.is_modified())

    def test_enter_backspace_delete(self):
        p = make(["    abc", "def"])
        p.set_cursor(0, 7)
        QTest.keyClick(p, Qt.Key_Return)
        self.assertEqual(p.lines[:3], ["    abc", "    ", "def"][:3] if False else p.lines[:3])
        self.assertEqual(p.lines, ["    abc", "    ", "def"])      # auto-indent
        QTest.keyClick(p, Qt.Key_Backspace)
        self.assertEqual(p.lines[1], "   ")
        p.set_cursor(1, 0)
        QTest.keyClick(p, Qt.Key_Backspace)                         # join with previous line
        self.assertEqual(p.lines, ["    abc   ", "def"])
        p.set_cursor(0, len(p.lines[0]))
        QTest.keyClick(p, Qt.Key_Delete)                            # join with next
        self.assertEqual(p.lines, ["    abc   def"])
        p.undo_()
        self.assertEqual(p.lines, ["    abc   ", "def"])

    def test_selection_clipboard(self):
        p = make(["one two", "three four"])
        p.set_cursor(0, 4)
        p.set_cursor(1, 5, extend=True)
        self.assertEqual(p.selected_text(), "two\nthree")
        p.copy()
        self.assertEqual(QGuiApplication.clipboard().text(), "two\nthree")
        p.cut()
        self.assertEqual(p.lines, ["one  four"])
        p.paste()
        self.assertEqual(p.lines, ["one two", "three four"])
        QGuiApplication.clipboard().setText("A\r\nB\nC")
        p.set_cursor(0, 0)
        p.paste()
        self.assertEqual(p.lines[:3], ["A", "B", "Cone two"])
        self.assertEqual((p.cur_li, p.cur_col), (2, 1))
        p.undo_()
        self.assertEqual(p.lines, ["one two", "three four"])

    def test_select_all_type_over(self):
        p = make(["a", "b"])
        p.select_all()
        self.type(p, "x")
        self.assertEqual(p.lines, ["x"])

    def test_navigation(self):
        p = make(["ab", "", "cdef"])
        p.set_cursor(0, 2)
        QTest.keyClick(p, Qt.Key_Down)
        self.assertEqual((p.cur_li, p.cur_col), (1, 0))
        QTest.keyClick(p, Qt.Key_Down)
        self.assertEqual((p.cur_li, p.cur_col), (2, 2))            # sticky column
        QTest.keyClick(p, Qt.Key_End)
        self.assertEqual(p.cur_col, 4)
        QTest.keyClick(p, Qt.Key_Home, Qt.ControlModifier)
        self.assertEqual((p.cur_li, p.cur_col), (0, 0))
        QTest.keyClick(p, Qt.Key_Right, Qt.ControlModifier)
        self.assertEqual((p.cur_li, p.cur_col), (0, 2))

    def test_filler_rows_skip(self):
        p = editor.EditorPane()
        p.resize(500, 300)
        p.set_lines(["a", "b"])
        p.set_rows([editor.ERow(0), editor.ERow(None, hatched=True), editor.ERow(1)])
        p.show()
        p.set_cursor(0, 0)
        QTest.keyClick(p, Qt.Key_Down)
        self.assertEqual(p.cur_li, 1)

    def test_tabs_columns(self):
        self.assertEqual(editor.disp_col("\tab", 1), 4)
        self.assertEqual(editor.raw_col("\tab", 4), 1)
        self.assertEqual(editor.raw_col("\tab", 5), 2)
        self.assertEqual(editor.raw_col("abc", 99), 3)

    def test_readonly(self):
        p = make(["abc"])
        p.readonly = True
        p.set_cursor(0, 0)
        self.type(p, "zzz")
        self.assertEqual(p.lines, ["abc"])

    def test_replace_lines_and_paint(self):
        p = make(["1", "2", "3"])
        p.replace_lines(1, 2, ["x", "y"])
        self.assertEqual(p.lines, ["1", "x", "y", "3"])
        p.undo_()
        self.assertEqual(p.lines, ["1", "2", "3"])
        # painting with wrap, selection, marks and spans must not raise
        p.wrap = True
        p.lines = ["word " * 80, "\ttabbed line"]
        p.set_rows([editor.ERow(0, "mod"), editor.ERow(1, "mod", bg=None)], heights=[4, 1])
        p.marks = [[(0, 4, "k")], [(1, 6, "s")]]
        p.span_provider = lambda r: [(2, 6, p.palette().color(p.backgroundRole()))]
        p.set_cursor(0, 20)
        p.anchor = (0, 3)
        p.grab()
        p.wrap = False
        p.set_rows(p.rows, None)
        p.grab()


if __name__ == "__main__":
    unittest.main()
