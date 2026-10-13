"""Windows registry comparison: .reg files and (on Windows) the live registry. No Qt imports.

A registry snapshot is {key_path: {value_name: (type, text)}}; key paths use backslashes and compare
case-insensitively. The default value of a key is stored under the name "@".
"""
import re

from . import engine

ROOTS = {"HKCR": "HKEY_CLASSES_ROOT", "HKCU": "HKEY_CURRENT_USER", "HKLM": "HKEY_LOCAL_MACHINE",
         "HKU": "HKEY_USERS", "HKCC": "HKEY_CURRENT_CONFIG"}
TYPE_NAMES = {0: "REG_NONE", 1: "REG_SZ", 2: "REG_EXPAND_SZ", 3: "REG_BINARY", 4: "REG_DWORD", 5: "REG_DWORD_BIG_ENDIAN",
              6: "REG_LINK", 7: "REG_MULTI_SZ", 8: "REG_RESOURCE_LIST", 9: "REG_FULL_RESOURCE_DESCRIPTOR",
              10: "REG_RESOURCE_REQUIREMENTS_LIST", 11: "REG_QWORD"}
MAX_KEYS = 250_000


class RegError(Exception):
    pass


def normalize_root(path):
    """'HKCU\\Software\\X' -> 'HKEY_CURRENT_USER\\Software\\X'."""
    path = path.strip().strip("\\")
    head, _, rest = path.partition("\\")
    head = ROOTS.get(head.upper(), head.upper())
    return head + ("\\" + rest if rest else "")


def is_registry_spec(spec):
    s = (spec or "").strip()
    if s.lower().startswith("registry:"):
        return True
    return bool(re.match(r"(?i)^(HKEY_[A-Z_]+|HKCR|HKCU|HKLM|HKU|HKCC)(\\|$)", s))


def format_value(rtype, data):
    """Human-readable text for a value of the given REG_* type number."""
    if rtype in (1, 2, 6):
        return data if isinstance(data, str) else data.decode("utf-16-le", "replace").rstrip("\0")
    if rtype == 4:
        n = data if isinstance(data, int) else int.from_bytes(data[:4], "little")
        return f"0x{n:08x} ({n})"
    if rtype == 5:
        n = int.from_bytes(data[:4], "big")
        return f"0x{n:08x} ({n})"
    if rtype == 11:
        n = data if isinstance(data, int) else int.from_bytes(data[:8], "little")
        return f"0x{n:016x} ({n})"
    if rtype == 7:
        if isinstance(data, (list, tuple)):
            return " | ".join(data)
        s = data.decode("utf-16-le", "replace")
        return " | ".join(p for p in s.split("\0") if p)
    b = bytes(data) if not isinstance(data, bytes) else data
    hexs = " ".join(f"{x:02x}" for x in b[:128])
    return hexs + (f" … ({len(b)} bytes)" if len(b) > 128 else "")


# ================================================================== .reg files
def _decode_reg(raw):
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        return raw.decode("utf-16")
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1252", "replace")


def _unescape(s):
    return re.sub(r"\\(.)", r"\1", s)


def parse_reg(text):
    """Parse the text of a .reg file. Returns {key: {name: (type_name, value_text)}}."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    out = {}
    key = None
    i = 0
    if lines and lines[0].startswith("﻿"):
        lines[0] = lines[0][1:]
    while i < len(lines):
        line = lines[i].strip()
        i += 1
        if not line or line.startswith(";") or line.startswith("Windows Registry Editor") or line == "REGEDIT4":
            continue
        if line.startswith("[") and line.endswith("]"):
            name = line[1:-1]
            if name.startswith("-"):                    # key deletion marker
                key = None
                continue
            key = normalize_root(name)
            out.setdefault(key, {})
            continue
        if key is None:
            continue
        while line.endswith("\\") and i < len(lines):          # hex values continue on the next line
            line = line[:-1] + lines[i].strip()
            i += 1
        if line.startswith("@="):
            name, rest = "@", line[2:]
        elif line.startswith('"'):
            m = re.match(r'"((?:\\.|[^"\\])*)"=(.*)$', line)
            if not m:
                continue
            name, rest = _unescape(m.group(1)), m.group(2)
        else:
            continue
        out[key][name] = _parse_value(rest.strip())
    return out


def _parse_value(rest):
    if rest.startswith('"'):
        return ("REG_SZ", _unescape(rest[1:-1] if rest.endswith('"') else rest[1:]))
    if rest.startswith("dword:"):
        n = int(rest[6:].strip() or "0", 16)
        return ("REG_DWORD", f"0x{n:08x} ({n})")
    if rest == "-":
        return ("(delete)", "")
    m = re.match(r"hex(?:\(([0-9a-fA-F]+)\))?:(.*)$", rest)
    if m:
        t = int(m.group(1), 16) if m.group(1) else 3
        hexs = [h for h in m.group(2).replace(" ", "").split(",") if h]
        try:
            data = bytes(int(h, 16) for h in hexs)
        except ValueError:
            data = b""
        return (TYPE_NAMES.get(t, f"REG_TYPE_{t}"), format_value(t, data))
    return ("REG_SZ", rest)


def load_reg_file(path):
    with open(path, "rb") as f:
        return parse_reg(_decode_reg(f.read()))


# ================================================================ live registry
def load_live(spec, ctx=None):
    """Read a registry branch with winreg (Windows only)."""
    try:
        import winreg
    except ImportError as e:
        raise RegError("The live registry is only available on Windows") from e
    spec = spec.strip()
    if spec.lower().startswith("registry:"):
        spec = spec[9:]
    path = normalize_root(spec)
    root_name, _, sub = path.partition("\\")
    root = getattr(winreg, root_name, None)
    if root is None:
        raise RegError(f"Unknown registry root: {root_name}")
    out = {}

    def walk(hkey, p, depth):
        if len(out) > MAX_KEYS:
            raise RegError(f"More than {MAX_KEYS:,} keys - choose a smaller branch")
        if ctx is not None and ctx.cancel:
            raise engine.Cancelled()
        vals = {}
        i = 0
        while True:
            try:
                name, data, typ = winreg.EnumValue(hkey, i)
            except OSError:
                break
            i += 1
            vals[name or "@"] = (TYPE_NAMES.get(typ, f"REG_TYPE_{typ}"), format_value(typ, data))
        out[p] = vals
        i = 0
        while True:
            try:
                child = winreg.EnumKey(hkey, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(hkey, child, 0, winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as ck:
                    walk(ck, p + "\\" + child, depth + 1)
            except OSError:
                out[p + "\\" + child] = {"(access denied)": ("", "")}
    try:
        with winreg.OpenKey(root, sub, 0, winreg.KEY_READ | getattr(winreg, "KEY_WOW64_64KEY", 0)) as hk:
            walk(hk, path, 0)
    except FileNotFoundError as e:
        raise RegError(f"Registry key not found: {path}") from e
    except PermissionError as e:
        raise RegError(f"Access denied: {path}") from e
    return out


def load(spec, ctx=None):
    if is_registry_spec(spec):
        return load_live(spec, ctx)
    return load_reg_file(spec)


# ================================================================== comparison
def _common_prefix(keys):
    keys = list(keys)
    if not keys:
        return ""
    parts = [k.split("\\") for k in keys]
    pre = parts[0]
    for p in parts[1:]:
        n = 0
        while n < min(len(pre), len(p)) and pre[n].lower() == p[n].lower():
            n += 1
        pre = pre[:n]
    return "\\".join(pre)


def _rel_map(reg):
    """(branch root path, {relative key: (full path, values)})."""
    if not reg:
        return "", {}
    base = min(reg, key=lambda k: k.count("\\"))
    root = base if all(k.lower() == base.lower() or k.lower().startswith(base.lower() + "\\") for k in reg) else _common_prefix(reg)
    out = {}
    for k, v in reg.items():
        out[k[len(root):].strip("\\")] = (k, v)
    return root, out


def compare_registry(left, right, ctx=None):
    """-> [root Node]: registry keys become folders and values become files.

    Key paths are taken relative to each side's branch root, so two exports of the same branch line up even if
    they were taken from different places.
    """
    lroot, lm = _rel_map(left)
    rroot, rm = _rel_map(right)
    lcase = {k.lower(): k for k in lm}
    rcase = {k.lower(): k for k in rm}
    index = {}

    def node_for(rel_lower, display):
        n = index.get(rel_lower)
        if n is not None:
            return n
        if rel_lower == "":
            n = engine.Node(name=(lroot or rroot).rsplit("\\", 1)[-1] or "(registry)", rel="", is_dir=True)
        else:
            pl, _, _ = rel_lower.rpartition("\\")
            pd, _, nd = display.rpartition("\\")
            n = engine.Node(name=nd or display, rel=rel_lower, is_dir=True)
            node_for(pl, pd).children.append(n)
        index[rel_lower] = n
        return n
    node_for("", "")
    for kl in sorted(set(lcase) | set(rcase), key=lambda k: (k.count("\\"), k)):
        if ctx is not None and ctx.cancel:
            raise engine.Cancelled()
        ld, rd = lcase.get(kl), rcase.get(kl)
        display = ld if ld is not None else rd
        node = node_for(kl, display)
        lfull, lv = lm[ld] if ld is not None else (None, None)
        rfull, rv = rm[rd] if rd is not None else (None, None)
        if lfull is not None:
            node.left = {"size": 0, "mtime": 0, "path": lfull}
        if rfull is not None:
            node.right = {"size": 0, "mtime": 0, "path": rfull}
        lvl = {k.lower(): (k, v) for k, v in (lv or {}).items()}
        rvl = {k.lower(): (k, v) for k, v in (rv or {}).items()}
        order = sorted(set(lvl) | set(rvl), key=lambda x: (x != "@", x))
        for nl in order:
            a, b = lvl.get(nl), rvl.get(nl)
            leaf = engine.Node(name=(a or b)[0], rel=(kl + "\\" + nl) if kl else nl, is_dir=False)
            leaf.left = {"size": 0, "mtime": 0, "path": "", "type": a[1][0], "data": a[1][1]} if a else None
            leaf.right = {"size": 0, "mtime": 0, "path": "", "type": b[1][0], "data": b[1][1]} if b else None
            if a and not b:
                leaf.status = "left"
            elif b and not a:
                leaf.status = "right"
            else:
                leaf.status = "same" if a[1] == b[1] else "changed"
            node.children.append(leaf)

    def settle(n):
        has_l = has_r = False
        for c in n.children:
            if c.is_dir:
                l, r = settle(c)
            else:
                l, r = c.left is not None, c.right is not None
            has_l |= l
            has_r |= r
        has_l |= n.left is not None
        has_r |= n.right is not None
        if has_l and n.left is None:
            n.left = {"size": 0, "mtime": 0, "path": ""}        # implied by descendants
        if has_r and n.right is None:
            n.right = {"size": 0, "mtime": 0, "path": ""}
        if n.left and not n.right:
            n.status = "left"
        elif n.right and not n.left:
            n.status = "right"
        else:
            n.status = "same" if all(c.status == "same" for c in n.children) else "changed"
        # folders sort before nothing special: values first (like regedit), then sub keys
        n.children.sort(key=lambda c: (c.is_dir, c.name.lower() if c.name != "@" else ""))
        return has_l, has_r
    settle(index[""])
    return [index[""]]


def count_values(nodes):
    c = {}
    for n in engine.walk(nodes):
        if not n.is_dir:
            c[n.status] = c.get(n.status, 0) + 1
    return c
