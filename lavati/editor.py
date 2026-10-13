"""Editable, row-aligned text pane used by Text Compare.

Two panes share the same row model (a row is one aligned line pair; filler rows keep both panes
vertically aligned). Each pane owns the real lines of its file and edits them in place with
undo/redo, selection, clipboard, word wrap and syntax colours.
"""
import bisect
import re

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QGuiApplication, QKeySequence, QPainter, QPen,
                           QPolygonF)
from PySide6.QtWidgets import QAbstractScrollArea, QFrame, QToolTip, QWidget

from . import theme

_SPLIT = re.compile(r"\r\n|\r|\n")
TAB = 4
_WORD = re.compile(r"\w+|[^\w\s]+|\s+")


def syntax_color(kind):
    if theme.is_dark():
        t = {"c": "#7f8aa8", "s": "#e6a36b", "n": "#7fb0ff", "k": "#c39bff", "t": "#5fd1c4", "p": "#8fc7ff", "d": "#e5c07b"}
    else:
        t = {"c": "#7a8194", "s": "#a1471d", "n": "#1a5fd0", "k": "#7a2fb8", "t": "#0d7a70", "p": "#2b6cb0", "d": "#9a6700"}
    return QColor(t.get(kind, theme.TEXT))


class ERow:
    __slots__ = ("li", "kind", "hunk", "bg", "sign", "hatched", "bar")

    def __init__(self, li=None, kind="eq", hunk=-1, bg=None, sign="", hatched=False, bar=None):
        self.li, self.kind, self.hunk = li, kind, hunk
        self.bg, self.sign, self.hatched, self.bar = bg, sign, hatched, bar


class _Undo:
    __slots__ = ("a", "old", "new", "before", "after", "key")

    def __init__(self, a, old, new, before, after, key):
        self.a, self.old, self.new, self.before, self.after, self.key = a, old, new, before, after, key


def disp_col(text, col):
    """Display column of character index `col` (tabs expand to TAB stops)."""
    if "\t" not in text:
        return col
    return len(text[:col].expandtabs(TAB))


def raw_col(text, dcol):
    """Character index closest to display column `dcol`."""
    if "	" not in text:
        return max(0, min(len(text), dcol))
    c = 0
    for i, ch in enumerate(text):
        w = TAB - c % TAB if ch == "	" else 1
        if dcol < c + w / 2.0:
            return i
        c += w
    return len(text)


class EditorPane(QAbstractScrollArea):
    edited = Signal()                 # the text of this pane changed
    spliced = Signal(int, int, int)   # first line, lines removed, lines inserted
    cursorMoved = Signal(int, int)    # real line, column
    rowClicked = Signal(int)
    contextRequested = Signal(int, QPoint)
    focusedIn = Signal()
    resized = Signal()
    blameClicked = Signal(int)        # real line whose blame cell was clicked

    NUM_W = 58
    SIGN_W = 22
    BLAME_W = 300

    def __init__(self, parent=None, show_sign=True):
        super().__init__(parent)
        self.lines = []
        self.rows = []
        self.heights = None
        self._ys = [0]
        self._li2row = {}
        self.readonly = False
        self.blame = None             # per real line: {sha, author, time, summary}; None = column hidden
        self.blame_w = 0
        self.NUM_W = EditorPane.NUM_W
        self.wrap = False
        self.wrap_cols = None
        self.show_sign = show_sign
        self.span_provider = None
        self.marks = []
        self.cur_li = 0
        self.cur_col = 0
        self.anchor = None
        self.want_dcol = None
        self.undo, self.redo = [], []
        self._last_key = None
        self._saved_token = None
        self.current_rows = None
        self.find_term, self.find_case = "", False
        self.empty_text = ""
        self._cursor_on = True
        self._dragging = False
        self._maxcols = 0
        self._font_setup()
        self.setFrameShape(QFrame.NoFrame)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAttribute(Qt.WA_InputMethodEnabled, True)
        self.viewport().setCursor(Qt.IBeamCursor)
        self.viewport().setMouseTracking(False)
        self.verticalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        self.horizontalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        self._blink = QTimer(self)
        self._blink.setInterval(530)
        self._blink.timeout.connect(self._toggle_cursor)

    def _font_setup(self):
        self.setFont(theme.mono())
        fm = QFontMetrics(self.font())
        self.cw = fm.horizontalAdvance("M" * 64) / 64.0
        self.rh = max(fm.height() + 8, 22)
        self.verticalScrollBar().setSingleStep(self.rh)

    # ------------------------------------------------------------ blame column
    def set_blame(self, data):
        """Show (or with None hide) the 'who changed this line' column to the left of the line numbers."""
        self.blame = data or None
        self.blame_w = self.BLAME_W if self.blame else 0
        self.NUM_W = EditorPane.NUM_W + self.blame_w
        self._update_bars()
        self.viewport().update()
        self.resized.emit()

    def _blame_at(self, pos):
        if not self.blame_w or pos.x() >= self.blame_w:
            return None
        r = self.row_at_y(int(pos.y()))
        li = self.rows[r].li if r >= 0 else None
        if li is None or li >= len(self.blame) or self.blame[li] is None:
            return None
        return li

    @staticmethod
    def _age(t):
        import time
        d = max(0, time.time() - t)
        for secs, unit in ((31557600, "y"), (2629800, "mo"), (604800, "w"), (86400, "d"), (3600, "h"), (60, "m")):
            if d >= secs:
                return f"{int(d // secs)}{unit} ago"
        return "just now"

    def viewportEvent(self, ev):
        if ev.type() == QEvent.ToolTip and self.blame_w:
            li = self._blame_at(ev.pos())
            if li is not None:
                import time
                b = self.blame[li]
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(b["time"])) if b["time"] else ""
                QToolTip.showText(ev.globalPos(), f"{b['sha'][:8]}  {b['author']}  {when}\n\n{b['summary']}\n\n(click to open the commit)", self)
                return True
            QToolTip.hideText()
        return super().viewportEvent(ev)

    @staticmethod
    def _sha_color(sha):
        h = int(sha[:6], 16) % 360 if sha and set(sha) != {"0"} else 30
        return QColor.fromHsv(h, 120 if set(sha) != {"0"} else 200, 200)

    def _paint_blame(self, p, y, li):
        if li is None or li >= len(self.blame) or self.blame[li] is None:
            return
        b = self.blame[li]
        col = self._sha_color(b["sha"])
        p.fillRect(QRect(5, int(y) + 2, 3, self.rh - 4), col)
        f = p.font()
        old = QFont(f)
        f.setPointSizeF(max(7.0, f.pointSizeF() - 2))
        p.setFont(f)
        p.setPen(QColor(theme.MUTED))
        text = f"{b['sha'][:7]}  {b['author']} · {self._age(b['time'])}" if b["time"] else f"{b['author']}"
        text = QFontMetrics(f).elidedText(text, Qt.ElideRight, self.blame_w - 18)
        p.drawText(QRect(14, int(y), self.blame_w - 16, self.rh), Qt.AlignVCenter | Qt.AlignLeft, text)
        p.setFont(old)

    # ------------------------------------------------------------ geometry
    def text_x(self):
        return self.NUM_W + (self.SIGN_W if self.show_sign else 0) + 8

    def cols(self):
        """Characters per visual line when wrapping."""
        if self.wrap_cols:
            return self.wrap_cols
        return self.natural_cols()

    def natural_cols(self):
        return max(10, int((self.viewport().width() - self.text_x() - 10) / self.cw))

    def row_top(self, r):
        if r < 0:
            return 0
        return self._ys[min(r, len(self._ys) - 1)]

    def row_height_px(self, r):
        if 0 <= r < len(self.rows):
            return self._ys[r + 1] - self._ys[r]
        return self.rh

    def total_height(self):
        return self._ys[-1]

    def row_at_y(self, y):
        """Row index at viewport y, or -1."""
        yy = y + self.verticalScrollBar().value()
        if yy < 0 or yy >= self._ys[-1]:
            return -1
        return bisect.bisect_right(self._ys, yy) - 1

    def row_of_line(self, li):
        r = self._li2row.get(li)
        if r is not None:
            return r
        return min(len(self.rows) - 1, max(0, li)) if self.rows else 0

    def _relayout(self):
        n = len(self.rows)
        h = self.heights
        ys = [0] * (n + 1)
        rh = self.rh
        if h is None:
            for i in range(n):
                ys[i + 1] = ys[i] + rh
        else:
            for i in range(n):
                ys[i + 1] = ys[i] + (h[i] if i < len(h) else 1) * rh
        self._ys = ys
        self._li2row = {r.li: i for i, r in enumerate(self.rows) if r.li is not None}
        self._update_bars()

    def _update_bars(self):
        vp = self.viewport()
        self.verticalScrollBar().setRange(0, max(0, self._ys[-1] - vp.height()))
        self.verticalScrollBar().setPageStep(max(vp.height() - self.rh, self.rh))
        if self.wrap:
            self.horizontalScrollBar().setRange(0, 0)
        else:
            content_w = self.text_x() + self._maxcols * self.cw + 40
            self.horizontalScrollBar().setRange(0, max(0, int(content_w - vp.width())))
            self.horizontalScrollBar().setPageStep(vp.width())

    def _calc_maxcols(self):
        m = 0
        for s in self.lines:
            n = len(s) if "\t" not in s else len(s.expandtabs(TAB))
            if n > m:
                m = n
        self._maxcols = m

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_bars()
        self.resized.emit()

    # --------------------------------------------------------------- model
    def set_lines(self, lines, reset_undo=True):
        self.lines = list(lines)
        if reset_undo:
            self.undo, self.redo = [], []
            self._last_key = None
            self._saved_token = None
            self.cur_li = self.cur_col = 0
            self.anchor = None
        self.cur_li = min(self.cur_li, max(0, len(self.lines) - 1))
        self._calc_maxcols()

    def set_rows(self, rows, heights=None, keep_scroll=True):
        v = self.verticalScrollBar().value() if keep_scroll else 0
        self.rows = rows
        self.heights = heights
        self._relayout()
        self.verticalScrollBar().setValue(min(v, self.verticalScrollBar().maximum()))
        self.viewport().update()

    def set_heights(self, heights):
        self.heights = heights
        self._relayout()
        self.viewport().update()

    def line(self, li):
        return self.lines[li] if 0 <= li < len(self.lines) else ""

    def is_modified(self):
        top = self.undo[-1] if self.undo else None
        return top is not self._saved_token

    def mark_saved(self):
        self._saved_token = self.undo[-1] if self.undo else None
        self._last_key = None

    def can_undo(self):
        return bool(self.undo)

    def can_redo(self):
        return bool(self.redo)

    def _state(self):
        return (self.cur_li, self.cur_col, self.anchor)

    def _splice(self, a, old_count, new_lines, after, key=None):
        """Replace lines[a:a+old_count] with new_lines (the single edit primitive)."""
        old = self.lines[a:a + old_count]
        before = self._state()
        merged = False
        if (key is not None and self.undo and key == self._last_key and self.undo[-1].key == key
                and self.undo[-1].a == a and len(self.undo[-1].new) == old_count == len(new_lines) == 1):
            u = self.undo[-1]
            u.new, u.after = list(new_lines), after
            merged = True
        if not merged:
            u = _Undo(a, old, list(new_lines), before, after, key)
            self.undo.append(u)
            if len(self.undo) > 2000:
                del self.undo[0]
        self.redo = []
        self._last_key = key
        self.lines[a:a + old_count] = new_lines
        self.cur_li, self.cur_col, self.anchor = after
        self._after_edit(a, old_count, len(new_lines))

    def _after_edit(self, a, old_n, new_n):
        for li in range(a, a + new_n):
            if li < len(self.lines):
                s = self.lines[li]
                n = len(s) if "\t" not in s else len(s.expandtabs(TAB))
                if n > self._maxcols:
                    self._maxcols = n
        if old_n != new_n or (self.heights is not None):
            # structure changed: the owner rebuilds rows; keep indices sane until then
            pass
        self.spliced.emit(a, old_n, new_n)
        self.edited.emit()
        self._ensure_cursor_visible()
        self.viewport().update()
        self.cursorMoved.emit(self.cur_li, self.cur_col)

    def replace_lines(self, a, b, new_lines, select=False):
        """Programmatic edit (hunk copy): lines[a:b] = new_lines, undoable."""
        a, b = max(0, a), max(0, b)
        after = (a, 0, None)
        self._splice(a, b - a, list(new_lines), after, key=None)
        if select and new_lines:
            self.anchor = (a, 0)
            self.cur_li, self.cur_col = a + len(new_lines) - 1, len(new_lines[-1])

    def undo_(self):
        if not self.undo:
            return
        u = self.undo.pop()
        self.lines[u.a:u.a + len(u.new)] = u.old
        self.redo.append(u)
        self._last_key = None
        self.cur_li, self.cur_col, self.anchor = u.before
        self._clamp_cursor()
        self._after_edit(u.a, len(u.new), len(u.old))

    def redo_(self):
        if not self.redo:
            return
        u = self.redo.pop()
        self.lines[u.a:u.a + len(u.old)] = u.new
        self.undo.append(u)
        self._last_key = None
        self.cur_li, self.cur_col, self.anchor = u.after
        self._clamp_cursor()
        self._after_edit(u.a, len(u.old), len(u.new))

    def _clamp_cursor(self):
        self.cur_li = max(0, min(self.cur_li, len(self.lines) - 1))
        self.cur_col = max(0, min(self.cur_col, len(self.line(self.cur_li))))
        if self.anchor is not None:
            al = max(0, min(self.anchor[0], len(self.lines) - 1))
            self.anchor = (al, max(0, min(self.anchor[1], len(self.line(al)))))

    # ----------------------------------------------------------- selection
    def selection(self):
        """Ordered ((li, col), (li, col)) or None."""
        if self.anchor is None or self.anchor == (self.cur_li, self.cur_col):
            return None
        a, b = self.anchor, (self.cur_li, self.cur_col)
        return (a, b) if a <= b else (b, a)

    def selected_text(self):
        sel = self.selection()
        if not sel:
            return ""
        (l1, c1), (l2, c2) = sel
        if l1 == l2:
            return self.lines[l1][c1:c2]
        parts = [self.lines[l1][c1:]] + self.lines[l1 + 1:l2] + [self.lines[l2][:c2]]
        return "\n".join(parts)

    def select_all(self):
        self.anchor = (0, 0)
        last = len(self.lines) - 1
        self.cur_li, self.cur_col = last, len(self.lines[last])
        self._moved()

    def set_cursor(self, li, col, extend=False, scroll=True):
        li = max(0, min(li, len(self.lines) - 1))
        col = max(0, min(col, len(self.line(li))))
        if extend:
            if self.anchor is None:
                self.anchor = (self.cur_li, self.cur_col)
        else:
            self.anchor = None
        self.cur_li, self.cur_col = li, col
        self._moved(scroll)

    def _moved(self, scroll=True):
        self._last_key = None
        self._cursor_on = True
        if scroll:
            self._ensure_cursor_visible()
        self.viewport().update()
        self.cursorMoved.emit(self.cur_li, self.cur_col)

    def _cursor_xy(self):
        """(x, y) of the cursor in content coordinates (before scrolling)."""
        r = self.row_of_line(self.cur_li)
        text = self.line(self.cur_li)
        dc = disp_col(text, self.cur_col)
        if self.wrap:
            cols = self.cols()
            return self.text_x() + (dc % cols) * self.cw, self.row_top(r) + (dc // cols) * self.rh
        return self.text_x() + dc * self.cw, self.row_top(r)

    def _ensure_cursor_visible(self):
        if not self.rows:
            return
        x, y = self._cursor_xy()
        vb, hb = self.verticalScrollBar(), self.horizontalScrollBar()
        h = self.viewport().height()
        if y < vb.value():
            vb.setValue(int(y))
        elif y + self.rh > vb.value() + h:
            vb.setValue(int(y + self.rh - h))
        if not self.wrap:
            w = self.viewport().width()
            if x - hb.value() < self.text_x():
                hb.setValue(max(0, int(x - self.text_x() - 40)))
            elif x - hb.value() > w - 30:
                hb.setValue(int(x - w + 60))

    def ensure_visible_row(self, r, center=False):
        if not (0 <= r < len(self.rows)):
            return
        vb = self.verticalScrollBar()
        top, h = self.row_top(r), self.viewport().height()
        if center:
            vb.setValue(max(0, int(top - h / 3)))
        elif top < vb.value():
            vb.setValue(int(top))
        elif top + self.row_height_px(r) > vb.value() + h:
            vb.setValue(int(top + self.row_height_px(r) - h))

    # ------------------------------------------------------------- editing
    def _guard(self):
        if self.readonly:
            from .widgets import toast
            toast("This side is read-only (converted or archived content)", "warn")
            return False
        return True

    def _delete_selection(self, key=None):
        sel = self.selection()
        if not sel:
            return False
        (l1, c1), (l2, c2) = sel
        joined = self.lines[l1][:c1] + self.lines[l2][c2:]
        self._splice(l1, l2 - l1 + 1, [joined], (l1, c1, None), key=key)
        return True

    def insert_text(self, text, key=None):
        if not self._guard() or not text:
            return
        parts = _SPLIT.split(text)
        sel = self.selection()
        if not self.lines:                       # empty document: nothing to replace
            new = list(parts)
            self._splice(0, 0, new, (len(new) - 1, len(new[-1]), None), key=key if len(parts) == 1 else None)
            return
        if sel:
            (l1, c1), (l2, c2) = sel
            first, last, a, n = self.lines[l1][:c1], self.lines[l2][c2:], l1, l2 - l1 + 1
        else:
            a, n = self.cur_li, 1
            first, last = self.lines[a][:self.cur_col], self.lines[a][self.cur_col:]
        new = list(parts)
        new[0] = first + new[0]
        endcol = len(new[-1]) if len(new) > 1 else len(first) + len(parts[0])
        new[-1] = new[-1] + last
        self._splice(a, n, new, (a + len(new) - 1, endcol, None),
                     key=key if len(parts) == 1 and not sel else None)

    def _newline(self):
        if not self._guard():
            return
        if not self.lines:
            self._splice(0, 0, ["", ""], (1, 0, None))
            return
        sel = self.selection()
        if sel:
            self._delete_selection()
        s = self.lines[self.cur_li]
        indent = re.match(r"[ \t]*", s).group()[:self.cur_col]
        left, right = s[:self.cur_col], s[self.cur_col:]
        self._splice(self.cur_li, 1, [left, indent + right], (self.cur_li + 1, len(indent), None))

    def _backspace(self, word=False):
        if not self._guard():
            return
        if self._delete_selection():
            return
        li, c = self.cur_li, self.cur_col
        if c > 0:
            s = self.lines[li]
            start = self._word_left(li, c)[1] if word else c - 1
            self._splice(li, 1, [s[:start] + s[c:]], (li, start, None), key=("t", li))
        elif li > 0:
            prev = self.lines[li - 1]
            self._splice(li - 1, 2, [prev + self.lines[li]], (li - 1, len(prev), None))

    def _delete(self, word=False):
        if not self._guard():
            return
        if self._delete_selection():
            return
        li, c = self.cur_li, self.cur_col
        s = self.lines[li]
        if c < len(s):
            end = self._word_right(li, c)[1] if word else c + 1
            self._splice(li, 1, [s[:c] + s[end:]], (li, c, None), key=("t", li))
        elif li + 1 < len(self.lines):
            self._splice(li, 2, [s + self.lines[li + 1]], (li, c, None))

    def cut(self):
        if self.selection() and self._guard():
            QGuiApplication.clipboard().setText(self.selected_text())
            self._delete_selection()

    def copy(self):
        t = self.selected_text()
        if t:
            QGuiApplication.clipboard().setText(t)

    def paste(self):
        t = QGuiApplication.clipboard().text()
        if t:
            self.insert_text(t)

    def _word_left(self, li, col):
        s = self.lines[li]
        if col == 0:
            return (li - 1, len(self.lines[li - 1])) if li > 0 else (li, 0)
        i = col
        while i > 0 and s[i - 1].isspace():
            i -= 1
        if i > 0:
            j = i - 1
            kind = s[j].isalnum() or s[j] == "_"
            while j > 0 and ((s[j - 1].isalnum() or s[j - 1] == "_") == kind) and not s[j - 1].isspace():
                j -= 1
            i = j
        return li, i

    def _word_right(self, li, col):
        s = self.lines[li]
        if col >= len(s):
            return (li + 1, 0) if li + 1 < len(self.lines) else (li, col)
        i = col
        kind = s[i].isalnum() or s[i] == "_"
        while i < len(s) and not s[i].isspace() and ((s[i].isalnum() or s[i] == "_") == kind):
            i += 1
        while i < len(s) and s[i].isspace():
            i += 1
        return li, i

    # --------------------------------------------------------------- input
    def keyPressEvent(self, e):
        key, mod = e.key(), e.modifiers()
        shift = bool(mod & Qt.ShiftModifier)
        ctrl = bool(mod & Qt.ControlModifier)
        if e.matches(QKeySequence.Copy):
            return self.copy()
        if e.matches(QKeySequence.SelectAll):
            return self.select_all()
        if e.matches(QKeySequence.Cut):
            return self.cut()
        if e.matches(QKeySequence.Paste):
            return self.paste()
        if e.matches(QKeySequence.Undo):
            return self.undo_()
        if e.matches(QKeySequence.Redo) or (ctrl and key == Qt.Key_Y):
            return self.redo_()
        li, col = self.cur_li, self.cur_col
        n = len(self.lines)
        if n == 0:
            if key in (Qt.Key_Return, Qt.Key_Enter):
                return self._newline()
            txt = e.text()
            if txt and not ctrl and txt.isprintable():
                return self.insert_text(txt, key=("t", 0))
            if key == Qt.Key_Tab and not ctrl:
                return self.insert_text("	")
            return
        if key in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down, Qt.Key_Home, Qt.Key_End,
                   Qt.Key_PageUp, Qt.Key_PageDown):
            sel = self.selection()
            if key == Qt.Key_Left:
                if sel and not shift:
                    return self.set_cursor(*sel[0])
                if ctrl:
                    nl, nc = self._word_left(li, col)
                else:
                    nl, nc = (li, col - 1) if col > 0 else ((li - 1, len(self.lines[li - 1])) if li > 0 else (li, 0))
                self.want_dcol = None
                return self.set_cursor(nl, nc, shift)
            if key == Qt.Key_Right:
                if sel and not shift:
                    return self.set_cursor(*sel[1])
                if ctrl:
                    nl, nc = self._word_right(li, col)
                else:
                    s = self.lines[li]
                    nl, nc = (li, col + 1) if col < len(s) else ((li + 1, 0) if li + 1 < n else (li, col))
                self.want_dcol = None
                return self.set_cursor(nl, nc, shift)
            if key in (Qt.Key_Up, Qt.Key_Down, Qt.Key_PageUp, Qt.Key_PageDown):
                step = 1 if key == Qt.Key_Down else -1
                if key in (Qt.Key_PageUp, Qt.Key_PageDown):
                    lines_per_page = max(1, self.viewport().height() // self.rh - 1)
                    step *= lines_per_page
                if self.want_dcol is None:
                    self.want_dcol = disp_col(self.lines[li], col)
                r = self.row_of_line(li)
                # move by rows so the cursor skips filler rows naturally
                nl = li
                rr = r
                moved = 0
                while moved < abs(step):
                    rr += 1 if step > 0 else -1
                    if rr < 0 or rr >= len(self.rows):
                        break
                    cand = self.rows[rr].li
                    if cand is not None:
                        nl = cand
                        moved += 1
                if not self.rows:
                    nl = max(0, min(n - 1, li + step))
                if nl == li and ((step < 0 and li == 0) or (step > 0 and li == n - 1)):
                    nc = 0 if step < 0 else len(self.lines[li])
                    self.want_dcol = None
                    return self.set_cursor(li, nc, shift)
                nc = raw_col(self.lines[nl], self.want_dcol)
                keep = self.want_dcol
                self.set_cursor(nl, nc, shift)
                self.want_dcol = keep
                return
            if key == Qt.Key_Home:
                if ctrl:
                    return self.set_cursor(0, 0, shift)
                s = self.lines[li]
                indent = len(s) - len(s.lstrip())
                return self.set_cursor(li, indent if col != indent else 0, shift)
            if key == Qt.Key_End:
                if ctrl:
                    return self.set_cursor(n - 1, len(self.lines[n - 1]), shift)
                return self.set_cursor(li, len(self.lines[li]), shift)
        self.want_dcol = None
        if key in (Qt.Key_Return, Qt.Key_Enter):
            return self._newline()
        if key == Qt.Key_Backspace:
            return self._backspace(ctrl)
        if key == Qt.Key_Delete:
            return self._delete(ctrl)
        if key == Qt.Key_Tab and not ctrl:
            return self.insert_text("\t")
        if key == Qt.Key_Escape:
            self.anchor = None
            self.viewport().update()
            return
        txt = e.text()
        if txt and not ctrl and txt.isprintable():
            return self.insert_text(txt, key=("t", self.cur_li))
        super().keyPressEvent(e)

    def inputMethodEvent(self, e):
        if e.commitString():
            self.insert_text(e.commitString())
        e.accept()

    def inputMethodQuery(self, q):
        if q == Qt.ImCursorRectangle:
            x, y = self._cursor_xy()
            return QRect(int(x - self.horizontalScrollBar().value()), int(y - self.verticalScrollBar().value()),
                         2, self.rh)
        return super().inputMethodQuery(q)

    # --------------------------------------------------------------- mouse
    def _pos_to_cursor(self, pos):
        y = pos.y()
        r = self.row_at_y(y)
        if r < 0:
            r = 0 if y < 0 else len(self.rows) - 1
            if r < 0:
                return 0, 0
        row = self.rows[r]
        x = pos.x() + self.horizontalScrollBar().value() - self.text_x() if not self.wrap else pos.x() - self.text_x()
        if row.li is None:
            # filler row: snap to the nearest real line
            for d in range(1, len(self.rows)):
                for cand in (r - d, r + d):
                    if 0 <= cand < len(self.rows) and self.rows[cand].li is not None:
                        li = self.rows[cand].li
                        return li, (len(self.lines[li]) if cand < r else 0)
            return 0, 0
        text = self.lines[row.li] if row.li < len(self.lines) else ""
        k = 0
        if self.wrap:
            k = int((y + self.verticalScrollBar().value() - self.row_top(r)) // self.rh)
        dc = int(round(x / self.cw)) + (k * self.cols() if self.wrap else 0)
        return row.li, raw_col(text, max(0, dc))

    def mousePressEvent(self, e):
        self.setFocus()
        if e.button() == Qt.RightButton:
            r = self.row_at_y(int(e.position().y()))
            if r >= 0 and not self.selection():
                li, c = self._pos_to_cursor(e.position())
                self.set_cursor(li, c)
            self.contextRequested.emit(r, e.globalPosition().toPoint())
            return
        if e.button() != Qt.LeftButton:
            return
        bl = self._blame_at(e.position())
        if bl is not None:
            self.blameClicked.emit(bl)
            return
        r = self.row_at_y(int(e.position().y()))
        li, c = self._pos_to_cursor(e.position())
        self.want_dcol = None
        self.set_cursor(li, c, extend=bool(e.modifiers() & Qt.ShiftModifier))
        self._dragging = True
        if r >= 0:
            self.rowClicked.emit(r)

    def mouseMoveEvent(self, e):
        if self._dragging and (e.buttons() & Qt.LeftButton):
            li, c = self._pos_to_cursor(e.position())
            if self.anchor is None:
                self.anchor = (self.cur_li, self.cur_col)
            self.cur_li, self.cur_col = li, max(0, min(c, len(self.line(li))))
            self._moved()

    def mouseReleaseEvent(self, e):
        self._dragging = False

    def mouseDoubleClickEvent(self, e):
        if e.button() != Qt.LeftButton:
            return
        li, c = self._pos_to_cursor(e.position())
        s = self.line(li)
        a = b = c
        for m in _WORD.finditer(s):
            if m.start() <= c <= m.end():
                a, b = m.start(), m.end()
                if m.group().isspace() and m.start() < c:
                    continue
                break
        self.anchor = (li, a)
        self.cur_li, self.cur_col = li, b
        self._moved()

    def contextMenuEvent(self, e):
        e.accept()

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self._cursor_on = True
        self._blink.start()
        self.viewport().update()
        self.focusedIn.emit()

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self._blink.stop()
        self._cursor_on = False
        self.viewport().update()

    def _toggle_cursor(self):
        self._cursor_on = not self._cursor_on
        self.viewport().update()

    # -------------------------------------------------------------- finding
    def find_matches(self, li):
        """[(start, end)] of the find term in a real line."""
        t = self.find_term
        if not t or not (0 <= li < len(self.lines)):
            return []
        s = self.lines[li]
        hay, needle = (s, t) if self.find_case else (s.lower(), t.lower())
        out, i = [], 0
        while True:
            j = hay.find(needle, i)
            if j < 0:
                break
            out.append((j, j + len(needle)))
            i = j + max(1, len(needle))
        return out

    # --------------------------------------------------------------- paint
    def paintEvent(self, ev):
        p = QPainter(self.viewport())
        p.setFont(self.font())
        w, h = self.viewport().width(), self.viewport().height()
        p.fillRect(0, 0, w, h, QColor(theme.PANEL))
        if not self.rows:
            if self.empty_text:
                p.setPen(QColor(theme.MUTED))
                p.drawText(self.viewport().rect(), Qt.AlignCenter, self.empty_text)
            return
        off = self.verticalScrollBar().value()
        hoff = 0 if self.wrap else self.horizontalScrollBar().value()
        first = max(0, bisect.bisect_right(self._ys, off) - 1)
        fm = p.fontMetrics()
        asc = (fm.ascent() - fm.descent())
        gutter_bg = QColor(theme.SURFACE2)
        sel = self.selection()
        focused = self.hasFocus()
        r = first
        n = len(self.rows)
        while r < n:
            y = self._ys[r] - off
            if y > h:
                break
            self._paint_row(p, r, y, w, hoff, fm, asc, gutter_bg, sel, focused)
            r += 1
        p.setPen(QColor(theme.HOVER))
        p.drawLine(self.NUM_W, 0, self.NUM_W, h)
        if self.blame_w:
            p.drawLine(self.blame_w, 0, self.blame_w, h)
        if self.current_rows:
            a, b = self.current_rows
            ya = self.row_top(a) - off
            yb = self.row_top(b + 1) - off
            p.setPen(QPen(QColor(theme.ACCENT), 1.5))
            p.setBrush(Qt.NoBrush)
            p.drawRect(QRect(self.NUM_W + 1, int(ya), w - self.NUM_W - 3, int(yb - ya - 1)))

    def _paint_row(self, p, r, y, w, hoff, fm, asc, gutter_bg, sel, focused):
        row = self.rows[r]
        nl = self.heights[r] if self.heights is not None and r < len(self.heights) else 1
        rect = QRect(0, int(y), w, nl * self.rh)
        if row.hatched:
            p.fillRect(rect, QColor(theme.SURFACE3))
            p.fillRect(rect, QBrush(QColor(theme.HATCH), Qt.BDiagPattern))
        elif row.bg is not None:
            p.fillRect(rect, row.bg)
        else:
            p.fillRect(QRect(0, int(y), self.NUM_W, nl * self.rh), gutter_bg)
        if row.bar:
            p.fillRect(QRect(0, int(y), 4, nl * self.rh), row.bar)
        li = row.li
        if self.blame_w:
            self._paint_blame(p, y, li)
        if li is not None:
            p.setPen(QColor("#8b91a3"))
            p.drawText(QRect(self.blame_w, int(y), EditorPane.NUM_W - 10, self.rh), Qt.AlignVCenter | Qt.AlignRight, str(li + 1))
        if self.show_sign and row.sign:
            p.setPen(QColor(theme.RED if row.sign == "-" else theme.GREEN))
            f = p.font()
            f.setBold(True)
            p.setFont(f)
            p.drawText(QRect(self.NUM_W, int(y), self.SIGN_W, self.rh), Qt.AlignCenter, row.sign)
            f.setBold(False)
            p.setFont(f)
        if li is None or li >= len(self.lines):
            return
        text = self.lines[li]
        tx = self.text_x()
        cw, rh = self.cw, self.rh
        has_tab = "\t" in text
        disp = text.expandtabs(TAB) if has_tab else text
        dlen = len(disp)
        if has_tab:
            cmap = [0] * (len(text) + 1)
            c = 0
            for i, ch in enumerate(text):
                cmap[i] = c
                c += (TAB - c % TAB) if ch == "\t" else 1
            cmap[len(text)] = c
        else:
            cmap = None

        def dcol(i):
            return cmap[min(i, len(cmap) - 1)] if cmap else i

        wrap = self.wrap
        cols = self.cols() if wrap else 0
        if wrap:
            chunks = [(k * cols, (k + 1) * cols) for k in range(nl)]
        else:
            c0 = max(0, int(hoff / cw) - 2)
            c1 = c0 + int(self.viewport().width() / cw) + 6
            chunks = [(c0, c1)]
        spans = self.span_provider(r) if (self.span_provider and row.kind in ("mod",)) else []
        sel_ranges = []
        if sel:
            (l1, c1_), (l2, c2_) = sel
            if l1 <= li <= l2:
                s_ = dcol(c1_) if li == l1 else 0
                e_ = dcol(c2_) if li == l2 else dlen + 1
                sel_ranges.append((s_, e_))
        find_ranges = [(dcol(a), dcol(b)) for a, b in self.find_matches(li)] if self.find_term else []
        marks = self.marks[li] if (self.marks and li < len(self.marks)) else None
        runs = self._runs(disp, marks, dcol) if marks else [(0, disp, None)]
        cur_k = -1
        if focused and self._cursor_on and li == self.cur_li and not self.readonly:
            cur_d = dcol(self.cur_col)
            cur_k = min(cur_d // cols, nl - 1) if wrap else 0
        else:
            cur_d = 0
        sel_col = QColor(theme.ACCENT)
        sel_col.setAlpha(80)
        find_col = QColor("#f5c542")
        find_col.setAlpha(110)
        for k, (a, b) in enumerate(chunks):
            yk = int(y) + k * rh
            if wrap:
                def xof(dc, a=a):
                    return tx + (dc - a) * cw
            else:
                def xof(dc):
                    return tx + dc * cw - hoff
            for s_, e_, col in spans:
                lo, hi = max(s_, a), min(e_, b)
                if hi > lo:
                    p.fillRect(QRectF(xof(lo), yk + 3, (hi - lo) * cw, rh - 6), col)
            for s_, e_ in find_ranges:
                lo, hi = max(s_, a), min(e_, b)
                if hi > lo:
                    p.fillRect(QRectF(xof(lo), yk + 2, (hi - lo) * cw, rh - 4), find_col)
            for s_, e_ in sel_ranges:
                lo, hi = max(s_, a), min(e_, b)
                if hi > lo:
                    p.fillRect(QRectF(xof(lo), yk + 1, (hi - lo) * cw, rh - 2), sel_col)
            base_y = yk + (rh + asc) // 2
            for rs, rt, rcol in runs:
                re_ = rs + len(rt)
                lo, hi = max(rs, a), min(re_, b)
                if hi <= lo:
                    continue
                p.setPen(rcol if rcol is not None else QColor(theme.TEXT))
                p.drawText(QPointF(xof(lo), base_y), rt[lo - rs:hi - rs])
            if cur_k == k:
                cx = xof(cur_d)
                p.fillRect(QRectF(cx, yk + 3, 1.6, rh - 6), QColor(theme.TEXT))

    @staticmethod
    def _runs(disp, marks, dcol):
        out, pos = [], 0
        for s, e, kind in sorted(marks):
            ds, de = dcol(s), dcol(e)
            if ds < pos or de <= ds:
                continue
            if ds > pos:
                out.append((pos, disp[pos:ds], None))
            out.append((ds, disp[ds:de], syntax_color(kind)))
            pos = de
        if pos < len(disp):
            out.append((pos, disp[pos:], None))
        return out or [(0, disp, None)]


# ====================================================================== gutter
class CopyGutter(QWidget):
    """Centre column with per-hunk copy buttons: left half copies L->R, right half R->L."""
    copyRequested = Signal(int, str)       # hunk, "right" (copy left->right) | "left"

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.source = source
        self.hunk_rows = {}      # hunk -> first row
        self.setFixedWidth(46)
        self.setMouseTracking(True)
        self._hover = None
        source.verticalScrollBar().valueChanged.connect(lambda _: self.update())

    def _hits(self):
        off = self.source.verticalScrollBar().value()
        for h, row in self.hunk_rows.items():
            y = self.source.row_top(row) - off
            if y + self.source.rh < 0 or y > self.height():
                continue
            yield h, y

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.SURFACE2))
        rh = self.source.rh
        for h, y in self._hits():
            for side, x0 in (("right", 3), ("left", self.width() / 2 + 1)):
                hov = self._hover == (h, side)
                r = QRectF(x0, y + 2, self.width() / 2 - 4, rh - 4)
                p.setPen(Qt.NoPen)
                col = QColor(theme.ACCENT if side == "right" else theme.BLUE)
                p.setBrush(col if hov else QColor(theme.HOVER2))
                p.drawRoundedRect(r, 4, 4)
                cx, cy = r.center().x(), r.center().y()
                a = 4.5
                pts = ([QPointF(cx - a * 0.6, cy - a), QPointF(cx + a, cy), QPointF(cx - a * 0.6, cy + a)] if side == "right"
                       else [QPointF(cx + a * 0.6, cy - a), QPointF(cx - a, cy), QPointF(cx + a * 0.6, cy + a)])
                p.setBrush(QColor("#ffffff") if hov else col)
                p.drawPolygon(QPolygonF(pts))

    def _hit(self, pos):
        rh = self.source.rh
        for h, y in self._hits():
            if y <= pos.y() <= y + rh:
                return h, ("right" if pos.x() < self.width() / 2 else "left")
        return None

    def mouseMoveEvent(self, e):
        hit = self._hit(e.position())
        if hit != self._hover:
            self._hover = hit
            self.setToolTip("" if not hit else ("Copy left to right" if hit[1] == "right" else "Copy right to left"))
            self.update()

    def leaveEvent(self, e):
        self._hover = None
        self.update()

    def mousePressEvent(self, e):
        hit = self._hit(e.position())
        if hit and e.button() == Qt.LeftButton:
            self.copyRequested.emit(*hit)


class MiniMap(QWidget):
    """Overview strip: marks are (row, colour). Works with variable row heights."""
    jump = Signal(int)

    def __init__(self, source, parent=None):
        super().__init__(parent)
        self.source = source
        self.marks = []
        self.setFixedWidth(14)
        source.verticalScrollBar().valueChanged.connect(lambda _: self.update())

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.SURFACE2))
        total = max(1, self.source.total_height())
        H = self.height()
        for row, col in self.marks:
            y = int(self.source.row_top(row) / total * H)
            p.fillRect(QRect(2, y, self.width() - 4, 3), QColor(col))
        sb = self.source.verticalScrollBar()
        vh = self.source.viewport().height()
        y0 = int(sb.value() / total * H)
        hh = max(14, int(min(1, vh / total) * H))
        p.setPen(QPen(QColor(theme.ACCENT), 1))
        c = QColor(theme.ACCENT)
        c.setAlpha(30)
        p.setBrush(c)
        p.drawRoundedRect(QRect(1, y0, self.width() - 3, hh), 3, 3)

    def mousePressEvent(self, e):
        total = max(1, self.source.total_height())
        y = e.position().y() / max(1, self.height()) * total
        r = bisect.bisect_right(self.source._ys, y) - 1
        self.jump.emit(max(0, r))
