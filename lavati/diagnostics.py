"""Crash / error log and 'Report a problem' text. Nothing is sent anywhere: the log stays on this computer until the user
chooses to copy it or open a prefilled GitHub issue. No Qt imports at module level."""
import faulthandler
import logging
import logging.handlers
import os
import platform
import re
import sys
import threading
import time
import traceback
import urllib.parse

from . import __version__, config

REPO = "yuvarajkanesan/LavatiCompare"
LOG_DIR = os.path.join(config.APP_DIR, "logs")
LOG_PATH = os.path.join(LOG_DIR, "lavati.log")
CRASH_PATH = os.path.join(LOG_DIR, "crash.log")
log = logging.getLogger("lavati")

last_error = ""            # text of the latest unhandled exception (this run)
on_error = None            # optional callback(text) the main window sets to show a notice
_fault_file = None
_installed = False


def setup(log_dir=None):
    """Start logging to a rotating file, record unhandled exceptions and native crashes. Safe to call twice."""
    global _installed, _fault_file, LOG_DIR, LOG_PATH, CRASH_PATH
    if _installed:
        return
    if log_dir:
        LOG_DIR = log_dir
        LOG_PATH = os.path.join(LOG_DIR, "lavati.log")
        CRASH_PATH = os.path.join(LOG_DIR, "crash.log")
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=512 * 1024, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        log.addHandler(handler)
        log.setLevel(logging.INFO)
        log.info("Lavati Compare %s starting on %s, Python %s", __version__, platform.platform(), platform.python_version())
        _fault_file = open(CRASH_PATH, "a", encoding="utf-8")
        faulthandler.enable(file=_fault_file, all_threads=True)
    except OSError:
        pass                                                  # logging is a convenience; never stop the app for it
    sys.excepthook = _hook
    threading.excepthook = _thread_hook
    _installed = True


def install_qt_handler():
    try:
        from PySide6.QtCore import QtMsgType, qInstallMessageHandler

        def handler(kind, ctx, msg):
            if kind in (QtMsgType.QtWarningMsg, QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
                log.warning("Qt: %s", msg)
        qInstallMessageHandler(handler)
    except Exception:
        pass


def _record(exc_type, exc, tb, where=""):
    global last_error
    text = "".join(traceback.format_exception(exc_type, exc, tb))
    last_error = text
    log.error("Unhandled exception%s\n%s", where, text)
    return text


def _hook(exc_type, exc, tb):
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc, tb)
        return
    text = _record(exc_type, exc, tb)
    if on_error:
        try:
            on_error(text)
        except Exception:
            pass


def _thread_hook(args):
    if args.exc_type is SystemExit:
        return
    _record(args.exc_type, args.exc_value, args.exc_traceback, f" in thread {getattr(args.thread, 'name', '?')}")


def sanitize(text):
    """Remove the user name and home folder from text that is about to be shared."""
    home = os.path.expanduser("~")
    if home and home != "~":
        text = text.replace(home, "~").replace(home.replace("\\", "/"), "~")
        user = os.path.basename(home.rstrip("\\/"))
        if len(user) > 2:
            # only where it is a folder name in a path (C:\Users\<user>\...), never in ordinary words
            sep = chr(92) + chr(47)                           # backslash and slash
            text = re.sub("(?<=[" + re.escape(sep) + "])" + re.escape(user) + "(?=[" + re.escape(sep) + "\\s\"'])", "<user>", text, flags=re.I)
    return text


def environment():
    from . import gitvfs
    try:
        from PySide6 import __version__ as qt
    except Exception:
        qt = "?"
    gv = gitvfs.version()
    return [f"Lavati Compare {__version__}", f"OS: {platform.platform()}", f"Python {platform.python_version()}, PySide6 {qt}",
            f"git: {'.'.join(map(str, gv)) if gv else 'not found'}", f"frozen: {bool(getattr(sys, 'frozen', False))}"]


def log_tail(lines=60):
    try:
        with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
            return "".join(f.readlines()[-lines:])
    except OSError:
        return ""


def report_text(include_log=True):
    parts = ["## Environment", *environment()]
    if last_error:
        parts += ["", "## Last error in this session", last_error.strip()]
    if include_log:
        tail = log_tail()
        if tail:
            parts += ["", "## Recent log", tail.strip()]
    return sanitize("\n".join(parts))


def issue_url(title, body, limit=5500):
    """GitHub 'new issue' address with the text prefilled (long text is cut so the address stays usable)."""
    if len(body) > limit:
        body = body[:limit] + "\n\n[cut - attach the full log from Help > Open log folder]"
    q = urllib.parse.urlencode({"title": title, "body": body}, quote_via=urllib.parse.quote)
    return f"https://github.com/{REPO}/issues/new?{q}"


def open_log_folder():
    from . import platform_utils
    os.makedirs(LOG_DIR, exist_ok=True)
    platform_utils.reveal(LOG_DIR if os.path.isdir(LOG_DIR) else LOG_PATH)


def age_text(t):
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(t)) if t else "never"
