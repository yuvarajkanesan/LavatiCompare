"""File-manager integration: "Select left side" / "Compare to selected" context-menu entries.

Windows: per-user Explorer entries in HKCU. Linux: Nautilus scripts and a Dolphin service menu.
macOS: no context-menu entries are installed; use "Open With" or drag items onto the Dock icon.
"""
import os
import shlex
import stat
import sys

from . import config, platform_utils

PENDING = os.path.join(config.APP_DIR, "pending.txt")
LABEL_SELECT = "Select as left side (Lavati Compare)"
LABEL_COMPARE = "Compare to the selected left side (Lavati Compare)"
VERBS = (("LavatiSelect", LABEL_SELECT, "--select"), ("LavatiCompareWith", LABEL_COMPARE, "--compare-with"))
ROOTS = (r"Software\Classes\*\shell", r"Software\Classes\Directory\shell")


class ShellError(Exception):
    pass


def title():
    if platform_utils.IS_WIN:
        return "Windows Explorer integration"
    if platform_utils.IS_MAC:
        return "Finder integration"
    return "File manager integration"


def description():
    if platform_utils.IS_WIN:
        return ("Adds “Select as left side” and “Compare to the selected left side” to the right-click menu of files and "
                "folders. Select one item, then right-click a second one to compare.")
    if platform_utils.IS_MAC:
        return ("Right-click files in Finder and choose Open With › Lavati Compare, or drop two items onto the Dock icon. "
                "Finder context-menu entries are not installed automatically.")
    return ("Adds the same two entries to the Nautilus (Files) scripts menu and to the Dolphin right-click menu. "
            "Select one item, then right-click a second one to compare.")


def is_supported():
    return platform_utils.IS_WIN or platform_utils.IS_LINUX


def launcher():
    """Command prefix that starts this app (frozen executable or pythonw + main.py), as one string."""
    parts = platform_utils.app_launcher()
    if platform_utils.IS_WIN:
        return " ".join(f'"{p}"' for p in parts)
    return " ".join(shlex.quote(p) for p in parts)


def icon_path():
    if getattr(sys, "frozen", False):
        return sys.executable
    from . import theme
    p = theme.resource("assets", "lavati.ico")
    return p if os.path.exists(p) else ""


# ------------------------------------------------------------------ Windows
def _win_register():
    import winreg
    cmd = launcher()
    ico = icon_path()
    for root in ROOTS:
        for key, label, flag in VERBS:
            k = winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{root}\{key}")
            winreg.SetValueEx(k, None, 0, winreg.REG_SZ, label)
            if ico:
                winreg.SetValueEx(k, "Icon", 0, winreg.REG_SZ, ico)
            winreg.CloseKey(k)
            c = winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{root}\{key}\command")
            winreg.SetValueEx(c, None, 0, winreg.REG_SZ, f'{cmd} {flag} "%1"')
            winreg.CloseKey(c)


def _win_unregister():
    import winreg
    for root in ROOTS:
        for key, _, _ in VERBS:
            for sub in (rf"{root}\{key}\command", rf"{root}\{key}"):
                try:
                    winreg.DeleteKey(winreg.HKEY_CURRENT_USER, sub)
                except OSError:
                    pass


def _win_is_registered():
    try:
        import winreg
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{ROOTS[0]}\{VERBS[0][0]}"))
        return True
    except (OSError, ImportError):
        return False


# -------------------------------------------------------------------- Linux
def _nautilus_dir():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "nautilus", "scripts")


def _kde_file():
    return os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "kio", "servicemenus",
                        "lavati-compare.desktop")


def _linux_register():
    cmd = launcher()
    d = _nautilus_dir()
    os.makedirs(d, exist_ok=True)
    for key, label, flag in VERBS:
        p = os.path.join(d, label)
        with open(p, "w", encoding="utf-8") as f:
            f.write(f'#!/bin/sh\nfirst="$(printf "%s" "$NAUTILUS_SCRIPT_SELECTED_FILE_PATHS" | head -n 1)"\n'
                    f'[ -n "$first" ] || first="$1"\nexec {cmd} {flag} "$first"\n')
        os.chmod(p, os.stat(p).st_mode | stat.S_IXUSR | stat.S_IXGRP)
    k = _kde_file()
    os.makedirs(os.path.dirname(k), exist_ok=True)
    with open(k, "w", encoding="utf-8") as f:
        f.write("[Desktop Entry]\nType=Service\nServiceTypes=KonqPopupMenu/Plugin\nMimeType=all/all;inode/directory;\n"
                "Actions=lavatiSelect;lavatiCompare;\nX-KDE-Submenu=Lavati Compare\n\n"
                f"[Desktop Action lavatiSelect]\nName=Select as left side\nExec={cmd} --select %f\n\n"
                f"[Desktop Action lavatiCompare]\nName=Compare to the selected left side\nExec={cmd} --compare-with %f\n")


def _linux_unregister():
    for _, label, _ in VERBS:
        try:
            os.remove(os.path.join(_nautilus_dir(), label))
        except OSError:
            pass
    try:
        os.remove(_kde_file())
    except OSError:
        pass


def _linux_is_registered():
    return os.path.exists(_kde_file()) or os.path.exists(os.path.join(_nautilus_dir(), VERBS[0][1]))


# --------------------------------------------------------------------- API
def register():
    if platform_utils.IS_WIN:
        return _win_register()
    if platform_utils.IS_LINUX:
        return _linux_register()
    raise ShellError("Context-menu entries are not available on this system. " + description())


def unregister():
    if platform_utils.IS_WIN:
        return _win_unregister()
    if platform_utils.IS_LINUX:
        return _linux_unregister()


def is_registered():
    if platform_utils.IS_WIN:
        return _win_is_registered()
    if platform_utils.IS_LINUX:
        return _linux_is_registered()
    return False


def set_pending(path):
    os.makedirs(config.APP_DIR, exist_ok=True)
    with open(PENDING, "w", encoding="utf-8") as f:
        f.write(path)


def take_pending():
    try:
        with open(PENDING, "r", encoding="utf-8") as f:
            p = f.read().strip()
        os.remove(PENDING)
        return p if os.path.exists(p) else None
    except OSError:
        return None
