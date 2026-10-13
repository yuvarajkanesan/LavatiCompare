"""Hex (binary) and picture comparison views."""
import mmap
import os
import re
from collections import OrderedDict

from PySide6.QtCore import QPointF, QRectF, QTimer, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (QComboBox, QFrame, QHBoxLayout, QInputDialog, QLabel, QSlider, QSpinBox,
                               QVBoxLayout, QWidget)

from . import engine, theme
from .widgets import ToolbarAdapter, LinesWidget, MiniMap, Row, Segmented, sty, toast, tool_button, vsep
from .workers import TaskWorker

BLOCK = 1 << 16
MAX_DIFF_ROWS = 400_000


# ====================================================================== hex
class Source:
    """Read-only random access to a file (mmap) or an empty placeholder."""

    def __init__(self, path):
        self.path = path
        self.f = self.mm = None
        try:
            self.f = open(path, "rb")
            self.size = os.fstat(self.f.fileno()).st_size
            if self.size:
                self.mm = mmap.mmap(self.f.fileno(), 0, access=mmap.ACCESS_READ)
        except OSError:
            self.size = 0

    def __getitem__(self, key):
        return self.mm[key] if self.mm is not None else b""

    def close(self):
        for h in (self.mm, self.f):
            try:
                if h is not None:
                    h.close()
            except Exception:
                pass


def scan_differences(a, b, bpr, ctx):
    """Row indices that contain differing bytes, plus the number of differing bytes. Fast block-wise XOR."""
    n = max(a.size, b.size)
    rows, count = [], 0
    last_row = -1
    nz = re.compile(rb"[^\x00]")
    for off in range(0, n, BLOCK):
        if ctx.cancel:
            raise engine.Cancelled()
        ba, bb = a[off:off + BLOCK], b[off:off + BLOCK]
        if ba == bb:
            continue
        m = max(len(ba), len(bb))
        # bytes past the end of the shorter file always differ
        xa = ba.ljust(m, b"\x00")
        xb = bb.ljust(m, b"\x00")
        x = (int.from_bytes(xa, "little") ^ int.from_bytes(xb, "little")).to_bytes(m, "little")
        short = min(len(ba), len(bb))
        diff_positions = [mm.start() for mm in nz.finditer(x)]
        if len(ba) != len(bb):
            diff_positions = sorted(set(diff_positions) | set(range(short, m)))
        count += len(diff_positions)
        for p in diff_positions:
            r = (off + p) // bpr
            if r != last_row:
                if len(rows) >= MAX_DIFF_ROWS:
                    return rows, count, True
                rows.append(r)
                last_row = r
    return rows, count, False


class HexRows:
    """Lazy sequence of display rows for one side."""

    def __init__(self, me, other, bpr, is_left):
        self.me, self.other, self.bpr, self.is_left = me, other, bpr, is_left
        self.n = (max(me.size, other.size) + bpr - 1) // bpr
        self.cache = OrderedDict()

    def __len__(self):
        return self.n

    def __getitem__(self, i):
        r = self.cache.get(i)
        if r is not None:
            return r
        bpr = self.bpr
        off = i * bpr
        data, other = self.me[off:off + bpr], self.other[off:off + bpr]
        text, spans = self._row(off, data, other)
        sc = QColor(theme.DIFF_DEL_WORD if self.is_left else theme.DIFF_ADD_WORD)
        r = Row("", text, spans=[(s, e, sc) for s, e in spans], hatched=not data)
        self.cache[i] = r
        if len(self.cache) > 6000:
            self.cache.popitem(last=False)
        return r

    def _row(self, off, data, other):
        bpr = self.bpr
        text = f"{off:08X}  "
        spans = []
        pos = len(text)
        groups = []
        for k in range(bpr):
            if k < len(data):
                if k >= len(other) or other[k] != data[k]:
                    spans.append((pos, pos + 2))
                groups.append(f"{data[k]:02X}")
            else:
                groups.append("  ")
            pos += 2 + 1 + (1 if (k % 8 == 7 and k != bpr - 1) else 0)
        hexs = ""
        for k, g in enumerate(groups):
            hexs += g + " " + (" " if k % 8 == 7 and k != bpr - 1 else "")
        text += hexs + "|"
        a0 = len(text)
        for k in range(len(data)):
            b = data[k]
            text += chr(b) if 32 <= b < 127 else "."
            if k >= len(other) or other[k] != b:
                spans.append((a0 + k, a0 + k + 1))
        text += "|"
        return text, spans


class HexCompareView(QWidget):
    resultChanged = Signal(str)

    def __init__(self, cfg, left, right, parent=None):
        super().__init__(parent)
        self.left, self.right = left, right
        self.bpr = 16
        self.diff_rows = []
        self.cur = -1
        self.A = self.B = None
        self.worker = None
        self.ndiff = 0
        self._ui()
        self.load()

    def _ui(self):
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QFrame()
        sty(bar, f"background:{theme.SURFACE}; border-bottom:1px solid {theme.BORDER};")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 6, 16, 6)
        bl.setSpacing(3)
        p, n = tool_button("Prev", "up", "Previous difference (F7)"), tool_button("Next", "down", "Next difference (F8)")
        p.clicked.connect(lambda: self.step(-1))
        n.clicked.connect(lambda: self.step(1))
        g = tool_button("Go to", "right", "Go to offset (Ctrl+G)")
        g.clicked.connect(self.goto_line)
        f = tool_button("Find", "search", "Find bytes or text (Ctrl+F)")
        f.clicked.connect(self.find)
        for b in (p, n, g, f):
            bl.addWidget(b)
        bl.addWidget(vsep())
        bl.addWidget(QLabel("Bytes per row"))
        self.bpr_combo = QComboBox()
        for v in (8, 16, 24, 32):
            self.bpr_combo.addItem(str(v), v)
        self.bpr_combo.setCurrentIndex(1)
        self.bpr_combo.currentIndexChanged.connect(self._bpr_changed)
        bl.addWidget(self.bpr_combo)
        bl.addStretch(1)
        self.stats = QLabel()
        self.stats.setTextFormat(Qt.RichText)
        bl.addWidget(self.stats)
        ToolbarAdapter(bar)
        root.addWidget(bar)
        hdr = QFrame()
        sty(hdr, f"background:{theme.PANEL}; border-bottom:1px solid {theme.BORDER};")
        hl = QHBoxLayout(hdr)
        hl.setContentsMargins(16, 8, 0, 8)
        self.lh, self.rh = QLabel(self.left), QLabel(self.right)
        for l in (self.lh, self.rh):
            l.setFont(theme.mono(10))
            l.setToolTip(l.text())
        hl.addWidget(self.lh, 1)
        hl.addSpacing(50)
        hl.addWidget(self.rh, 1)
        root.addWidget(hdr)
        body = QHBoxLayout()
        body.setSpacing(0)
        self.lv, self.rv = LinesWidget(show_sign=False), LinesWidget(show_sign=False)
        for v in (self.lv, self.rv):
            v.NUM_W = 8
            v.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.lv.verticalScrollBar().valueChanged.connect(self.rv.verticalScrollBar().setValue)
        self.rv.verticalScrollBar().valueChanged.connect(self.lv.verticalScrollBar().setValue)
        self.lv.horizontalScrollBar().valueChanged.connect(self.rv.horizontalScrollBar().setValue)
        self.rv.horizontalScrollBar().valueChanged.connect(self.lv.horizontalScrollBar().setValue)
        self.mm = MiniMap(self.rv)
        self.mm.jump.connect(lambda r: (self.lv.ensure_visible(r, True), self.rv.ensure_visible(r, True)))
        sep = QFrame()
        sep.setFixedWidth(10)
        sep.setStyleSheet(f"background:{theme.SURFACE2};")
        body.addWidget(self.lv, 1)
        body.addWidget(sep)
        body.addWidget(self.rv, 1)
        body.addWidget(self.mm)
        root.addLayout(body, 1)
        self.status = QLabel()
        self.status.setStyleSheet(f"color:{theme.MUTED}; font-size:9pt; padding:4px 16px; background:{theme.TABBAR};")
        root.addWidget(self.status)

    def _bpr_changed(self):
        self.bpr = self.bpr_combo.currentData()
        self.load()

    def load(self):
        self.cancel_work()
        for s in (self.A, self.B):
            if s is not None:
                s.close()
        self.A, self.B = Source(self.left), Source(self.right)
        self.diff_rows, self.cur, self.ndiff = [], -1, 0
        a, b, bpr = self.A, self.B, self.bpr
        self.lv.set_rows(HexRows(a, b, bpr, True), maxlen=10 + 3 * bpr + 2 + bpr + 4)
        self.rv.set_rows(HexRows(b, a, bpr, False), maxlen=10 + 3 * bpr + 2 + bpr + 4)
        self.stats.setText(f"<span style='color:{theme.MUTED}'>Comparing\u2026</span>")
        self.status.setText(f"L {a.size:,} B \u00b7 R {b.size:,} B")
        w = TaskWorker(lambda ctx: scan_differences(a, b, bpr, ctx), parent=self)
        w.done.connect(self._scanned)
        w.failed.connect(lambda e: self.stats.setText(f"<span style='color:{theme.RED}'>{e}</span>"))
        self.worker = w
        w.start()

    def reload(self):
        self.load()

    def cancel_work(self):
        if self.worker is not None and self.worker.isRunning():
            self.worker.cancel()
            self.worker.wait(5000)

    def _scanned(self, res):
        rows, count, truncated = res
        self.diff_rows, self.ndiff = rows, count
        self.mm.marks = [(r, theme.RED) for r in rows[:20000]]
        self.mm.update()
        sizes = f"L {self.A.size:,} B \u00b7 R {self.B.size:,} B"
        extra = "  (only the first differences are listed)" if truncated else ""
        col = theme.RED if count else theme.GREEN
        self.stats.setText(f"<b style='color:{col}'>{count:,} bytes differ</b> &nbsp; "
                           f"<span style='color:{theme.MUTED}'>{len(rows):,} rows{extra}</span>")
        self.status.setText(sizes + ("   \u00b7   sizes differ" if self.A.size != self.B.size else ""))
        self.resultChanged.emit(f"{count} bytes" if count else "identical")
        if rows:
            self.step(1)

    def _show_row(self, r):
        self.cur = r
        self.lv.current = self.rv.current = r
        self.lv.ensure_visible(r, True)
        self.rv.ensure_visible(r, True)
        self.lv.viewport().update()
        self.rv.viewport().update()
        self.status.setText(f"Offset 0x{r * self.bpr:08X} ({r * self.bpr:,})   \u00b7   L {self.A.size:,} B \u00b7 R {self.B.size:,} B")

    def step(self, d):
        if not self.diff_rows:
            return
        import bisect
        if d > 0:
            i = bisect.bisect_right(self.diff_rows, self.cur)
            r = self.diff_rows[i] if i < len(self.diff_rows) else self.diff_rows[0]
        else:
            i = bisect.bisect_left(self.diff_rows, self.cur) - 1
            r = self.diff_rows[i] if i >= 0 else self.diff_rows[-1]
        self._show_row(r)

    def goto_line(self):
        text, ok = QInputDialog.getText(self, "Go to offset", "Offset (decimal, or hex like 0x1F40):")
        if not ok or not text.strip():
            return
        try:
            off = int(text.strip(), 16 if text.strip().lower().startswith("0x") else 10)
        except ValueError:
            toast("That is not a number", "warn")
            return
        if off < 0 or off >= max(self.A.size, self.B.size):
            toast("Offset is past the end of both files", "warn")
            return
        self._show_row(off // self.bpr)

    def find(self):
        text, ok = QInputDialog.getText(self, "Find", "Hex bytes (e.g. DE AD BE EF) or text prefixed with \"t:\" (e.g. t:hello):")
        if not ok or not text.strip():
            return
        t = text.strip()
        try:
            needle = t[2:].encode("utf-8") if t.lower().startswith("t:") else bytes.fromhex(t.replace("0x", "").replace(" ", ""))
        except ValueError:
            toast("Enter an even number of hex digits", "warn")
            return
        if not needle:
            return
        start = (self.cur + 1) * self.bpr if self.cur >= 0 else 0
        best = None
        for src in (self.A, self.B):
            if src.mm is None:
                continue
            i = src.mm.find(needle, start)
            if i < 0 and start:
                i = src.mm.find(needle, 0)
            if i >= 0 and (best is None or i < best):
                best = i
        if best is None:
            toast("Not found", "warn")
            return
        self._show_row(best // self.bpr)

    def has_unsaved(self):
        return False

    def swap_sides(self):
        self.left, self.right = self.right, self.left
        self.lh.setText(self.left)
        self.rh.setText(self.right)
        self.load()


# ===================================================================== image
class ImageCanvas(QWidget):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.a = self.b = None
        self.diff = None
        self.mode = 0
        self.t = 50
        self.zoom = None             # None = fit
        self.pan = QPointF(0, 0)
        self._drag = None
        self._blink_b = False
        self.setMinimumHeight(300)
        self.setMouseTracking(True)

    def set_images(self, a, b, diff):
        self.a, self.b, self.diff = a, b, diff
        self.update()

    def _fit(self, img, rect):
        if img is None or img.isNull():
            return QRectF()
        if self.zoom is None:
            s = min(rect.width() / img.width(), rect.height() / img.height(), 4.0)
            w, h = img.width() * s, img.height() * s
            return QRectF(rect.center().x() - w / 2, rect.center().y() - h / 2, w, h)
        w, h = img.width() * self.zoom, img.height() * self.zoom
        return QRectF(rect.center().x() - w / 2 + self.pan.x(), rect.center().y() - h / 2 + self.pan.y(), w, h)

    def _checker(self, p, r):
        p.save()
        p.setClipRect(r)
        size = 12
        for y in range(int(r.top()), int(r.bottom()) + size, size):
            for x in range(int(r.left()), int(r.right()) + size, size):
                c = theme.PANEL if ((x - int(r.left())) // size + (y - int(r.top())) // size) % 2 == 0 else theme.HOVER
                p.fillRect(x, y, size, size, QColor(c))
        p.restore()

    def current_scale(self):
        if self.a is None or self.a.isNull():
            return 1.0
        area = QRectF(self.rect()).adjusted(16, 16, -16, -16)
        r = self._fit(self.a, area)
        return r.width() / self.a.width() if self.a.width() else 1.0

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.SmoothPixmapTransform, self.zoom is None or self.zoom < 1)
        p.fillRect(self.rect(), QColor(theme.BG))
        if self.a is None or self.a.isNull() or self.b is None or self.b.isNull():
            p.setPen(QColor(theme.MUTED))
            p.drawText(self.rect(), Qt.AlignCenter, "Could not load one or both images")
            return
        area = QRectF(self.rect()).adjusted(16, 16, -16, -16)
        if self.mode == 0:
            half = (area.width() - 16) / 2
            for img, x, label in ((self.a, area.left(), "LEFT"), (self.b, area.left() + half + 16, "RIGHT")):
                box = QRectF(x, area.top() + 22, half, area.height() - 22)
                p.setPen(QColor(theme.MUTED))
                p.drawText(QRectF(x, area.top(), half, 20), Qt.AlignLeft, label)
                p.save()
                p.setClipRect(box)
                r = self._fit(img, box)
                self._checker(p, r)
                p.drawImage(r, img)
                p.restore()
            return
        r = self._fit(self.a, area)
        self._checker(p, r)
        p.save()
        p.setClipRect(area)
        if self.mode == 1:        # overlay
            p.drawImage(r, self.a)
            p.setOpacity(self.t / 100)
            p.drawImage(r, self.b)
        elif self.mode == 2:      # slider
            p.drawImage(r, self.b)
            x = r.left() + r.width() * self.t / 100
            p.save()
            p.setClipRect(QRectF(r.left(), r.top(), max(0, x - r.left()), r.height()))
            p.drawImage(r, self.a)
            p.restore()
            p.setPen(QPen(QColor(theme.ACCENT), 2))
            p.drawLine(QPointF(x, r.top()), QPointF(x, r.bottom()))
        elif self.mode == 3:      # blink
            p.drawImage(r, self.b if self._blink_b else self.a)
            p.setPen(QColor(theme.ACCENT))
            p.drawText(QRectF(area.left(), area.top(), 200, 18), Qt.AlignLeft, "RIGHT" if self._blink_b else "LEFT")
        else:                     # difference
            p.fillRect(r, QColor("#101218"))
            if self.diff is not None:
                p.drawImage(r, self.diff)
        p.restore()

    # zoom / pan
    def wheelEvent(self, e):
        if self.mode == 0:
            return
        base = self.zoom if self.zoom is not None else self.current_scale()
        factor = 1.25 if e.angleDelta().y() > 0 else 0.8
        self.zoom = max(0.05, min(32.0, base * factor))
        self.update()
        self.changed.emit()

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self._drag = e.position()
            if self.mode == 2:
                self._slide(e.position())

    def _slide(self, pos):
        area = QRectF(self.rect()).adjusted(16, 16, -16, -16)
        r = self._fit(self.a, area)
        if r.width() > 0:
            self.t = max(0, min(100, (pos.x() - r.left()) / r.width() * 100))
            self.update()
            self.changed.emit()

    def mouseMoveEvent(self, e):
        if self._drag is not None and (e.buttons() & Qt.LeftButton):
            if self.mode == 2 and self.zoom is None:
                self._slide(e.position())
            elif self.zoom is not None:
                d = e.position() - self._drag
                self.pan += d
                self._drag = e.position()
                self.update()

    def mouseReleaseEvent(self, e):
        self._drag = None

    def mouseDoubleClickEvent(self, e):
        self.zoom, self.pan = None, QPointF(0, 0)
        self.update()
        self.changed.emit()


def _pil():
    try:
        from PIL import Image, ImageChops
        return Image, ImageChops
    except Exception:
        return None, None


class ImageCompareView(QWidget):
    resultChanged = Signal(str)

    def __init__(self, cfg, left, right, parent=None):
        super().__init__(parent)
        self.paths = (left, right)
        self.A = self.B = None
        self.tol = 0
        self.style = 0
        self.fit_mode = 0
        self.stats_txt = ""
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        bar = QFrame()
        sty(bar, f"background:{theme.SURFACE}; border-bottom:1px solid {theme.BORDER};")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(12, 6, 16, 6)
        bl.setSpacing(6)
        self.seg = Segmented(["Side by side", "Overlay", "Slider", "Blink", "Difference"])
        bl.addWidget(self.seg)
        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(50)
        self.slider.setFixedWidth(170)
        self.slider.setEnabled(False)
        self.slider.setToolTip("Overlay opacity / slider position")
        bl.addWidget(self.slider)
        bl.addWidget(vsep())
        b_fit = tool_button("Fit", "expand", "Fit to window (double-click)")
        b_100 = tool_button("100%", "image", "Actual size")
        b_fit.clicked.connect(lambda: self._set_zoom(None))
        b_100.clicked.connect(lambda: self._set_zoom(1.0))
        bl.addWidget(b_fit)
        bl.addWidget(b_100)
        bl.addStretch(1)
        self.info = QLabel()
        self.info.setTextFormat(Qt.RichText)
        bl.addWidget(self.info)
        ToolbarAdapter(bar)
        root.addWidget(bar)
        bar2 = QFrame()
        sty(bar2, f"background:{theme.SURFACE}; border-bottom:1px solid {theme.BORDER};")
        b2 = QHBoxLayout(bar2)
        b2.setContentsMargins(12, 4, 16, 4)
        b2.setSpacing(8)
        b2.addWidget(QLabel("Tolerance"))
        self.tol_spin = QSpinBox()
        self.tol_spin.setRange(0, 255)
        self.tol_spin.setButtonSymbols(QSpinBox.NoButtons)
        self.tol_spin.setFixedWidth(70)
        self.tol_spin.setToolTip("Per-channel difference (0-255) that still counts as the same pixel")
        b2.addWidget(self.tol_spin)
        self.style_combo = QComboBox()
        self.style_combo.addItems(["Highlight on left", "Mask (white = different)", "Amplified difference"])
        self.style_combo.setToolTip("How the Difference mode draws changed pixels")
        b2.addWidget(self.style_combo)
        self.size_combo = QComboBox()
        self.size_combo.addItems(["Different sizes: scale right", "Different sizes: align top-left"])
        b2.addWidget(self.size_combo)
        b2.addStretch(1)
        ToolbarAdapter(bar2)
        root.addWidget(bar2)
        self.canvas = ImageCanvas()
        root.addWidget(self.canvas, 1)
        self.note = QLabel()
        self.note.setStyleSheet(f"color:{theme.MUTED}; font-size:9pt; padding:4px 16px; background:{theme.TABBAR};")
        root.addWidget(self.note)
        self.seg.changed.connect(self._mode)
        self.slider.valueChanged.connect(self._t)
        self.canvas.changed.connect(self._canvas_changed)
        self._deb = QTimer(self)
        self._deb.setSingleShot(True)
        self._deb.setInterval(250)
        self._deb.timeout.connect(self._recompute)
        self.tol_spin.valueChanged.connect(lambda v: (setattr(self, "tol", v), self._deb.start()))
        self.style_combo.currentIndexChanged.connect(lambda i: (setattr(self, "style", i), self._deb.start()))
        self.size_combo.currentIndexChanged.connect(lambda i: (setattr(self, "fit_mode", i), self._deb.start()))
        self._blink = QTimer(self)
        self._blink.setInterval(550)
        self._blink.timeout.connect(self._tick)
        self._load()

    def _tick(self):
        self.canvas._blink_b = not self.canvas._blink_b
        self.canvas.update()

    def _mode(self, i):
        self.canvas.mode = i
        self.slider.setEnabled(i in (1, 2))
        self.style_combo.setEnabled(i == 4)
        if i == 3:
            self._blink.start()
        else:
            self._blink.stop()
        self.canvas.update()

    def _t(self, v):
        self.canvas.t = v
        self.canvas.update()

    def _canvas_changed(self):
        self.slider.blockSignals(True)
        self.slider.setValue(int(self.canvas.t))
        self.slider.blockSignals(False)
        z = self.canvas.zoom
        self.note.setText(self.stats_txt + (f"   \u00b7   zoom {z * 100:.0f}%" if z else ""))

    def _set_zoom(self, z):
        self.canvas.zoom = z
        self.canvas.pan = QPointF(0, 0)
        self.canvas.update()
        self._canvas_changed()

    def reload(self):
        self._load()

    def _load(self):
        left, right = self.paths
        self.A, self.B = QImage(left), QImage(right)
        self._recompute()

    def _recompute(self):
        a, b = self.A, self.B
        diff, pct, mean, differing = None, None, None, 0
        Image, ImageChops = _pil()
        total = 0
        if a.isNull() or b.isNull():
            self.canvas.set_images(a, b, None)
            self.info.setText(f"<span style='color:{theme.RED}'>Could not load one or both images</span>")
            return
        try:
            if Image is not None:
                A = Image.open(self.paths[0]).convert("RGBA")
                B = Image.open(self.paths[1]).convert("RGBA")
                if B.size != A.size:
                    if self.fit_mode == 0:
                        B = B.resize(A.size)
                    else:
                        canvas = Image.new("RGBA", A.size, (0, 0, 0, 0))
                        canvas.paste(B, (0, 0))
                        B = canvas
                total = A.size[0] * A.size[1]
                D = ImageChops.difference(A, B)
                r, g, bb, al = D.split()
                mx = ImageChops.lighter(ImageChops.lighter(r, g), ImageChops.lighter(bb, al))
                hist = mx.histogram()
                tol = self.tol
                differing = sum(hist[tol + 1:])
                pct = differing / max(1, total) * 100
                mean = sum(i * h for i, h in enumerate(hist)) / max(1, total)
                mask = mx.point([0] * (tol + 1) + [255] * (255 - tol))
                if self.style == 0:
                    red = Image.new("RGBA", A.size, (230, 40, 60, 255))
                    base = A.copy()
                    base.putalpha(110)
                    out = Image.composite(red, base, mask)
                elif self.style == 1:
                    out = Image.merge("RGBA", (mask, mask, mask, Image.new("L", A.size, 255)))
                else:
                    amp = mx.point([min(255, i * 8) for i in range(256)])
                    out = Image.merge("RGBA", (amp, Image.new("L", A.size, 0), Image.new("L", A.size, 0), Image.new("L", A.size, 255)))
                data = out.tobytes()
                diff = QImage(data, A.size[0], A.size[1], A.size[0] * 4, QImage.Format_RGBA8888).copy()
                if self.fit_mode == 0 and a.size() != b.size():
                    pass
            else:
                diff, pct, mean, total = self._qt_diff(a, b)
        except Exception as e:
            self.note.setText(f"Difference could not be computed: {e}")
        self.canvas.set_images(a, b, diff)
        sz = lambda i: f"{i.width()}\u00d7{i.height()}" if not i.isNull() else "?"
        txt = f"<span style='color:{theme.MUTED}'>L {sz(a)} \u00b7 R {sz(b)}</span>"
        if pct is not None:
            col = theme.GREEN if pct == 0 else theme.RED
            txt += f" &nbsp; <b style='color:{col}'>{pct:.2f}% pixels differ</b>"
            self.resultChanged.emit("identical" if pct == 0 else f"{pct:.1f}% diff")
        self.info.setText(txt)
        sizes = [os.path.getsize(p) if os.path.exists(p) else 0 for p in self.paths]
        self.stats_txt = (f"{os.path.basename(self.paths[0])} {sizes[0]:,} B  \u00b7  {os.path.basename(self.paths[1])} {sizes[1]:,} B"
                          + (f"  \u00b7  {differing:,} of {total:,} pixels differ (tolerance {self.tol})" if pct is not None else "")
                          + (f"  \u00b7  mean difference {mean:.2f}/255" if mean is not None else "")
                          + ("  \u00b7  sizes differ" if a.size() != b.size() else ""))
        self.note.setText(self.stats_txt)

    def _qt_diff(self, a, b):
        """Slow fallback when Pillow is not installed (only for modest image sizes)."""
        a = a.convertToFormat(QImage.Format_ARGB32)
        b = b.convertToFormat(QImage.Format_ARGB32)
        if (a.width() * a.height()) > 1_500_000:
            raise RuntimeError("install Pillow (pip install pillow) to compare large images")
        if b.size() != a.size():
            b = b.scaled(a.size())
        out = QImage(a.size(), QImage.Format_ARGB32)
        out.fill(QColor("#101218"))
        diff = 0
        tol = self.tol
        for y in range(a.height()):
            for x in range(a.width()):
                pa, pb = a.pixel(x, y), b.pixel(x, y)
                if max(abs(((pa >> s) & 255) - ((pb >> s) & 255)) for s in (0, 8, 16, 24)) > tol:
                    diff += 1
                    out.setPixel(x, y, 0xFFE62840)
        total = a.width() * a.height()
        return out, diff / max(1, total) * 100, None, total

    def has_unsaved(self):
        return False

    def swap_sides(self):
        self.paths = (self.paths[1], self.paths[0])
        self._load()
