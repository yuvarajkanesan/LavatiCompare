"""Main window: toolbar, menus, tabs, toasts, drag & drop and routing between session views."""
import os

from PySide6.QtCore import QPropertyAnimation, QSize, Qt, QTimer
from PySide6.QtGui import QAction, QActionGroup, QColor, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (QApplication, QDialog, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QMainWindow,
                               QMenu, QMessageBox, QPushButton, QSizePolicy, QTabBar, QTabWidget, QToolBar, QToolButton,
                               QVBoxLayout, QWidget)

from . import __version__, config, connect_dialog, gitvfs, remote, theme, vfs
from .folder_view import FolderCompareView
from .git_view import GitCompareDialog
from .home import KINDS, HomePage, PickDialog, detect, session_name
from .media_views import HexCompareView, ImageCompareView
from .merge_view import FolderMergeView, MergeView
from .special_views import MediaCompareView, RegistryCompareView, VersionCompareView
from .table_view import TableCompareView
from .text_view import TextCompareView

SHORTCUTS = [
    ("General", [("Ctrl+N", "New comparison"), ("Ctrl+O", "Open two files / folders…"), ("Ctrl+K", "Search sessions"),
                 ("Ctrl+W", "Close tab"), ("Ctrl+Tab / Ctrl+Shift+Tab", "Next / previous tab"), ("Ctrl+1 … 9", "Go to tab"),
                 ("F11", "Full screen"), ("Ctrl+Q", "Quit")]),
    ("Navigation", [("F7 / F8", "Previous / next difference"), ("Alt+Up / Alt+Down", "Previous / next difference (text)"),
                    ("F5", "Refresh / re-compare"), ("Ctrl+F, F3, Shift+F3", "Find, find next, find previous"), ("Ctrl+G", "Go to line / offset")]),
    ("Editing (Text Compare)", [("Ctrl+Z / Ctrl+Y", "Undo / redo in the active pane"), ("Ctrl+S", "Save changed files"),
                                ("Alt+Right / Alt+Left", "Copy difference to the right / left"), ("Ctrl+Shift+X", "Swap left and right")]),
    ("Folders", [("Enter / double-click", "Open the selected pair"), ("Ctrl+Right / Ctrl+Left", "Copy selection across"),
                 ("F2", "Rename"), ("Delete", "Delete…"), ("Ctrl+Shift+Y", "Synchronize…"), ("Right-click", "All file actions")]),
]


class Toast(QLabel):
    """Small non-blocking message that fades out."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.fx = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.fx)
        self.anim = QPropertyAnimation(self.fx, b"opacity", self)
        self.anim.setDuration(350)
        self.timer = QTimer(self, singleShot=True)
        self.timer.timeout.connect(self._fade)
        self.anim.finished.connect(lambda: self.hide() if self.fx.opacity() < 0.05 else None)
        self.hide()

    def show_message(self, text, kind="ok"):
        col = {"ok": theme.GREEN, "warn": theme.AMBER, "error": theme.RED}.get(kind, theme.ACCENT)
        glyph = {"ok": "✓", "warn": "!", "error": "✕"}.get(kind, "i")
        text = str(text).replace("<", "&lt;").replace("\n", "<br>")
        self.setText(f"<span style='color:{col}; font-weight:700'>{glyph}</span>&nbsp;&nbsp;{text}")
        self.setTextFormat(Qt.RichText)
        self.setStyleSheet(f"background:{theme.PANEL}; color:{theme.TEXT}; border:1px solid {theme.BORDER};"
                           f"border-left:4px solid {col}; border-radius:8px; padding:12px 20px; font-size:11pt;")
        self.setMaximumWidth(max(300, self.parentWidget().width() - 80))
        self.setWordWrap(True)
        self.adjustSize()
        p = self.parentWidget()
        self.move((p.width() - self.width()) // 2, p.height() - self.height() - 46)
        self.raise_()
        self.anim.stop()
        self.fx.setOpacity(1.0)
        self.show()
        self.timer.start(3600 if kind in ("warn", "error") else 2600)

    def _fade(self):
        self.anim.setStartValue(1.0)
        self.anim.setEndValue(0.0)
        self.anim.start()


class DropOverlay(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.hide()

    def paintEvent(self, e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = QColor(theme.ACCENT)
        c.setAlpha(34)
        p.fillRect(self.rect(), c)
        r = self.rect().adjusted(28, 28, -28, -28)
        pen = QPen(QColor(theme.ACCENT), 2.5, Qt.DashLine)
        p.setPen(pen)
        p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(r, 18, 18)
        f = p.font()
        f.setPointSize(20)
        f.setBold(True)
        p.setFont(f)
        p.setPen(QColor(theme.ACCENT))
        p.drawText(r.adjusted(0, -20, 0, -20), Qt.AlignCenter, "Drop two files or folders to compare")
        f.setPointSize(11)
        f.setBold(False)
        p.setFont(f)
        p.setPen(QColor(theme.MUTED))
        p.drawText(r.adjusted(0, 50, 0, 50), Qt.AlignCenter, "Three items open a merge · release to start")


def license_text():
    """The MIT text shipped next to the program (or in the source tree)."""
    import sys
    base = os.path.dirname(sys.executable) if getattr(sys, "frozen", False) else os.path.dirname(os.path.dirname(__file__))
    try:
        with open(os.path.join(base, "LICENSE.txt"), encoding="utf-8") as f:
            return f.read()
    except OSError:
        return "MIT License. Copyright (c) 2026 Lavati Softwares.\nSee https://opensource.org/licenses/MIT"


class LicenseDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("License")
        self.resize(560, 440)
        lay = QVBoxLayout(self)
        from PySide6.QtWidgets import QPlainTextEdit
        t = QPlainTextEdit(license_text())
        t.setReadOnly(True)
        lay.addWidget(t)
        ok = QPushButton("Close")
        ok.setProperty("primary", True)
        ok.clicked.connect(self.accept)
        lay.addWidget(ok)


class AboutDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("About Lavati Compare")
        self.setFixedSize(460, 380)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(32, 30, 32, 24)
        lay.setAlignment(Qt.AlignHCenter)
        logo = QLabel()
        pm = theme.logo_pixmap(192)
        pm.setDevicePixelRatio(2)
        logo.setPixmap(pm)
        logo.setAlignment(Qt.AlignCenter)
        lay.addWidget(logo)
        t = QLabel("Lavati Compare")
        t.setAlignment(Qt.AlignCenter)
        t.setStyleSheet("font-size:20pt; font-weight:700;")
        v = QLabel(f"Version {__version__} · Pro")
        v.setAlignment(Qt.AlignCenter)
        v.setStyleSheet(f"color:{theme.MUTED};")
        d = QLabel("Folder, text, table, binary, picture, media, version and registry\ncomparison with merge, synchronisation and scripting.")
        d.setAlignment(Qt.AlignCenter)
        d.setStyleSheet(f"color:{theme.MUTED};")
        c = QLabel("Copyright (c) 2026 Lavati Softwares. Free and open source (MIT License).\nBuilt with Python and Qt (PySide6).")
        c.setAlignment(Qt.AlignCenter)
        c.setStyleSheet(f"color:{theme.MUTED}; font-size:9pt;")
        for w in (t, v):
            lay.addWidget(w)
        lay.addSpacing(10)
        lay.addWidget(d)
        lay.addStretch(1)
        lay.addWidget(c)
        row = QHBoxLayout()
        lic = QPushButton("License (MIT)")
        lic.setMinimumHeight(36)
        lic.clicked.connect(lambda: LicenseDialog(self).exec())
        ok = QPushButton("Close")
        ok.setProperty("primary", True)
        ok.setMinimumHeight(36)
        ok.clicked.connect(self.accept)
        row.addWidget(lic)
        row.addWidget(ok)
        lay.addSpacing(10)
        lay.addLayout(row)


class ShortcutsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Keyboard shortcuts")
        self.resize(560, 640)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(28, 24, 28, 20)
        for title, rows in SHORTCUTS:
            h = QLabel(title.upper())
            h.setStyleSheet(f"color:{theme.MUTED}; font-weight:700; font-size:9pt; letter-spacing:1px; margin-top:8px;")
            lay.addWidget(h)
            for key, desc in rows:
                row = QHBoxLayout()
                k = QLabel(key)
                k.setFont(theme.mono(9))
                k.setFixedWidth(210)
                k.setStyleSheet(f"background:{theme.SURFACE3}; border:1px solid {theme.BORDER}; border-radius:5px; padding:3px 8px;")
                row.addWidget(k)
                row.addWidget(QLabel(desc), 1)
                lay.addLayout(row)
        lay.addStretch(1)
        ok = QPushButton("Close")
        ok.setProperty("primary", True)
        ok.setMinimumHeight(36)
        ok.clicked.connect(self.accept)
        lay.addWidget(ok)


class DocTabBar(QTabBar):
    """Middle-click closes a tab; the Home tab stays in front."""

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            i = self.tabAt(e.position().toPoint())
            if i > 0:
                self.tabCloseRequested.emit(i)
                return
        super().mouseReleaseEvent(e)


class MainWindow(QMainWindow):
    def __init__(self, cfg=None):
        super().__init__()
        self.cfg = cfg or config.Config()
        self.setWindowTitle("Lavati Compare")
        self.resize(1440, 960)
        self.setMinimumSize(1000, 640)
        self.setAcceptDrops(True)
        self.tool_mode = False          # launched as a git (merge/diff) tool: quit when the tool tab closes
        self.merge_saved = False
        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setMovable(True)
        bar = DocTabBar()
        self.tabs.setTabBar(bar)
        bar.setDrawBase(False)
        bar.setExpanding(False)
        bar.setContextMenuPolicy(Qt.CustomContextMenu)
        bar.customContextMenuRequested.connect(self._tab_menu)
        bar.tabMoved.connect(self._tab_moved)
        self.setCentralWidget(self.tabs)
        self.home = HomePage(self.cfg)
        self.tabs.addTab(self.home, theme.icon("home", theme.TEXT, 20), "")
        bar.setIconSize(QSize(20, 20))
        self.home.compareRequested.connect(self.open_compare)
        self.home.sessionRequested.connect(self.open_session)
        self.home.kindRequested.connect(self.new_session)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        self.tabs.setTabsClosable(True)
        bar.setTabButton(0, QTabBar.RightSide, None)
        bar.setTabButton(0, QTabBar.LeftSide, None)
        self.tabs.currentChanged.connect(self._tab_changed)
        self.toast = Toast(self)
        self.overlay = DropOverlay(self)
        self.actions_by_name = {}
        self._tab_info = {}               # view -> how to reopen it on the next start
        self._menus()
        self._toolbar()
        for i in range(1, 10):
            QShortcut(QKeySequence(f"Ctrl+{i}"), self, activated=lambda i=i: self._goto_tab(i - 1))
        QShortcut(QKeySequence("Ctrl+Tab"), self, activated=lambda: self._cycle(1))
        QShortcut(QKeySequence("Ctrl+Shift+Tab"), self, activated=lambda: self._cycle(-1))
        self._update_actions()

    # ------------------------------------------------------------- actions
    def _act(self, menu, text, slot, shortcut=None, tip=None, icon=None, name=None, checkable=False):
        a = QAction(text, self)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
            a.setShortcutContext(Qt.WindowShortcut)
        if tip:
            a.setStatusTip(tip)
            a.setToolTip(tip + (f"  ({shortcut})" if shortcut else ""))
        if icon:
            a.setIcon(theme.icon(icon, theme.TEXT, 20))
        a.setCheckable(checkable)
        if checkable:
            a.toggled.connect(lambda on: slot(on))
        else:
            a.triggered.connect(lambda _=False: slot())
        if menu is not None:
            menu.addAction(a)
        if name:
            self.actions_by_name[name] = a
        return a

    def _cur(self, method, *args):
        w = self.tabs.currentWidget()
        fn = getattr(w, method, None)
        if callable(fn):
            return fn(*args)
        on_home = self.tabs.currentIndex() == 0
        self.toast.show_message("Open a comparison first - this action works inside a comparison tab" if on_home
                                else "This action isn't available for this type of comparison", "warn")

    def _menus(self):
        mb = self.menuBar()
        mb.setStyleSheet(f"QMenuBar {{ background:{theme.SURFACE}; border-bottom:1px solid {theme.BORDER}; padding:2px 8px; }}"
                         f"QMenuBar::item {{ padding:6px 14px; margin:2px 1px; border-radius:6px; background:transparent; color:{theme.TEXT}; }}"
                         f"QMenuBar::item:selected {{ background:{theme.HOVER2}; }}"
                         f"QMenuBar::item:pressed {{ background:{theme.ACCENT_SOFT}; }}")
        f = mb.addMenu("File")
        nm = f.addMenu("&New comparison")
        for k, (title, *_rest) in KINDS.items():
            self._act(nm, title, lambda k=k: self.new_session(k))
        self._act(f, "&Open two files or folders…", self.open_dialog, "Ctrl+O", "Compare any two files or folders", "folder_open", "open")
        self._act(f, "Compare pasted &text…", self.open_pasted_text, "Ctrl+Shift+T",
                  "Type or paste two pieces of text and compare them - no files needed", "doc")
        self._act(f, "Compare &Git revisions…", self.open_git, None, "Branches, tags, commits, index and working tree", "git", "git")
        self._act(f, "Local changes vs &last commit…", self.open_local_changes, None,
                  "Compare your working copy of a git repository with its last commit", "git")
        self._act(f, "Connect to a &server…", self.connect_server, None, "Compare a local folder with FTP, SFTP, WebDAV, S3 ...", "plug")
        f.addSeparator()
        self._act(f, "&Save changes", lambda: self._cur("save"), "Ctrl+S", "Save modified files", "save", "save")
        self._act(f, "Save left as…", lambda: self._cur("save_as", "L"), None, "Save the left file under another name")
        self._act(f, "Save right as…", lambda: self._cur("save_as", "R"), None, "Save the right file under another name")
        self._act(f, "&Close tab", lambda: self.close_tab(self.tabs.currentIndex()), "Ctrl+W")
        f.addSeparator()
        self._act(f, "E&xit", self.close, "Ctrl+Q")

        e = mb.addMenu("Edit")
        self._act(e, "&Undo", lambda: self._cur("undo"), "Ctrl+Z", "Undo in the active pane", "undo", "undo")
        self._act(e, "&Redo", lambda: self._cur("redo"), "Ctrl+Y", "Redo in the active pane", "redo", "redo")
        e.addSeparator()
        self._act(e, "&Find…", lambda: self._cur("find"), "Ctrl+F", "Find in both files", "search", "find")
        self._act(e, "Find &next", lambda: self._cur("find_next", 1), None)
        self._act(e, "&Go to line…", lambda: self._cur("goto_line"), "Ctrl+G")
        e.addSeparator()
        self._act(e, "S&wap left and right", lambda: self._cur("swap_sides"), "Ctrl+Shift+X", "Swap the two sides", "swap", "swap")

        v = mb.addMenu("View")
        self._act(v, "&Previous difference", lambda: self._cur("step", -1), "F7", "Previous difference", "up", "prev")
        self._act(v, "&Next difference", lambda: self._cur("step", 1), "F8", "Next difference", "down", "next")
        self._act(v, "&Refresh", self._refresh, "F5", "Compare again", "refresh", "refresh")
        v.addSeparator()
        self._act(v, "Go to &Home", lambda: self.tabs.setCurrentIndex(0), "Ctrl+H", "Home", "home", "home")
        self._act(v, "&Full screen", self._fullscreen, "F11")
        v.addSeparator()
        lm = v.addMenu("Text &layout")
        for key, label in (("side", "Side by side"), ("over", "Over / under")):
            self._act(lm, label, lambda key=key: self._cur("set_layout", key))
        self.act_wrap = self._act(v, "&Word wrap (text)", lambda on: self._cur("set_wrap", on), None, checkable=True)
        self.act_wrap.setChecked(bool(self.cfg["word_wrap"]))
        v.addSeparator()
        tm = v.addMenu("&Theme")
        grp = QActionGroup(self)
        for name, label in (("light", "Light"), ("dark", "Dark"), ("system", "Follow the system")):
            a = QAction(label, self, checkable=True)
            a.setChecked(self.cfg["theme"] == name)
            a.triggered.connect(lambda _=False, n=name: self.set_theme(n))
            grp.addAction(a)
            tm.addAction(a)

        t = mb.addMenu("Tools")
        self._act(t, "&Synchronize folders…", lambda: self._cur("sync"), "Ctrl+Shift+Y", "Synchronize the two folders", "refresh", "sync")
        self._act(t, "&Merge…", lambda: self.new_session("merge"), None, "Two- or three-way merge of files or folders", "merge")
        t.addSeparator()
        self._act(t, "Comparison &rules && file formats", lambda: self._home_page(2))
        self._act(t, "S&cripts && automation", lambda: self._home_page(3))
        self._act(t, "&Settings", lambda: self._home_page(4), "Ctrl+,", "Settings", "settings", "settings")

        h = mb.addMenu("Help")
        self._act(h, "&Keyboard shortcuts", lambda: ShortcutsDialog(self).exec(), "F1")
        self._act(h, "&About Lavati Compare", lambda: AboutDialog(self).exec())

        self._act_new = QAction(self)
        self._act_new.setShortcut(QKeySequence("Ctrl+N"))
        self._act_new.triggered.connect(lambda: (self.tabs.setCurrentIndex(0), self.home.p_left.edit.setFocus()))
        self.addAction(self._act_new)

    def _toolbar(self):
        tb = QToolBar("Main toolbar")
        tb.setMovable(False)
        tb.setIconSize(QSize(20, 20))
        tb.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        tb.setStyleSheet(f"QToolBar {{ background:{theme.SURFACE}; border-bottom:1px solid {theme.BORDER}; spacing:2px; padding:3px 8px; }}"
                         f"QToolButton {{ padding:5px 9px; border-radius:6px; }}"
                         f"QToolButton:hover {{ background:{theme.HOVER}; }}"
                         f"QToolButton:disabled {{ color:{theme.MUTED}; }}")
        self.addToolBar(Qt.TopToolBarArea, tb)
        self.toolbar = tb
        A = self.actions_by_name
        # contextual toolbar: hidden on Home, and on a tab it shows only what that kind of comparison supports
        self._tb_items = []
        for group in (("refresh", "swap"), ("prev", "next"), ("sync",), ("save", "undo", "redo", "find")):
            if self._tb_items:
                self._tb_items.append(tb.addSeparator())
            for n in group:
                tb.addAction(A[n])
                self._tb_items.append(A[n])
        self._caps = {"refresh": ("refresh", "compare", "reload"), "prev": ("step",), "next": ("step",), "swap": ("swap_sides",),
                      "sync": ("sync",), "save": ("save",), "undo": ("undo",), "redo": ("redo",), "find": ("find",)}
        tb.setVisible(False)

    def _update_actions(self):
        w = self.tabs.currentWidget()
        on_tab = self.tabs.currentIndex() > 0
        for name, methods in self._caps.items():
            a = self.actions_by_name.get(name)
            if a is not None:
                ok = on_tab and any(callable(getattr(w, m, None)) for m in methods)
                a.setVisible(ok)
                a.setEnabled(ok)
        # a separator is shown only between two visible groups
        seen_visible = False
        pending_sep = None
        for it in self._tb_items:
            if it.isSeparator():
                it.setVisible(False)
                pending_sep = it
            elif it.isVisible():
                if seen_visible and pending_sep is not None:
                    pending_sep.setVisible(True)
                seen_visible, pending_sep = True, None
        self.toolbar.setVisible(False)           # every comparison has its own toolbar; menus and shortcuts stay global
        self.actions_by_name["home"].setEnabled(on_tab)

    def _home_page(self, i):
        self.tabs.setCurrentIndex(0)
        self.home.show_page(i)

    def _refresh(self):
        w = self.tabs.currentWidget()
        for m in ("refresh", "compare", "reload"):
            if callable(getattr(w, m, None)):
                getattr(w, m)()
                return

    def _fullscreen(self):
        if self.isFullScreen():
            self.showMaximized()
        else:
            self.showFullScreen()

    def set_theme(self, name):
        if name == self.cfg["theme"]:
            return
        self.cfg["theme"] = name
        r = QMessageBox.question(self, "Theme changed", "Restart now to apply the new theme?")
        if r == QMessageBox.Yes:
            self.restart()

    def restart(self):
        import subprocess
        from . import platform_utils
        self.cfg.save()
        subprocess.Popen(platform_utils.app_launcher())
        QApplication.quit()

    def open_dialog(self):
        dlg = PickDialog("folder", self.home.p_left.text(), self.home.p_right.text(), self)
        dlg.setWindowTitle("Open two files or folders")
        for b in dlg.boxes:
            b.folder_ok = True
            b.registry = True
        if dlg.exec():
            kind, label = detect(dlg.values[0], dlg.values[1])
            if kind:
                self.open_compare(kind, dlg.values[0], dlg.values[1])
            else:
                QMessageBox.information(self, "Compare", f"{label}. Choose two files or two folders.")

    def connect_server(self):
        dlg = connect_dialog.ConnectDialog(self, "right")
        if dlg.exec() and dlg.url:
            local = self.home.p_left.text()
            if not local:
                self.home.set_paths(right=dlg.url)
                self.tabs.setCurrentIndex(0)
                toast_txt = "Server added as the right side - choose a folder for the left side and compare"
                self.toast.show_message(toast_txt, "info")
            else:
                self.open_compare("folder", local, dlg.url)

    # -------------------------------------------------------------- tabs
    def _tab_changed(self, i):
        t = self.tabs.tabText(i)
        self.setWindowTitle("Lavati Compare" if i <= 0 else f"{t} — Lavati Compare")
        self._update_actions()
        w = self.tabs.widget(i)
        if hasattr(w, "layout_mode"):
            pass

    def _goto_tab(self, i):
        if 0 <= i < self.tabs.count():
            self.tabs.setCurrentIndex(i)

    def _cycle(self, d):
        self.tabs.setCurrentIndex((self.tabs.currentIndex() + d) % self.tabs.count())

    def _tab_moved(self, frm, to):
        if frm == 0 or to == 0:
            self.tabs.tabBar().moveTab(to, frm)

    def _tab_menu(self, pos):
        i = self.tabs.tabBar().tabAt(pos)
        if i <= 0:
            return
        m = QMenu(self)
        m.addAction("Close", lambda: self.close_tab(i))
        m.addAction("Close other tabs", lambda: self._close_others(i))
        m.addAction("Close tabs to the right", lambda: self._close_right(i))
        m.exec(self.tabs.tabBar().mapToGlobal(pos))

    def _close_others(self, keep):
        w = self.tabs.widget(keep)
        for j in range(self.tabs.count() - 1, 0, -1):
            if self.tabs.widget(j) is not w:
                self.close_tab(j)

    def _close_right(self, i):
        for j in range(self.tabs.count() - 1, i, -1):
            self.close_tab(j)

    def show_toast(self, text, kind="ok"):
        self.toast.show_message(text, kind)

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self.overlay.setGeometry(self.tabs.geometry().translated(0, self.menuBar().height() + self.toolbar.height()))

    def add_tab(self, widget, title):
        idx = self.tabs.addTab(widget, title)
        btn = QToolButton()
        btn.setIcon(theme.icon("close", theme.MUTED, 14))
        btn.setIconSize(QSize(12, 12))
        btn.setAutoRaise(True)
        btn.setCursor(Qt.PointingHandCursor)
        btn.clicked.connect(lambda: self.close_tab(self.tabs.indexOf(widget)))
        self.tabs.tabBar().setTabButton(idx, QTabBar.RightSide, btn)
        self.tabs.setCurrentIndex(idx)
        return idx

    def close_tab(self, i):
        if i <= 0 or i >= self.tabs.count():
            return
        w = self.tabs.widget(i)
        if hasattr(w, "has_unsaved") and w.has_unsaved():
            self.tabs.setCurrentIndex(i)
            r = QMessageBox.question(self, "Unsaved changes", "Save changes before closing?",
                                     QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel)
            if r == QMessageBox.Cancel:
                return
            if r == QMessageBox.Save:
                w.save()
        if hasattr(w, "cancel_work"):
            w.cancel_work()
        self.tabs.removeTab(i)
        w.deleteLater()
        if self.tool_mode and self.tabs.count() <= 1:
            QTimer.singleShot(0, self.close)

    # ----------------------------------------------------------- sessions
    def open_pasted_text(self, left="", right=""):
        """Text compare with no files: two empty (or pre-filled) panes to type or paste into."""
        v = self.open_compare("text", "", "")
        if v is not None:
            for k, text in (("L", left), ("R", right)):
                if text:
                    v.panes[k].replace_lines(0, 0, text.splitlines())
            v.panes["L"].setFocus()
        return v

    def open_local_changes(self, start_path=""):
        """One step: current local changes (working tree) against the last commit (HEAD) of a repository."""
        from .widgets import pick_folder
        start = start_path
        if not start:
            for p in (self.home.p_left.text(), self.home.p_right.text()):
                if p and os.path.exists(p):
                    start = p
                    break
        repo = gitvfs.find_repo(start) if start else ""
        if not repo:
            d = pick_folder(self, "Choose the repository folder", start)
            repo = gitvfs.find_repo(d) if d else ""
            if not repo:
                if d:
                    QMessageBox.warning(self, "Git", "That folder is not inside a Git repository.")
                return None
        return self.open_compare("folder", gitvfs.make_spec(repo, "HEAD"), gitvfs.make_spec(repo, "WORKTREE"),
                                 {"git_renames": True})

    def open_git(self, start_path=""):
        """Git browser: pick a repository and two revisions, then compare them as folders (or merge with the merge base)."""
        start = start_path or self.home.p_left.text() or self.home.p_right.text()
        dlg = GitCompareDialog(self, start)
        if not dlg.exec() or not dlg.result_specs:
            return None
        if dlg.three_way:
            ours, base, theirs = dlg.result_specs
            return self.open_merge(ours, base, theirs, None, origin="git")
        left, right = dlg.result_specs
        return self.open_compare("folder", left, right, {"git_renames": dlg.detect_renames})

    def set_titles(self, view, t1=None, t2=None, t3=None):
        """Friendly names for the sides (git passes temporary file names such as Main_LOCAL_1234.kt)."""
        if view is None:
            return
        if hasattr(view, "set_titles"):
            view.set_titles(t1, t2, t3)
        i = self.tabs.indexOf(view)
        if i > 0 and t1 and t2:
            strip = lambda t: t.rsplit(" (", 1)[0]
            self.tabs.setTabText(i, strip(t1) if strip(t1) == strip(t2) else f"{strip(t1)} vs {strip(t2)}")

    def new_session(self, kind):
        if kind == "git":
            return self.open_git()
        l, r = self.home.p_left.text(), self.home.p_right.text()
        if kind != "merge":
            k, _ = detect(l, r)
            ok = k is not None and (k == kind or (kind == "text" and k in ("table", "hex", "registry", "doc", "media")) or
                                    (kind == "hex" and k in ("text", "table", "media", "doc")) or
                                    (kind in ("version", "media", "doc") and k in ("hex", "text", "media", "doc")))
            if ok:
                self.open_compare(kind, l, r)
                return
        dlg = PickDialog(kind, l, r, self)
        if dlg.exec():
            v = dlg.values
            if getattr(dlg, "paste", False):
                return self.open_pasted_text()
            if kind == "merge":
                self.open_merge(v[0], v[1] or None, v[2], v[3] or None)
            else:
                self.open_compare(kind, v[0], v[1])

    def open_session(self, s):
        ex = s.get("extra", {}) or {}
        if s["kind"] == "merge":
            self.open_merge(s["left"], ex.get("base") or None, s["right"], ex.get("output"))
            return
        if s["kind"] == "sync":
            v = self.open_compare("folder", s["left"], s["right"], ex)
            if v is not None:
                v.sync_when_ready(ex.get("preset"))
            return
        for p in (s["left"], s["right"]):
            if not (vfs.is_remote_spec(p) or gitvfs.is_git_spec(p) or os.path.exists(p) or _is_registry(p)):
                QMessageBox.warning(self, "Session", f"Path no longer exists:\n{p}")
                return
        self.open_compare(s["kind"], s["left"], s["right"], ex)

    def open_compare(self, kind, left, right, extra=None):
        left, right = remote.stash_url_secrets(left), remote.stash_url_secrets(right)
        name = session_name(kind, left, right)
        v = None
        try:
            if kind in ("folder", "snapshot"):
                kind = "folder"
                v = FolderCompareView(self.cfg, left, right, extra=extra)
                v.openFile.connect(self.open_file_pair)
                v.openAs.connect(self._open_as)
            elif kind in ("text", "doc"):
                v = TextCompareView(self.cfg, left, right)
            elif kind == "table":
                v = TableCompareView(self.cfg, left, right)
            elif kind == "hex":
                v = HexCompareView(self.cfg, left, right)
            elif kind == "image":
                v = ImageCompareView(self.cfg, left, right)
            elif kind == "media":
                v = MediaCompareView(self.cfg, left, right)
            elif kind == "version":
                v = VersionCompareView(self.cfg, left, right)
            elif kind == "registry":
                v = RegistryCompareView(self.cfg, left, right)
            else:
                return None
        except Exception as e:  # keep the app alive on bad input
            QMessageBox.critical(self, "Could not open session", f"{type(e).__name__}: {e}")
            return None
        self._remember(kind, v, name, left, right)
        self._tab_info[v] = dict(kind=kind, left=left, right=right)
        if hasattr(v, "optionsChanged"):
            v.optionsChanged.connect(lambda k=kind, vv=v, n=name: self._remember(k, vv, n, vv.left, vv.right))
        v.resultChanged.connect(lambda txt, k=kind, vv=v: self._result(k, getattr(vv, "left", left), getattr(vv, "right", right), txt))
        title = name if kind != "text" else (os.path.basename(right or left) or "Pasted text")
        self.add_tab(v, title)
        self.home.refresh()
        return v

    def _remember(self, kind, v, name, left, right):
        if not left and not right:
            return                                    # pasted text has no paths to reopen
        left, right = remote.stash_url_secrets(left), remote.stash_url_secrets(right)
        extra = v.session_extra() if hasattr(v, "session_extra") else None
        self.cfg.add_session(session_name(kind, left, right) if left != "" else name, kind, left, right, extra=extra)

    def _is_tool_session(self):
        return self.tool_mode

    def _open_as(self, kind, left, right):
        self.open_compare(kind, left, right)

    def _result(self, kind, l, r, txt):
        self.cfg.update_result(kind, l, r, txt)
        self.home.refresh()

    def open_file_pair(self, left, right):
        """Open a file comparison (from the folder view)."""
        if not left or not right:
            QMessageBox.information(self, "Open", "This file exists on only one side.")
            return
        kind, _ = detect(left, right)
        if kind is None:
            return
        if kind == "table" and os.path.splitext(left)[1].lower() in (".csv", ".tsv"):
            kind = "text"
        self.open_compare(kind, left, right)

    def open_merge(self, ours, base, theirs, output=None, origin=""):
        try:
            if os.path.isdir(ours) or vfs.is_archive_file(ours) or vfs.is_remote_spec(ours) or gitvfs.is_git_spec(ours):
                v = FolderMergeView(self.cfg, ours, base, theirs, output)
                v.mergeRequested.connect(lambda e, o, b, t, out, fv=v: self._folder_text_merge(fv, e, o, b, t, out))
                name = f"{os.path.basename(ours.rstrip('/' + os.sep)) or 'folders'} (folder merge)"
            else:
                v = MergeView(self.cfg, ours, base, theirs, output, origin)
                name = f"{os.path.basename(output or ours)} (merge)"
        except Exception as e:
            QMessageBox.critical(self, "Could not open merge", f"{type(e).__name__}: {e}")
            return None
        self.cfg.add_session(name.replace(" (merge)", " merge"), "merge", ours, theirs,
                             extra={"base": base or "", "output": output})
        v.resultChanged.connect(lambda txt, l=ours, r=theirs: self._result("merge", l, r, txt))
        v.saved.connect(lambda _p: setattr(self, "merge_saved", True))
        self._tab_info[v] = dict(kind="merge", left=ours, right=theirs, base=base or "", output=output)
        self.add_tab(v, name)
        self.home.refresh()
        return v

    def _folder_text_merge(self, fv, entry, ours, base, theirs, out):
        v = self.open_merge(ours, base or None, theirs, out, origin="folder merge")
        if v is not None and hasattr(v, "saved"):
            v.saved.connect(lambda p, fv=fv, e=entry: fv.set_merged(e, p))

    # --------------------------------------------------------- drag & drop
    def dragEnterEvent(self, e):
        if e.mimeData().hasUrls():
            self.overlay.setGeometry(self.tabs.geometry().translated(0, self.menuBar().height() + self.toolbar.height()))
            self.overlay.raise_()
            self.overlay.show()
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self.overlay.hide()

    def dropEvent(self, e):
        self.overlay.hide()
        paths = [os.path.normpath(u.toLocalFile()) for u in e.mimeData().urls() if u.isLocalFile()]
        if not paths:
            return
        self.add_dropped(paths)
        e.acceptProposedAction()

    def add_dropped(self, paths):
        """Two paths start a comparison, three a merge; a single path waits for its partner."""
        self.tabs.setCurrentIndex(0)
        if len(paths) >= 3:
            self.open_merge(paths[0], paths[1], paths[2])
        elif len(paths) == 2:
            self.home.set_paths(paths[0], paths[1])
            kind, _ = detect(paths[0], paths[1])
            if kind:
                self.open_compare(kind, paths[0], paths[1])
        else:
            if not self.home.p_left.text() or self.home.p_right.text():
                self.home.set_paths(paths[0], "")
                self.toast.show_message("Added — drop or choose the second item", "info")
            else:
                self.home.set_paths(right=paths[0])
                kind, _ = detect(self.home.p_left.text(), paths[0])
                if kind:
                    self.open_compare(kind, self.home.p_left.text(), paths[0])

    def save_open_tabs(self):
        """Remember the open tabs (in tab order) so the next start can bring them back."""
        out = []
        for i in range(1, self.tabs.count()):
            info = self._tab_info.get(self.tabs.widget(i))
            if info and (info.get("left") or info.get("right")):
                info = dict(info)
                w = self.tabs.widget(i)
                if hasattr(w, "session_extra"):
                    info["extra"] = w.session_extra()
                out.append(info)
        self.cfg["open_tabs"] = out[-12:]

    def restore_tabs(self):
        """Reopen the tabs saved at the last exit. Entries whose files vanished are skipped."""
        n = 0
        for info in list(self.cfg["open_tabs"]):
            try:
                paths = [info.get("left", ""), info.get("right", "")]
                if not all(vfs.is_remote_spec(p) or gitvfs.is_git_spec(p) or os.path.exists(p) or _is_registry(p) for p in paths if p):
                    continue
                if info.get("kind") == "merge":
                    self.open_merge(info["left"], info.get("base") or None, info["right"], info.get("output"))
                else:
                    self.open_compare(info["kind"], info["left"], info["right"], info.get("extra"))
                n += 1
            except Exception:
                continue
        if n:
            self.tabs.setCurrentIndex(1)
        return n

    def closeEvent(self, e):
        if not self.tool_mode:
            for i in range(self.tabs.count() - 1, 0, -1):
                w = self.tabs.widget(i)
                if hasattr(w, "has_unsaved") and w.has_unsaved():
                    self.tabs.setCurrentIndex(i)
                    r = QMessageBox.question(self, "Unsaved changes", "Some tabs have unsaved changes. Quit anyway?")
                    if r != QMessageBox.Yes:
                        e.ignore()
                        return
                    break
        if not self.tool_mode and self.cfg["restore_tabs"]:
            self.save_open_tabs()
        for i in range(self.tabs.count() - 1, 0, -1):
            w = self.tabs.widget(i)
            if hasattr(w, "cancel_work"):
                try:
                    w.cancel_work()
                except Exception:
                    pass
        e.accept()


def _is_registry(p):
    from . import regcmp
    return regcmp.is_registry_spec(p)
