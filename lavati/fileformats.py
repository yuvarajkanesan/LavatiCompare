"""Per-file-type rules: grammar (language), unimportant text, replacements, converters. No Qt imports.

A format is a dict stored in the config under "file_formats":
    name, patterns ("*.log; *.txt"), language, ws, ignore_case, unimportant [regex], replacements [[regex, text]],
    converter ("" | "builtin" | "external"), command (external converter command line with {in} and {out}),
    encoding ("" = auto-detect)
"""
import fnmatch
import os
import shlex
import subprocess
import tempfile
from dataclasses import dataclass

from . import docs, engine, platform_utils, vfs

BUILTIN_FORMATS = [
    dict(name="Word document", patterns="*.docx; *.docm; *.dotx", converter="builtin"),
    dict(name="OpenDocument", patterns="*.odt; *.ods; *.odp; *.ott", converter="builtin"),
    dict(name="PowerPoint", patterns="*.pptx; *.pptm", converter="builtin"),
    dict(name="Rich Text", patterns="*.rtf", converter="builtin"),
    dict(name="PDF document", patterns="*.pdf", converter="builtin"),
    dict(name="Log file", patterns="*.log", unimportant=[engine.UNIMPORTANT_PRESETS["ISO timestamps (2024-01-31 12:00:00)"],
                                                          engine.UNIMPORTANT_PRESETS["Times (12:34:56)"]], converter=""),
]
DEFAULT_FORMAT = dict(name="", patterns="", language="", ws="", ignore_case=None, unimportant=[], replacements=[],
                      converter="", command="", encoding="")


def blank_format():
    return dict(DEFAULT_FORMAT, unimportant=[], replacements=[])


def _matches(fmt, path):
    name = os.path.basename(path).lower()
    for pat in (fmt.get("patterns") or "").replace(",", ";").split(";"):
        pat = pat.strip().lower()
        if pat and fnmatch.fnmatch(name, pat):
            return True
    return False


def all_formats(cfg):
    """User formats first (they win), then the built-in ones."""
    return list(cfg["file_formats"]) + [dict(blank_format(), **f) for f in BUILTIN_FORMATS]


def match_format(cfg, path):
    for f in all_formats(cfg):
        if _matches(f, path):
            return dict(blank_format(), **f)
    return None


def rules_for(base, fmt):
    """TextRules for one file: the global rules extended by the format's rules."""
    if not fmt:
        return base
    r = base.copy()
    if fmt.get("ws"):
        r.ws = fmt["ws"]
    if fmt.get("ignore_case") is not None:
        r.ignore_case = bool(fmt["ignore_case"])
    r.unimportant = list(base.unimportant) + [u for u in fmt.get("unimportant", []) if u not in base.unimportant]
    r.replacements = list(base.replacements) + [tuple(x) for x in fmt.get("replacements", [])]
    return r


_cache = {}


def run_external(command, path, timeout=90):
    """Run a user-defined converter. {in} is the source file, {out} a temp file (or stdout when absent)."""
    out_dir = tempfile.mkdtemp(dir=vfs.temp_dir())
    out = os.path.join(out_dir, "converted.txt")
    parts = shlex.split(command, posix=not platform_utils.IS_WIN)
    use_out = any("{out}" in p for p in parts)
    cmd = []
    for p in parts:
        p = p.strip('"') if platform_utils.IS_WIN else p
        cmd.append(p.replace("{in}", path).replace("{out}", out))
    if not cmd:
        raise docs.ExtractError("Empty converter command")
    try:
        res = subprocess.run(cmd, capture_output=True, timeout=timeout, shell=False)
    except FileNotFoundError as e:
        raise docs.ExtractError(f"Converter not found: {cmd[0]}") from e
    except subprocess.TimeoutExpired as e:
        raise docs.ExtractError(f"Converter timed out after {timeout}s") from e
    if res.returncode != 0:
        msg = (res.stderr or b"").decode("utf-8", "replace").strip()[:300]
        raise docs.ExtractError(f"Converter failed ({res.returncode}): {msg}")
    if use_out:
        with open(out, "rb") as f:
            return f.read()
    return res.stdout


@dataclass
class Prepared:
    lines: list
    encoding: str
    eol: str
    mixed_eol: bool
    mtime: float
    readonly: bool = False
    fmt: dict = None
    note: str = ""
    final_newline: bool = True


def prepare(path, cfg, encoding=None, use_formats=True):
    """Load a file for Text Compare, converting it first when its file format asks for it."""
    fmt = match_format(cfg, path) if use_formats and path else None
    if not path or not os.path.exists(path):
        return Prepared([], "UTF-8", "LF", False, None, False, fmt, "")
    forced = encoding or (fmt or {}).get("encoding") or None
    conv = (fmt or {}).get("converter", "")
    if conv in ("builtin", "external"):
        st = os.stat(path)
        key = (path, st.st_mtime, st.st_size, conv, (fmt or {}).get("command", ""))
        data = _cache.get(key)
        if data is None:
            if conv == "builtin":
                text = docs.extract(path)
                raw = text.encode("utf-8")
            else:
                raw = run_external(fmt["command"], path)
            _cache[key] = data = raw
            if len(_cache) > 6:
                _cache.pop(next(iter(_cache)))
        tf = engine.load_text_file(path, forced or ("UTF-8" if conv == "builtin" else None), raw=data)
        what = "converted text" if conv == "builtin" else "output of external converter"
        return Prepared(tf.lines, tf.encoding, tf.eol, tf.mixed_eol, tf.mtime, True, fmt,
                        f"Showing {what} – read-only", tf.final_newline)
    tf = engine.load_text_file(path, forced)
    return Prepared(tf.lines, tf.encoding, tf.eol, tf.mixed_eol, tf.mtime, False, fmt, "", tf.final_newline)
