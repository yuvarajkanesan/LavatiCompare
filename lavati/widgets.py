"""Reusable widgets: painted line viewer, gutter, minimap, segmented control, helpers."""
from PySide6.QtCore import QEvent, QObject, QPointF, QRect, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontMetrics, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import (QAbstractScrollArea, QButtonGroup, QFrame, QHBoxLayout, QLabel, QMenu,
                               QPushButton, QSizePolicy, QToolButton, QWidget)

from . import theme


class MenuStyler(QObject):
    """Gives every popup menu rounded, translucent corners so the stylesheet radius shows."""

    def eventFilter(self, obj, ev):
        if ev.type() == QEvent.Polish and isinstance(obj, QMenu) and not obj.property("lc_styled"):
            obj.setProperty("lc_styled", True)
            obj.setWindowFlag(Qt.FramelessWindowHint, True)
            obj.setAttribute(Qt.WA_TranslucentBackground, True)
        return False


class Row:
    __slots__ = ("num", "text", "bg", "sign", "hatched", "spans", "fg", "outline", "bar", "bold_sign")

    def __init__(self, num="", text="", bg=None, sign="", hatched=False, spans=None, fg=None,
                 outline=False, bar=None):
        self.num = num
        self.text = text.expandtabs(4)
        self.bg = bg
        self.sign = sign
        self.hatched = hatched
        self.spans = spans or []      # [(start, end, QColor)]  background
        self.fg = fg or []            # [(start, end, QColor)]  foreground
        self.outline = outline
        self.bar = bar


class LinesWidget(QAbstractScrollArea):
    rowClicked = Signal(int)
    scrolled = Signal(int)

    NUM_W = 54
    SIGN_W = 26

    def __init__(self, parent=None, show_sign=True):
        super().__init__(parent)
        self.rows = []
        self.show_sign = show_sign
        self.setFont(theme.mono())
        fm = QFontMetrics(self.font())
        self.cw = fm.horizontalAdvance("M")
        self.rh = max(fm.height() + 8, 22)
        self.setFrameShape(QFrame.NoFrame)
        self.viewport().setBackgroundRole(self.backgroundRole())
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.verticalScrollBar().setSingleStep(self.rh)
        self.verticalScrollBar().valueChanged.connect(self.scrolled.emit)
        self.verticalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        self.horizontalScrollBar().valueChanged.connect(lambda _: self.viewport().update())
        self._maxlen = 0
        self.current = -1
        self.empty_text = ""

    def text_x(self):
        return self.NUM_W + (self.SIGN_W if self.show_sign else 0) + 8

    def set_rows(self, rows, keep_scroll=True, maxlen=None):
        v = self.verticalScrollBar().value() if keep_scroll else 0
        self.rows = rows
        self._maxlen = maxlen if maxlen is not None else max((len(r.text) for r in rows), default=0)
        self._update_bars()
        self.verticalScrollBar().setValue(v)
        self.viewport().update()

    def _update_bars(self):
        vp = self.viewport()
        total = len(self.rows) * self.rh
        self.verticalScrollBar().setRange(0, max(0, total - vp.height()))
        self.verticalScrollBar().setPageStep(vp.height())
        content_w = self.text_x() + self._maxlen * self.cw + 24
        self.horizontalScrollBar().setRange(0, max(0, content_w - vp.width()))
        self.horizontalScrollBar().setPageStep(vp.width())

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._update_bars()

    def row_at(self, y):
        i = (y + self.verticalScrollBar().value()) // self.rh
        return int(i) if 0 <= i < len(self.rows) else -1

    def ensure_visible(self, i, center=False):
        sb = self.verticalScrollBar()
        top = i * self.rh
        if center:
            sb.setValue(max(0, top - self.viewport().height() // 3))
        elif top < sb.value():
            sb.setValue(top)
        elif top + self.rh > sb.value() + self.viewport().height():
            sb.setValue(top + self.rh - self.viewport().height())

    def mousePressEvent(self, e):
        i = self.row_at(int(e.position().y()))
        if i >= 0:
            self.rowClicked.emit(i)
        super().mousePressEvent(e)

    def paintEvent(self, ev):
        p = QPainter(self.viewport())
        p.setFont(self.font())
        w = self.viewport().width()
        h = self.viewport().height()
        p.fillRect(0, 0, w, h, QColor(theme.PANEL))
        off = self.verticalScrollBar().value()
        hoff = self.horizontalScrollBar().value()
        first = off // self.rh
        last = min(len(self.rows), (off + h) // self.rh + 2)
        fm = p.fontMetrics()
        tx = self.text_x()
        gutter_bg = QColor(theme.SURFACE2)
        for i in range(first, last):
            r = self.rows[i]
            y = i * self.rh - off
            rect = QRect(0, y, w, self.rh)
            if r.hatched:
                p.fillRect(rect, QColor(theme.SURFACE3))
                p.fillRect(rect, QBrush(QColor(theme.HATCH), Qt.BDiagPattern))
            elif r.bg is not None:
                p.fillRect(rect, r.bg)
            # gutter (numbers)
            if r.bg is None and not r.hatched:
                p.fillRect(QRect(0, y, self.NUM_W, self.rh), gutter_bg)
            p.setPen(QColor("#8b91a3"))
            p.drawText(QRect(0, y, self.NUM_W - 10, self.rh), Qt.AlignVCenter | Qt.AlignRight, r.num)
            if r.bar:
                p.fillRect(QRect(0, y, 4, self.rh), r.bar)
            if self.show_sign and r.sign:
                p.setPen(QColor(theme.RED if r.sign == "-" else theme.GREEN))
                f = p.font()
                f.setBold(True)
                p.setFont(f)
                p.drawText(QRect(self.NUM_W, y, self.SIGN_W, self.rh), Qt.AlignCenter, r.sign)
                f.setBold(False)
                p.setFont(f)
            if r.text:
                x0 = tx - hoff
                base_y = y + (self.rh + fm.ascent() - fm.descent()) // 2
                for a, b, col in r.spans:
                    p.fillRect(QRect(x0 + a * self.cw, y + 3, (b - a) * self.cw, self.rh - 6), col)
                if r.fg:
                    # draw segment by segment with foreground colors
                    pos = 0
                    marks = sorted(r.fg)
                    for a, b, col in marks:
                        if a > pos:
                            p.setPen(QColor(theme.TEXT))
                            p.drawText(x0 + pos * self.cw, base_y, r.text[pos:a])
                        p.setPen(col)
                        p.drawText(x0 + a * self.cw, base_y, r.text[a:b])
                        pos = b
                    if pos < len(r.text):
                        p.setPen(QColor(theme.TEXT))
                        p.drawText(x0 + pos * self.cw, base_y, r.text[pos:])
                else:
                    p.setPen(QColor(theme.TEXT))
                    p.drawText(x0, base_y, r.text)
            if r.outline or i == self.current:
                p.setPen(QPen(QColor(theme.ACCENT), 1.4))
                p.setBrush(Qt.NoBrush)
                p.drawRect(QRect(self.NUM_W + 1, y, w - self.NUM_W - 2, self.rh - 1))
        # separator after number column
        p.setPen(QColor(theme.HOVER))
        p.drawLine(self.NUM_W, 0, self.NUM_W, h)
        if not self.rows and self.empty_text:
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.viewport().rect(), Qt.AlignCenter, self.empty_text)


class Gutter(QWidget):
    """Center column with copy arrows aligned to LinesWidget rows."""
    clicked = Signal(int)

    def __init__(self, source: LinesWidget, parent=None):
        super().__init__(parent)
        self.source = source
        self.marks = {}      # row -> "right" | "left"
        self.setFixedWidth(34)
        self.setCursor(Qt.PointingHandCursor)
        source.scrolled.connect(lambda _: self.update())

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor(theme.SURFACE2))
        off = self.source.verticalScrollBar().value()
        rh = self.source.rh
        for row, d in self.marks.items():
            y = row * rh - off
            if y + rh < 0 or y > self.height():
                continue
            cy = y + rh / 2
            cx = self.width() / 2
            if d == "right":
                pts = [QPointF(cx - 4, cy - 6), QPointF(cx + 5, cy), QPointF(cx - 4, cy + 6)]
                col = QColor(theme.ACCENT)
            else:
                pts = [QPointF(cx + 4, cy - 6), QPointF(cx - 5, cy), QPointF(cx + 4, cy + 6)]
                col = QColor(theme.BLUE)
            p.setPen(Qt.NoPen)
            p.setBrush(col)
            p.drawPolygon(QPolygonF(pts))

    def mousePressEvent(self, e):
        r = self.source.row_at(int(e.position().y()))
        if r >= 0 and r in self.marks:
            self.clicked.emit(r)


class MiniMap(QWidget):
    """Narrow overview strip; marks are (row, color)."""
    jump = Signal(int)

    def __init__(self, source: LinesWidget, parent=None):
        super().__init__(parent)
        self.source = source
        self.marks = []
        self.setFixedWidth(14)
        source.scrolled.connect(lambda _: self.update())

    def paintEvent(self, e):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(theme.SURFACE2))
        n = max(1, len(self.source.rows))
        H = self.height()
        for row, col in self.marks:
            y = int(row / n * H)
            p.fillRect(QRect(2, y, self.width() - 4, 3), QColor(col))
        sb = self.source.verticalScrollBar()
        total = n * self.source.rh
        vh = self.source.viewport().height()
        y0 = int(sb.value() / total * H)
        hh = max(14, int(min(1, vh / total) * H))
        p.setPen(QPen(QColor(theme.ACCENT), 1))
        p.setBrush(QColor(51, 64, 184, 25))
        p.drawRoundedRect(QRect(1, y0, self.width() - 3, hh), 3, 3)

    def mousePressEvent(self, e):
        n = max(1, len(self.source.rows))
        self.jump.emit(int(e.position().y() / self.height() * n))


class Segmented(QFrame):
    changed = Signal(int)

    def __init__(self, labels, current=0, parent=None):
        super().__init__(parent)
        self.setObjectName("seg")
        self.setStyleSheet(
            f"#seg {{ background:{theme.PANEL}; border:1px solid {theme.BORDER}; border-radius:7px; }}"
            f"#seg QPushButton {{ border:none; border-radius:0; padding:7px 14px; background:transparent; }}"
            f"#seg QPushButton:checked {{ background:{theme.ACCENT}; color:{theme.PANEL}; font-weight:600; }}"
            f"#seg QPushButton:hover:!checked {{ background:{theme.HOVER}; }}"
        )
        lay = QHBoxLayout(self)
        lay.setContentsMargins(1, 1, 1, 1)
        lay.setSpacing(0)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        for i, t in enumerate(labels):
            b = QPushButton(t)
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setFlat(True)
            bf = b.font()
            bf.setBold(True)
            b.setMinimumWidth(QFontMetrics(bf).horizontalAdvance(t) + 34)
            self.group.addButton(b, i)
            lay.addWidget(b)
        self.group.button(current).setChecked(True)
        self.group.idClicked.connect(self.changed.emit)

    def index(self):
        return self.group.checkedId()

    def set_current(self, i):
        b = self.group.button(i)
        if b is not None:
            b.setChecked(True)

    def clear(self):
        """Uncheck every segment (used when a custom filter replaces the presets)."""
        self.group.setExclusive(False)
        for b in self.group.buttons():
            b.setChecked(False)
        self.group.setExclusive(True)


class Pill(QLabel):
    def __init__(self, text, fg, bg, parent=None, bold=True):
        super().__init__(text, parent)
        self.setStyleSheet(
            f"background:{bg}; color:{fg}; border-radius:10px; padding:3px 10px; "
            f"font-weight:{'600' if bold else '400'};")
        self.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Fixed)

    def set(self, text, fg=None, bg=None):
        self.setText(text)
        if fg and bg:
            self.setStyleSheet(f"background:{bg}; color:{fg}; border-radius:10px; padding:3px 10px; font-weight:600;")


def toast(text, kind="ok"):
    """Show a non-blocking message on the main window (no-op if none)."""
    from PySide6.QtWidgets import QApplication
    for w in QApplication.topLevelWidgets():
        if hasattr(w, "show_toast"):
            w.show_toast(text, kind)
            return


_uid = [0]


def sty(w, css):
    """Apply css to this widget only (not its children)."""
    if not w.objectName():
        _uid[0] += 1
        w.setObjectName(f"sty{_uid[0]}")
    w.setStyleSheet(f"#{w.objectName()} {{ {css} }}")


def tool_button(text, icon_name, tip=None, checkable=False, icon_only=False):
    b = QToolButton()
    b.setText(text)
    b.setIcon(theme.icon(icon_name, theme.MUTED, 20))
    b.setIconSize(QSize(20, 20))
    b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
    b.setCheckable(checkable)
    b.setAutoRaise(True)
    b.setCursor(Qt.PointingHandCursor)
    b.setMinimumWidth(54)
    b.setFixedHeight(54)                       # every button the same height so icons and labels line up
    if not tip:
        b.setToolTip(text)
    if tip:
        b.setToolTip(tip)
    return b


class ToolbarAdapter(QObject):
    """Shows icon + label on every button while they fit, and switches ALL of them to icon-only together when the
    bar gets too narrow, so the toolbar never looks half-finished."""

    def __init__(self, bar):
        super().__init__(bar)
        self.bar = bar
        self.compact = None
        self._full = None
        bar.installEventFilter(self)

    def buttons(self):
        return [b for b in self.bar.findChildren(QToolButton) if b.parent() is self.bar]

    def eventFilter(self, obj, ev):
        if obj is self.bar and ev.type() in (QEvent.Resize, QEvent.Show):
            self.update()
        return False

    def update(self):
        btns = self.buttons()
        if self._full is None:
            for b in btns:
                b.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            self._full = sum(max(b.sizeHint().width(), 54) for b in btns) + 340     # + segmented control / spin box / gaps
        want = self.bar.width() < self._full
        if want != self.compact:
            self.compact = want
            for b in btns:
                b.setToolButtonStyle(Qt.ToolButtonIconOnly if want else Qt.ToolButtonTextUnderIcon)
                b.setMinimumWidth(40 if want else 54)


def vsep():
    f = QFrame()
    f.setFixedWidth(1)
    f.setStyleSheet(f"background:{theme.BORDER};")
    return f


def card(parent=None, radius=10):
    f = QFrame(parent)
    f.setObjectName("card")
    f.setStyleSheet(f"#card {{ background:{theme.PANEL}; border:1px solid {theme.BORDER}; border-radius:{radius}px; }}")
    return f


def mono_label(text, color=None, size=10):
    l = QLabel(text)
    l.setFont(theme.mono(size))
    if color:
        l.setStyleSheet(f"color:{color};")
    return l
