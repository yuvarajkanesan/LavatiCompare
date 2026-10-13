"""Lavati Compare - entry point.

Usage:
    lavati                                     open the home screen
    lavati LEFT RIGHT                          compare two files / folders / archives / URLs
    lavati A B C                               three-way merge (ours, base, theirs)
    lavati --compare LEFT RIGHT [--type KIND]  force the comparison type: folder text hex table image media version registry doc
    lavati --merge LOCAL BASE REMOTE [MERGED]  three-way merge; BASE may be "" for a two-way merge
    lavati --merge LOCAL REMOTE BASE --output MERGED [--title1 T --title2 T --title3 T]
                                               git mergetool form: friendly titles, exit code 0 only when the merge was saved
    lavati LEFT RIGHT [--title1 T --title2 T]  git difftool form (two files or two folders for --dir-diff)
    lavati --git REPO [REV_A [REV_B]]          compare two revisions of a repository (REV: branch, tag, commit, INDEX, WORKTREE)
    lavati --merge2 LEFT RIGHT [MERGED]        two-way merge
    lavati --foldermerge OURS BASE THEIRS [OUTPUT]
    lavati --script FILE [--var NAME=VALUE]    run a script without opening a window (exit code 0 ok, 1 differences/errors, 2 script error)
    lavati --sync MODE LEFT RIGHT              one-shot synchronisation; MODE = update-right | update-left | update-both | mirror-right | mirror-left
    lavati --report FORMAT OUTPUT LEFT RIGHT   write a report (FORMAT = html | text | xml) for two folders or two files
    lavati --version | --help
"""
import os
import sys

VERSION_TEXT = "Lavati Compare {}"
KINDS = ("folder", "text", "hex", "table", "image", "media", "version", "registry", "doc")


def _pop_option(args, name, nargs=1):
    """Remove `name` (and its values) from args; returns the values or None."""
    if name in args:
        i = args.index(name)
        vals = args[i + 1:i + 1 + nargs]
        del args[i:i + 1 + nargs]
        return vals
    return None


def selftest():
    """Exercise every bundled component once (used to prove a packaged build is complete)."""
    import io
    import tempfile
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    ok = True

    def check(name, fn):
        nonlocal ok
        try:
            fn()
            print(f"ok    {name}")
        except Exception as e:
            ok = False
            print(f"FAIL  {name}: {type(e).__name__}: {e}")

    def qt():
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication
        app = QApplication.instance() or QApplication([])
        from lavati import theme
        assert not theme.icon("folder", "#000000", 20).isNull()
        assert not theme.logo_pixmap(64).isNull()
        d = tempfile.mkdtemp()
        from PIL import Image
        for ext in ("png", "jpg", "gif", "bmp"):
            p = os.path.join(d, "t." + ext)
            Image.new("RGB", (8, 8), (200, 10, 10)).save(p)
            assert not QImage(p).isNull(), ext

    def xl():
        import openpyxl
        from lavati import engine
        d = tempfile.mkdtemp()
        p = os.path.join(d, "a.xlsx")
        wb = openpyxl.Workbook()
        wb.active.append(["k", "v"])
        wb.active.append(["a", 1])
        wb.save(p)
        assert engine.load_table(p) == (["k", "v"], [["a", "1"]])

    def pdf():
        import pypdfium2
        assert pypdfium2.PdfDocument

    def ssh():
        import paramiko
        from cryptography.hazmat.primitives import hashes
        assert paramiko.SSHClient and hashes.SHA256

    def zips():
        from lavati import vfs
        import zipfile
        d = tempfile.mkdtemp()
        p = os.path.join(d, "a.zip")
        with zipfile.ZipFile(p, "w") as z:
            z.writestr("x/y.txt", "hi")
        fs, root = vfs.open_location(p)
        assert fs.scandir(root)[0].name == "x"

    def tls():
        import ssl
        import urllib.request
        assert ssl.create_default_context()

    check("Qt widgets, SVG icons and image codecs", qt)
    check("Excel (openpyxl)", xl)
    check("PDF text (pypdfium2)", pdf)
    check("SFTP (paramiko + cryptography)", ssh)
    check("archives", zips)
    check("TLS / downloads", tls)
    print("SELFTEST " + ("PASSED" if ok else "FAILED"))
    return 0 if ok else 1


def headless(args):
    """Commands that need no window. Returns an exit code or None when the arguments are not headless."""
    from lavati import script
    args = list(args)
    if not args:
        return None
    cmd = args[0]
    if cmd in ("--script", "-s", "/script"):
        rest = args[1:]
        variables = {}
        while True:
            v = _pop_option(rest, "--var")
            if not v:
                break
            if "=" in v[0]:
                k, _, val = v[0].partition("=")
                variables[k] = val
        if not rest:
            print("usage: lavati --script FILE [--var NAME=VALUE]", file=sys.stderr)
            return 2
        try:
            return script.run_file(rest[0], variables)
        except OSError as e:
            print(f"cannot read script: {e}", file=sys.stderr)
            return 2
    if cmd == "--sync":
        if len(args) < 4:
            print("usage: lavati --sync MODE LEFT RIGHT [--no-backup] [--no-trash]", file=sys.stderr)
            return 2
        mode = args[1].replace("-", "_").lower()
        mode = {"update_right": "update:left->right", "update_left": "update:right->left", "update_both": "update:both",
                "mirror_right": "mirror:left->right", "mirror_left": "mirror:right->left"}.get(mode, args[1])
        flags = []
        flags.append("backup=off" if "--no-backup" in args else "backup=on")
        flags.append("trash=off" if "--no-trash" in args else "trash=on")
        text = f'load "{args[2]}" "{args[3]}"\ncompare\nsync {mode} {" ".join(flags)}\n'
        return script.ScriptRunner().run(text)
    if cmd == "--report":
        if len(args) < 5:
            print("usage: lavati --report FORMAT OUTPUT LEFT RIGHT [--show differences|all|same|orphans]", file=sys.stderr)
            return 2
        fmt, out, left, right = args[1].lower(), args[2], args[3], args[4]
        show = (_pop_option(list(args), "--show") or ["differences"])[0]
        if os.path.isdir(left) or left.startswith(("ftp", "sftp", "s3", "dav")):
            text = f'load "{left}" "{right}"\ncompare\nfolder-report output-to:"{out}" format={fmt} show={show}\n'
        else:
            text = f'file-report "{left}" "{right}" output-to:"{out}" format={fmt}\n'
        return script.ScriptRunner().run(text)
    if cmd == "--selftest":
        return selftest()
    if cmd in ("--version", "-v"):
        from lavati import __version__
        print(VERSION_TEXT.format(__version__))
        return 0
    if cmd in ("--help", "-h", "/?"):
        print(__doc__)
        return 0
    return None


def main(argv):
    args0 = argv[1:]
    code = headless(args0)
    if code is not None:
        return code
    if len(args0) == 2 and args0[0] == "--select":      # file manager: remember the first item, no window
        from lavati import shell
        shell.set_pending(os.path.abspath(args0[1]))
        return 0

    from PySide6.QtCore import QEvent, Qt, Signal
    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication, QProxyStyle, QStyle

    from lavati import config, theme

    class LavatiApp(QApplication):
        fileOpened = Signal(str)

        def event(self, e):
            if e.type() == QEvent.FileOpen:             # macOS: Open With / drop on the Dock icon
                self.fileOpened.emit(e.file())
                return True
            return super().event(e)

    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)
    try:  # own taskbar identity so Windows shows our icon, not python.exe's
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Lavati.Compare.2")
    except (AttributeError, OSError):
        pass
    app = LavatiApp(argv)
    app.setApplicationName("Lavati Compare")

    class _Style(QProxyStyle):          # Fusion without always-visible mnemonic underlines
        def styleHint(self, hint, option=None, widget=None, returnData=None):
            if hint == QStyle.SH_UnderlineShortcut:
                return 0
            return super().styleHint(hint, option, widget, returnData)

    app.setStyle(_Style("Fusion"))
    cfg = config.Config()
    name = cfg["theme"]
    if name == "system":
        try:
            name = "dark" if app.styleHints().colorScheme() == Qt.ColorScheme.Dark else "light"
        except AttributeError:
            name = "light"
    theme.apply(name)                  # palette must be chosen before the views are imported
    theme.set_sizes(cfg["font_size"])
    from lavati.app import MainWindow
    from lavati.home import detect
    app.setWindowIcon(theme.app_icon())
    app.setFont(QFont(theme.ui_family(), theme.UI_SIZE))
    app.setPalette(theme.palette())
    app.setStyleSheet(theme.qss())
    from lavati.widgets import MenuStyler
    styler = MenuStyler(app)
    app.installEventFilter(styler)
    app._menu_styler = styler
    win = MainWindow(cfg)
    win.showMaximized()
    app.fileOpened.connect(lambda p: win.add_dropped([p]))

    args = list(args0)
    forced = None
    t = _pop_option(args, "--type")
    if t:
        forced = t[0].lower()
        if forced not in KINDS:
            print(f"unknown type '{forced}' (use one of: {', '.join(KINDS)})", file=sys.stderr)
    if len(args) == 2 and args[0] == "--compare-with":  # file manager: second item -> compare with the remembered one
        from lavati import shell
        other = os.path.abspath(args[1])
        first = shell.take_pending()
        if first:
            args = [first, other]
        else:
            win.home.set_paths(other, "")
            win.show_toast("Pick the second item to compare", "info")
            args = []
    tool = False
    titles = [(_pop_option(args, f"--title{i}") or [None])[0] for i in (1, 2, 3)]
    output = (_pop_option(args, "--output") or [None])[0]
    if args and args[0] == "--compare":
        args = args[1:]
    if args and args[0] == "--git":
        from lavati import gitvfs
        repo = os.path.abspath(args[1]) if len(args) > 1 else os.getcwd()
        top = gitvfs.find_repo(repo)
        if not top:
            print(f"not a git repository: {repo}", file=sys.stderr)
            win.show_toast("Not a Git repository", "warn")
        else:
            a = args[2] if len(args) > 2 else "HEAD"
            b = args[3] if len(args) > 3 else "WORKTREE"
            win.open_compare("folder", gitvfs.make_spec(top, a), gitvfs.make_spec(top, b), {"git_renames": True})
        args = []
    if args and args[0] in ("--merge", "--merge2", "--foldermerge"):
        mode, rest = args[0], args[1:]
        win.tool_mode = True
        tool = True
        v = None
        if mode == "--merge2" and len(rest) >= 2:
            v = win.open_merge(rest[0], None, rest[1], rest[2] if len(rest) > 2 else output, origin="command line")
        elif mode == "--merge" and output is not None and len(rest) >= 2:
            # git mergetool form: LOCAL REMOTE [BASE] --output MERGED
            base = rest[2] if len(rest) > 2 and rest[2] and os.path.exists(rest[2]) and os.path.getsize(rest[2]) > 0 else None
            v = win.open_merge(rest[0], base, rest[1], output, origin="git mergetool")
        elif len(rest) >= 3:
            base = rest[1] if rest[1] and os.path.exists(rest[1]) else None
            v = win.open_merge(rest[0], base, rest[2], rest[3] if len(rest) > 3 else output,
                               origin="git mergetool" if mode == "--merge" else "command line")
        else:
            print("usage: --merge LOCAL BASE REMOTE [MERGED]", file=sys.stderr)
        if v is not None and any(titles):
            win.set_titles(v, *titles)
    elif len(args) >= 3:
        win.open_merge(args[0], args[1], args[2], args[3] if len(args) > 3 else None, origin="command line")
    elif len(args) == 2:
        l = args[0] if "://" in args[0] else os.path.abspath(args[0])
        r = args[1] if "://" in args[1] else os.path.abspath(args[1])
        kind, label = detect(l, r)
        if forced or kind:
            v = win.open_compare(forced or kind, l, r)
            if v is not None and any(titles):
                win.set_titles(v, *titles)
        else:
            win.home.set_paths(l, r)
            win.show_toast(label, "warn")
        # `git difftool` and friends wait for the process: a lone compare window is fine, the user closes it
    if not args0 and cfg["restore_tabs"]:
        win.restore_tabs()
    rc = app.exec()
    if tool:
        return 0 if win.merge_saved else 1       # git (trustExitCode) must not accept an unsaved merge
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv))
