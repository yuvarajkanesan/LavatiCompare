"""OS helpers that differ between Windows, macOS and Linux (no Qt imports)."""
import os
import shutil
import subprocess
import sys
import time

IS_WIN = sys.platform.startswith("win")
IS_MAC = sys.platform == "darwin"
IS_LINUX = sys.platform.startswith("linux")


def config_dir():
    """Per-user settings folder: %APPDATA%, ~/Library/Application Support or $XDG_CONFIG_HOME."""
    if IS_WIN:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
    elif IS_MAC:
        base = os.path.expanduser("~/Library/Application Support")
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return os.path.join(base, "LavatiCompare")


# ------------------------------------------------------------------ trash
def _trash_linux(path):
    """Freedesktop.org trash spec (home trash only)."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    files = os.path.join(base, "Trash", "files")
    info = os.path.join(base, "Trash", "info")
    os.makedirs(files, exist_ok=True)
    os.makedirs(info, exist_ok=True)
    name = os.path.basename(path.rstrip("/"))
    target, n = name, 1
    while os.path.exists(os.path.join(files, target)) or os.path.exists(os.path.join(info, target + ".trashinfo")):
        n += 1
        target = f"{name}.{n}"
    from urllib.parse import quote
    with open(os.path.join(info, target + ".trashinfo"), "w", encoding="utf-8") as f:
        f.write("[Trash Info]\nPath=%s\nDeletionDate=%s\n" % (quote(os.path.abspath(path)),
                                                              time.strftime("%Y-%m-%dT%H:%M:%S")))
    shutil.move(path, os.path.join(files, target))


def _trash_mac(path):
    dest = os.path.expanduser("~/.Trash")
    os.makedirs(dest, exist_ok=True)
    name = os.path.basename(path.rstrip("/"))
    target, n = os.path.join(dest, name), 1
    while os.path.exists(target):
        n += 1
        target = os.path.join(dest, f"{name} {n}")
    shutil.move(path, target)


def _trash_win(path):
    import ctypes
    from ctypes import wintypes

    class SHFILEOPSTRUCT(ctypes.Structure):
        _fields_ = [("hwnd", wintypes.HWND), ("wFunc", wintypes.UINT), ("pFrom", wintypes.LPCWSTR),
                    ("pTo", wintypes.LPCWSTR), ("fFlags", ctypes.c_ushort), ("fAnyOperationsAborted", wintypes.BOOL),
                    ("hNameMappings", ctypes.c_void_p), ("lpszProgressTitle", wintypes.LPCWSTR)]
    op = SHFILEOPSTRUCT()
    op.wFunc = 3          # FO_DELETE
    op.pFrom = os.path.abspath(path) + "\0\0"
    op.fFlags = 0x40 | 0x10 | 0x4 | 0x400   # ALLOWUNDO | NOCONFIRMATION | NOERRORUI | SILENT
    if ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op)) != 0:
        raise OSError("Recycle Bin move failed")


def move_to_trash(path):
    """Send a file or folder to the Recycle Bin / Trash. Returns True on success."""
    try:
        if IS_WIN:
            _trash_win(path)
        elif IS_MAC:
            _trash_mac(path)
        else:
            _trash_linux(path)
        return not os.path.exists(path)
    except (OSError, AttributeError):
        return False


def delete_permanently(path):
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return True
    except OSError:
        return False


# ------------------------------------------------------------ file manager
def reveal(path):
    """Show a file in Explorer / Finder / the default file manager."""
    if not path or not os.path.exists(path):
        return
    path = os.path.normpath(path)
    try:
        if IS_WIN:
            subprocess.Popen(["explorer", "/select,", path])
        elif IS_MAC:
            subprocess.Popen(["open", "-R", path])
        else:
            subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
    except OSError:
        pass


def open_default(path):
    try:
        if IS_WIN:
            os.startfile(path)      # noqa: S606 - user-initiated
        elif IS_MAC:
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
    except OSError:
        pass


def edit_file(path):
    try:
        if IS_WIN:
            os.startfile(path, "edit")
        else:
            open_default(path)
    except OSError:
        open_default(path)


def run_script(path):
    """Run a saved script (.bat/.cmd/.ps1/.sh) in a visible terminal where possible."""
    ext = os.path.splitext(path)[1].lower()
    if IS_WIN:
        if ext == ".ps1":
            cmd = ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", path]
        else:
            cmd = ["cmd", "/c", path]
        subprocess.Popen(cmd, creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0))
    else:
        subprocess.Popen(["sh", path])


def app_launcher():
    """Command list that starts this application (frozen executable or python + main.py)."""
    if getattr(sys, "frozen", False):
        return [sys.executable]
    main = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "main.py"))
    py = sys.executable
    if IS_WIN:
        pyw = os.path.join(os.path.dirname(py), "pythonw.exe")
        if os.path.exists(pyw):
            py = pyw
    return [py, main]


def schedule_command(script_path, when="02:00"):
    """A ready-to-paste command that runs a script nightly with the OS scheduler."""
    parts = app_launcher()
    if IS_WIN:
        launcher = " ".join(f'\\"{p}\\"' for p in parts)
        return f'schtasks /Create /SC DAILY /ST {when} /TN "Lavati nightly" /TR "{launcher} --script \\"{script_path}\\""'
    launcher = " ".join(f"'{p}'" for p in parts)
    h, m = when.split(":")
    return f"( crontab -l 2>/dev/null; echo \"{int(m)} {int(h)} * * * {launcher} --script '{script_path}'\" ) | crontab -"
