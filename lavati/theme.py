"""Colors, fonts, stylesheet and SVG icon factory."""
from PySide6.QtCore import QByteArray, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

LIGHT = dict(
    ACCENT="#3340b8", ACCENT_DARK="#28339a", ACCENT_SOFT="#dfe3f8", ACCENT_TEXT="#1c2a85",
    BG="#f0f1f5", SIDEBAR="#f5f6fa", PANEL="#ffffff", SURFACE="#f7f8fb", SURFACE2="#f5f6fa",
    SURFACE3="#f3f4f8", LINE_SOFT="#eef0f5", HOVER="#eceef4", HOVER2="#e2e5ef", BTN="#f6f7fa",
    HATCH="#e1e4ec", BORDER="#dcdfe8", TEXT="#1d2030", MUTED="#5b6074", TABBAR="#e2e5ec",
    SCROLL="#c9cdda", CHECK_BORDER="#aab0c4",
    RED="#c62828", RED_BG="#fde8e8", BLUE="#1a5fd0", BLUE_BG="#e9f0fd", PURPLE="#6a35c9",
    PURPLE_BG="#f0ebfb", GREEN="#1b7a3a", GREEN_BG="#e3f4e8", AMBER="#8a5200", AMBER_BG="#fdf3e1",
    GRAY="#8b91a3", TEAL="#0e7490", CHIP_AMBER="#fbe9c8", CHIP_GRAY="#e8eaf0", CHIP_BLUE="#e3ecfb",
    DIFF_DEL_BG="#fde6e6", DIFF_DEL_WORD="#f6b9b9", DIFF_ADD_BG="#e3f4e8", DIFF_ADD_WORD="#a9deb7",
    OURS_BG="#e9effc", THEIRS_BG="#f0eafb", CONFLICT_BG="#fde6e6", ROW_LEFT="#eef3fd", ROW_RIGHT="#f2edfb",
    ROW_CHANGED="#fff6e4", CELL_NEW="#fbe6bd", CELL_NEW_TEXT="#6b3f00", SELECT_TEXT="#1d2030",
    TIP_BG="#1d2030", TIP_TEXT="#f3f4f8",
)
DARK = dict(
    ACCENT="#6b7bff", ACCENT_DARK="#8794ff", ACCENT_SOFT="#2a3170", ACCENT_TEXT="#c9d0ff",
    BG="#14161d", SIDEBAR="#191c25", PANEL="#1d2029", SURFACE="#1a1d26", SURFACE2="#171a22",
    SURFACE3="#222633", LINE_SOFT="#262a36", HOVER="#272b38", HOVER2="#303548", BTN="#242836",
    HATCH="#2b2f3d", BORDER="#303546", TEXT="#e6e8f0", MUTED="#98a0b8", TABBAR="#0f1117",
    SCROLL="#444a60", CHECK_BORDER="#5b627a",
    RED="#ff7b7b", RED_BG="#3d2024", BLUE="#6ea2ff", BLUE_BG="#1c2a45", PURPLE="#b394ff",
    PURPLE_BG="#2a2145", GREEN="#59d17f", GREEN_BG="#1b3626", AMBER="#f0b45a", AMBER_BG="#3a2e17",
    GRAY="#7c839a", TEAL="#5fd1e6", CHIP_AMBER="#4a3a18", CHIP_GRAY="#2c3040", CHIP_BLUE="#1f3258",
    DIFF_DEL_BG="#3b2025", DIFF_DEL_WORD="#7a2d36", DIFF_ADD_BG="#1b3626", DIFF_ADD_WORD="#2a6b43",
    OURS_BG="#1f2a4a", THEIRS_BG="#2a2147", CONFLICT_BG="#3b2025", ROW_LEFT="#1c2842", ROW_RIGHT="#261f40",
    ROW_CHANGED="#3a2e17", CELL_NEW="#5a4416", CELL_NEW_TEXT="#ffe2a1", SELECT_TEXT="#e6e8f0",
    TIP_BG="#e6e8f0", TIP_TEXT="#14161d",
)
NAME = "light"
globals().update(LIGHT)


def apply(name):
    """Switch palette. Must run before views are created (restart to switch at runtime)."""
    global NAME
    NAME = "dark" if name == "dark" else "light"
    globals().update(DARK if NAME == "dark" else LIGHT)


CODE_SIZE = 10
UI_SIZE = 10


def set_sizes(ui_size=10):
    global CODE_SIZE, UI_SIZE
    UI_SIZE = int(ui_size)
    CODE_SIZE = int(ui_size)


def palette():
    from PySide6.QtGui import QColor as C, QPalette
    p = QPalette()
    for role, col in ((QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, PANEL),
                      (QPalette.AlternateBase, SURFACE), (QPalette.Text, TEXT), (QPalette.Button, BTN),
                      (QPalette.ButtonText, TEXT), (QPalette.ToolTipBase, PANEL), (QPalette.ToolTipText, TEXT),
                      (QPalette.Highlight, ACCENT_SOFT), (QPalette.HighlightedText, TEXT),
                      (QPalette.PlaceholderText, MUTED), (QPalette.Link, ACCENT)):
        p.setColor(role, C(col))
    return p


def is_dark():
    return NAME == "dark"


_FONTS_LOADED = False


def resource(*parts):
    """Path to a bundled resource, in the source tree or inside a PyInstaller bundle."""
    import os
    import sys
    base = getattr(sys, "_MEIPASS", None) or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, *parts)


def load_fonts():
    """Register the bundled fonts in ./fonts (IBM Plex Sans, JetBrains Mono)."""
    global _FONTS_LOADED
    if _FONTS_LOADED:
        return
    _FONTS_LOADED = True
    import glob
    import os
    d = resource("fonts")
    for f in glob.glob(os.path.join(d, "*.ttf")) + glob.glob(os.path.join(d, "*.otf")):
        QFontDatabase.addApplicationFont(f)


def _pick(candidates, fallback):
    load_fonts()
    fams = set(QFontDatabase.families())
    for c in candidates:
        if c in fams:
            return c
    return fallback


_UI = None
_MONO = None


def ui_family():
    global _UI
    if _UI is None:
        _UI = _pick(["IBM Plex Sans", "Segoe UI Variable Text", "Segoe UI"], "Sans Serif")
    return _UI


def mono_family():
    global _MONO
    if _MONO is None:
        _MONO = _pick(["JetBrains Mono", "IBM Plex Mono", "Cascadia Mono", "Consolas", "Courier New"], "Monospace")
    return _MONO


def mono(size=None, bold=False):
    f = QFont(mono_family(), size or CODE_SIZE)
    f.setStyleHint(QFont.Monospace)
    f.setBold(bold)
    return f


def ui(size=None, bold=False):
    f = QFont(ui_family(), size or UI_SIZE)
    f.setBold(bold)
    return f


# ----------------------------------------------------------------- icons
_PATHS = {
    "home": '<path d="M3 11l9-8 9 8"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>',
    "list": '<path d="M4 6h16M4 12h10M4 18h16"/>',
    "rules": '<path d="M4 4h16v5H4zM4 13h9v7H4zM16 13h4v7h-4z"/>',
    "scripts": '<path d="M5 4v7M5 15v5M12 4v3M12 11v9M19 4v9M19 17v3"/><path d="M3 13h4M10 9h4M17 15h4"/>',
    "settings": '<circle cx="12" cy="12" r="3.5"/><path d="M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1"/>',
    "folder": '<path d="M3 6.5A1.5 1.5 0 014.5 5H9l2 2.2h8.5A1.5 1.5 0 0121 8.7V18a1.5 1.5 0 01-1.5 1.5h-15A1.5 1.5 0 013 18z"/>',
    "doc": '<path d="M6 3h8l4 4v14H6z"/><path d="M9 12h6M9 16h6"/>',
    "merge": '<circle cx="6" cy="5" r="2"/><circle cx="18" cy="5" r="2"/><circle cx="12" cy="19" r="2"/><path d="M6 7c0 5 6 5 6 10M18 7c0 5-6 5-6 10"/>',
    "table": '<rect x="3" y="5" width="18" height="14" rx="1.5"/><path d="M3 10h18M3 15h18M9 5v14"/>',
    "hex": '<path d="M4 7h2M9 7h11M4 12h2M9 12h11M4 17h2M9 17h11"/>',
    "image": '<rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="9" cy="10" r="1.5"/><path d="M4 18l5-5 4 4 3-3 4 4"/>',
    "search": '<circle cx="11" cy="11" r="6.5"/><path d="M16 16l5 5"/>',
    "swap": '<path d="M4 8h15M15 4l4 4-4 4M20 16H5M9 12l-4 4 4 4"/>',
    "right": '<path d="M4 12h16M14 6l6 6-6 6"/>',
    "left": '<path d="M20 12H4M10 6l-6 6 6 6"/>',
    "up": '<path d="M12 20V4M6 10l6-6 6 6"/>',
    "down": '<path d="M12 4v16M6 14l6 6 6-6"/>',
    "refresh": '<path d="M20 11a8 8 0 10-2.3 6.3"/><path d="M20 4v7h-7"/>',
    "filter": '<path d="M3 5h18l-7 8v6l-4-2v-4z"/>',
    "expand": '<path d="M4 6h16M4 12h16M4 18h10"/>',
    "report": '<path d="M6 3h8l4 4v14H6z"/><path d="M12 11v6M9 14h6"/>',
    "edit": '<path d="M4 20l1-5L16 4l4 4L9 19z"/>',
    "ws": '<path d="M4 7h16M4 12h10M4 17h16"/>',
    "syntax": '<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 10h6M7 14h10"/>',
    "close": '<path d="M6 6l12 12M18 6L6 18"/>',
    "download": '<path d="M12 4v11M7 11l5 5 5-5M5 20h14"/>',
    "check": '<path d="M5 12l5 5 9-10"/>',
    "warn": '<path d="M12 4l9 16H3z"/><path d="M12 10v4M12 17v.5"/>',
    "save": '<path d="M5 4h12l3 3v13H5z"/><path d="M8 4v5h7V4M8 20v-6h8v6"/>',
    "pin": '<path d="M9 4h6l-1 6 3 3H7l3-3zM12 13v7"/>',
    "play": '<path d="M7 4l13 8-13 8z"/>',
    "trash": '<path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/>',
    "git": '<circle cx="6" cy="5" r="2"/><circle cx="6" cy="19" r="2"/><circle cx="18" cy="9" r="2"/><path d="M6 7v10M18 11c0 4-6 3-12 6"/>',
    "undo": '<path d="M9 14L4 9l5-5"/><path d="M4 9h10a6 6 0 010 12h-3"/>',
    "redo": '<path d="M15 14l5-5-5-5"/><path d="M20 9H10a6 6 0 000 12h3"/>',
    "wrap": '<path d="M4 6h16M4 12h13a3 3 0 010 6h-4"/><path d="M15 15l-2 3 2 3"/><path d="M4 18h5"/>',
    "layout_side": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M12 4v16"/>',
    "layout_over": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M3 12h18"/>',
    "align": '<path d="M4 8h6M14 16h6M10 8l4 8"/><circle cx="10" cy="8" r="1.5"/><circle cx="14" cy="16" r="1.5"/>',
    "patch": '<path d="M7 3h7l4 4v14H7z"/><path d="M10 12h5M12.5 9.5v5M10 17.5h5"/>',
    "plug": '<path d="M9 3v5M15 3v5M7 8h10v4a5 5 0 01-10 0zM12 17v4"/>',
    "cloud": '<path d="M7 18a4 4 0 010-8 5 5 0 019.6-1A4.5 4.5 0 0117 18z"/>',
    "camera": '<path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/>',
    "tag": '<path d="M3 12V4h8l10 10-8 8z"/><circle cx="7.5" cy="8.5" r="1.3"/>',
    "music": '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    "key": '<circle cx="8" cy="15" r="4"/><path d="M11 12l9-9M16 7l3 3"/>',
    "cube": '<path d="M12 3l8 4.5v9L12 21l-8-4.5v-9z"/><path d="M12 12l8-4.5M12 12v9M12 12L4 7.5"/>',
    "archive": '<rect x="3" y="4" width="18" height="5" rx="1"/><path d="M5 9v10h14V9M10 13h4"/>',
    "clock": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>',
    "copy": '<rect x="8" y="8" width="12" height="12" rx="2"/><path d="M16 8V6a2 2 0 00-2-2H6a2 2 0 00-2 2v8a2 2 0 002 2h2"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "lock": '<rect x="5" y="11" width="14" height="9" rx="2"/><path d="M8 11V8a4 4 0 018 0v3"/>',
    "layers": '<path d="M12 3l9 5-9 5-9-5z"/><path d="M3 13l9 5 9-5"/>',
    "select": '<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M8 12l3 3 5-6"/>',
    "terminal": '<rect x="3" y="4" width="18" height="16" rx="2"/><path d="M7 9l3 3-3 3M12 15h5"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.5"/>',
    "move": '<path d="M4 12h12M12 7l5 5-5 5M20 4v16"/>',
    "globe": '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>',
    "more": '<circle cx="5" cy="12" r="1.2"/><circle cx="12" cy="12" r="1.2"/><circle cx="19" cy="12" r="1.2"/>',
    "tool": '<path d="M15 4a4.5 4.5 0 00-3.5 6L4 17.5 6.5 20 14 12.5A4.5 4.5 0 0020 9l-3 2-2-2z"/>',
    "touch": '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/><path d="M19 3l2 2M21 3l-2 2"/>',
    "rename": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13 7l4 4"/>',
    "folder_open": '<path d="M3 18V6.5A1.5 1.5 0 014.5 5H9l2 2.2h8.5A1.5 1.5 0 0121 8.7V10"/><path d="M3 18l3-8h17l-3 8z"/>',
    "stop": '<rect x="6" y="6" width="12" height="12" rx="1.5"/>',
    "script": '<path d="M7 3h10v18l-2-1.5L13 21l-2-1.5L9 21l-2-1.5z"/><path d="M10 8h4M10 12h4"/>',
}


def icon(name, color=TEXT, size=20):
    body = _PATHS[name]
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
        f'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )
    r = QSvgRenderer(QByteArray(svg.encode()))
    scale = 2
    pm = QPixmap(size * scale, size * scale)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    r.render(p)
    p.end()
    pm.setDevicePixelRatio(scale)
    return QIcon(pm)


_STATUS_SHAPES = {
    "changed": ('<path d="M5 9h14M5 15h14M9.5 20L14.5 4"/>', False),
    "same": ('<path d="M5 9h14M5 15h14"/>', False),
    "newer": ('<path d="M12 5l7.5 14h-15z"/>', True),
    "older": ('<path d="M12 19L4.5 5h15z"/>', True),
    "left": ('<path d="M5 12l13-7.5v15z"/>', True),
    "right": ('<path d="M19 12L6 4.5v15z"/>', True),
}
_STATUS_COLORS = {"changed": "RED", "same": "GRAY", "newer": "AMBER", "older": "TEAL", "left": "BLUE", "right": "PURPLE"}
_status_cache = {}


def status_color(status):
    return globals()[_STATUS_COLORS[status]]


def status_icon(status, size=16):
    """Crisp painted marker for a comparison status (no dependence on font glyph coverage)."""
    key = (status, size, NAME)
    if key not in _status_cache:
        body, fill = _STATUS_SHAPES[status]
        col = status_color(status)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
               f'fill="{col if fill else "none"}" stroke="{col}" stroke-width="{1.5 if fill else 2.4}" '
               f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>')
        r = QSvgRenderer(QByteArray(svg.encode()))
        scale = 2
        pm = QPixmap(size * scale, size * scale)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        r.render(p)
        p.end()
        pm.setDevicePixelRatio(scale)
        _status_cache[key] = QIcon(pm)
    return _status_cache[key]


LOGO_PURPLE_1, LOGO_PURPLE_2, LOGO_DARK, LOGO_ORANGE = "#6D28D9", "#4C1D95", "#2A1160", "#FF7A59"


def _chevron_path(x, y, w, h):
    """Open 'V' (the Lavati mark) inside the box x, y, w, h."""
    from PySide6.QtGui import QPainterPath
    path = QPainterPath()
    path.moveTo(x, y)
    path.lineTo(x + w / 2, y + h)
    path.lineTo(x + w, y)
    return path


def logo_pixmap(size):
    """The Lavati mark: purple rounded square, white chevron, orange bar."""
    from PySide6.QtGui import QLinearGradient, QPen
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(0, 0, size, size)
    g.setColorAt(0, QColor(LOGO_PURPLE_1))
    g.setColorAt(1, QColor(LOGO_PURPLE_2))
    p.setPen(Qt.NoPen)
    p.setBrush(g)
    r = size * 0.27
    p.drawRoundedRect(QRectF(0, 0, size, size), r, r)
    pen = QPen(QColor("#ffffff"), size * 0.095)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawPath(_chevron_path(size * 0.255, size * 0.27, size * 0.49, size * 0.30))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(LOGO_ORANGE))
    bar = size * 0.075
    p.drawRoundedRect(QRectF(size * 0.31, size * 0.69, size * 0.38, bar), bar / 2, bar / 2)
    p.end()
    return pm


def wordmark_pixmap(width, tagline=True, dark_text=LOGO_DARK):
    """'lavati' with the chevron as its v, plus the SOFTWARES line."""
    from PySide6.QtGui import QFont, QFontMetricsF, QPen
    h = int(width * (0.42 if tagline else 0.30))
    pm = QPixmap(width, h)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)
    f = QFont(ui_family(), 10)
    f.setWeight(QFont.Bold)
    f.setPixelSize(int(width * 0.26))
    fm = QFontMetricsF(f)
    parts = ["la", None, "ati"]
    vw = fm.horizontalAdvance("v") * 0.92
    total = fm.horizontalAdvance("la") + vw + fm.horizontalAdvance("ati")
    x = (width - total) / 2
    base = width * 0.22
    p.setFont(f)
    p.setPen(QColor(dark_text))
    p.drawText(QPointF(x, base), "la")
    x += fm.horizontalAdvance("la")
    cap = fm.xHeight()
    pen = QPen(QColor(LOGO_PURPLE_1), width * 0.04)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawPath(_chevron_path(x + vw * 0.1, base - cap * 0.95, vw * 0.8, cap * 0.78))
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(LOGO_ORANGE))
    bh = width * 0.028
    p.drawRoundedRect(QRectF(x + vw * 0.22, base + width * 0.012, vw * 0.56, bh), bh / 2, bh / 2)
    x += vw
    p.setPen(QColor(dark_text))
    p.drawText(QPointF(x, base), "ati")
    if tagline:
        f2 = QFont(ui_family(), 10)
        f2.setWeight(QFont.Medium)
        f2.setPixelSize(int(width * 0.055))
        f2.setLetterSpacing(QFont.AbsoluteSpacing, width * 0.022)
        p.setFont(f2)
        p.setPen(QColor("#5B4A8A"))
        p.drawText(QRectF(0, base + width * 0.07, width, width * 0.1), Qt.AlignHCenter | Qt.AlignTop, "SOFTWARES")
    p.end()
    return pm


def app_icon():
    ic = QIcon()
    for sz in (16, 20, 24, 32, 40, 48, 64, 128, 256):
        ic.addPixmap(logo_pixmap(sz))
    return ic


def save_ico(path):
    """Write a multi-size .ico (for shortcuts / PyInstaller)."""
    from PySide6.QtCore import QBuffer, QIODevice
    from PIL import Image
    import io
    buf = QBuffer()
    buf.open(QIODevice.WriteOnly)
    logo_pixmap(256).save(buf, "PNG")
    Image.open(io.BytesIO(bytes(buf.data()))).save(
        path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def chevron_png():
    import os
    d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "LavatiCompare", "assets")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, f"chevron_{NAME}.png")
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="{MUTED}" '
           'stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>')
    r = QSvgRenderer(QByteArray(svg.encode()))
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    pt = QPainter(pm)
    pt.setRenderHint(QPainter.Antialiasing)
    r.render(pt)
    pt.end()
    pm.save(p)
    return p.replace("\\", "/")


def check_png():
    """White check mark on transparent; used for checked checkbox indicators."""
    import os
    d = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "LavatiCompare", "assets")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "check.png")
    r = QSvgRenderer(QByteArray(
        b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="#fff" '
        b'stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>'))
    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    pt = QPainter(pm)
    pt.setRenderHint(QPainter.Antialiasing)
    r.render(pt)
    pt.end()
    pm.save(p)
    return p.replace("\\", "/")


def qss():
    chk = check_png()
    chev = chevron_png()
    return f"""
* {{ color: {TEXT}; }}
QMainWindow, QDialog {{ background: {BG}; }}
QToolTip {{ background: {TIP_BG}; color: {TIP_TEXT}; border: none; border-radius: 6px; padding: 6px 10px; }}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; padding: 6px 10px;
    selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; }}
QLineEdit:focus, QComboBox:focus {{ border: 1px solid {ACCENT}; }}
QLineEdit[mono="true"] {{ font-family: "{mono_family()}"; }}
QComboBox::drop-down {{ border: none; width: 28px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({chev}); width: 12px; height: 12px; }}
QComboBox:hover, QLineEdit:hover {{ border: 1px solid {CHECK_BORDER}; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: {ACCENT_SOFT}; selection-color: {TEXT}; }}
QPushButton {{ background: {BTN}; border: 1px solid {BORDER}; border-radius: 6px; padding: 7px 16px; }}
QPushButton:hover {{ background: {HOVER}; }}
QPushButton:pressed {{ background: {HOVER2}; }}
QPushButton:disabled {{ color: {MUTED}; background: {SURFACE3}; }}
QPushButton[primary="true"] {{ background: {ACCENT}; color: #ffffff; border: 1px solid {ACCENT}; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {ACCENT_DARK}; }}
QPushButton[primary="true"]:disabled {{ background: {ACCENT_SOFT}; border-color: {ACCENT_SOFT}; color: {MUTED}; }}
QPushButton[plain="true"] {{ background: {PANEL}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {CHECK_BORDER}; border-radius: 3px; background: {PANEL}; }}
QCheckBox::indicator:checked {{ background: {BLUE}; border-color: {BLUE}; image: url({chk}); }}
QRadioButton {{ spacing: 8px; }}
QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {CHECK_BORDER}; border-radius: 9px; background: {PANEL}; }}
QRadioButton::indicator:checked {{ border: 1px solid {BLUE}; background: qradialgradient(cx:.5, cy:.5, radius:.5, fx:.5, fy:.5, stop:0 {BLUE}, stop:.5 {BLUE}, stop:.58 {PANEL}, stop:1 {PANEL}); }}
QToolButton {{ background: transparent; border: none; border-radius: 6px; padding: 4px 8px; color: {MUTED}; }}
QToolButton:hover {{ background: {HOVER}; }}
QToolButton:checked {{ background: {ACCENT_SOFT}; }}
QToolButton:disabled {{ color: {MUTED}; }}
QScrollBar:vertical {{ background: transparent; width: 12px; margin: 0; }}
QScrollBar::handle:vertical:hover, QScrollBar::handle:horizontal:hover {{ background: {CHECK_BORDER}; }}
QScrollBar::handle:vertical {{ background: {SCROLL}; border-radius: 4px; min-height: 30px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 12px; }}
QScrollBar::handle:horizontal {{ background: {SCROLL}; border-radius: 4px; min-width: 30px; margin: 2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QTabBar {{ background: {TABBAR}; }}
QTabBar::tab {{ background: transparent; padding: 9px 22px; margin-top: 8px; color: {MUTED};
    border-top-left-radius: 8px; border-top-right-radius: 8px; }}
QTabBar::tab:selected {{ background: {BG}; color: {TEXT}; font-weight: 600; }}
QTabBar::tab:hover:!selected {{ background: {HOVER}; }}
QTabWidget::pane {{ border: none; }}
QHeaderView::section {{ background: {SURFACE}; color: {MUTED}; font-weight: 600; border: none;
    border-bottom: 1px solid {BORDER}; padding: 8px 12px; }}
QTreeWidget, QTableWidget, QTableView {{ background: {PANEL}; border: none; gridline-color: transparent; outline: 0; }}
QMenu {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px; padding: 6px; }}
QMenu::item {{ padding: 8px 42px 8px 16px; margin: 1px 2px; border-radius: 6px; }}
QMenu::item:selected {{ background: {ACCENT_SOFT}; color: {SELECT_TEXT}; }}
QMenu::item:disabled {{ color: {MUTED}; }}
QMenu::right-arrow {{ width: 10px; margin-right: 10px; }}
QSplitter::handle {{ background: {BORDER}; }}
QPlainTextEdit, QTextEdit {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; }}
QListWidget {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 6px; outline: 0; }}
QListWidget::item {{ padding: 8px 10px; }}
QListWidget::item:selected {{ background: {ACCENT_SOFT}; color: {TEXT}; }}
QSlider::groove:horizontal {{ height: 4px; background: {BORDER}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {ACCENT}; width: 16px; margin: -6px 0; border-radius: 8px; }}
QStatusBar {{ background: {TABBAR}; }}
QMenuBar {{ background: {TABBAR}; padding: 3px 8px; border-bottom: 1px solid {BORDER}; }}
QMenuBar::item {{ padding: 6px 14px; margin: 0 1px; border-radius: 6px; background: transparent; }}
QMenuBar::item:pressed {{ background: {ACCENT_SOFT}; }}
QMenuBar::item:selected {{ background: {HOVER}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 6px 10px; }}
QDialog QLabel {{ background: transparent; }}
QMessageBox {{ background: {BG}; }}
QMessageBox QPushButton {{ min-width: 84px; }}
QToolButton::menu-indicator {{ image: none; }}
QPushButton:focus {{ border: 1px solid {ACCENT}; }}
QPushButton[primary="true"]:focus {{ border: 1px solid {ACCENT_DARK}; }}
QTabBar::close-button {{ subcontrol-position: right; }}
QProgressBar {{ background: {HOVER}; border: none; border-radius: 4px; height: 8px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {ACCENT}; border-radius: 4px; }}
"""
