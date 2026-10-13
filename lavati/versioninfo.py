"""Read version resources and header facts from Windows PE files (EXE, DLL, SYS ...). Pure Python, no Qt."""
import struct
import time

MACHINES = {0x14c: "x86 (32-bit)", 0x8664: "x64 (AMD64)", 0x1c0: "ARM", 0x1c4: "ARM Thumb-2", 0xaa64: "ARM64",
            0x200: "Itanium", 0x5064: "RISC-V 64", 0x166: "MIPS", 0x1f0: "PowerPC"}
SUBSYSTEMS = {1: "Native", 2: "Windows GUI", 3: "Windows console", 5: "OS/2 console", 7: "POSIX console",
              9: "Windows CE GUI", 10: "EFI application", 11: "EFI boot driver", 12: "EFI runtime driver", 14: "Xbox"}
FILE_OS = {0x0: "Unknown", 0x1: "16-bit DOS", 0x2: "16-bit OS/2", 0x3: "32-bit OS/2", 0x4: "32-bit Windows NT",
           0x10000: "DOS", 0x20000: "OS/2 16-bit", 0x30000: "OS/2 32-bit", 0x40000: "Windows NT",
           0x10001: "16-bit Windows on DOS", 0x40004: "32-bit Windows NT"}
FILE_TYPE = {0: "Unknown", 1: "Application", 2: "DLL", 3: "Device driver", 4: "Font", 5: "Virtual device", 7: "Static library"}
FLAGS = [(0x1, "Debug build"), (0x2, "Pre-release"), (0x4, "Patched"), (0x8, "Private build"), (0x10, "Info inferred"),
         (0x20, "Special build")]


class PEError(Exception):
    pass


def _u16(d, o):
    return struct.unpack_from("<H", d, o)[0]


def _u32(d, o):
    return struct.unpack_from("<I", d, o)[0]


def _utf16z(d, o, end):
    """Read a NUL-terminated UTF-16LE string. Returns (text, offset after the terminator)."""
    p = o
    while p + 1 < end and d[p:p + 2] != b"\x00\x00":
        p += 2
    return d[o:p].decode("utf-16-le", "replace"), p + 2


def _align4(o):
    return (o + 3) & ~3


class PE:
    def __init__(self, data):
        self.d = data
        if len(data) < 0x40 or data[:2] != b"MZ":
            raise PEError("Not a Windows executable (missing MZ header)")
        self.pe = _u32(data, 0x3C)
        if self.pe + 24 > len(data) or data[self.pe:self.pe + 4] != b"PE\0\0":
            raise PEError("Not a PE file (missing PE signature)")
        c = self.pe + 4
        self.machine, self.nsec, self.timestamp = _u16(data, c), _u16(data, c + 2), _u32(data, c + 4)
        self.opt_size, self.chars = _u16(data, c + 16), _u16(data, c + 18)
        self.opt = c + 20
        self.magic = _u16(data, self.opt)
        if self.magic not in (0x10b, 0x20b):
            raise PEError("Unknown optional header format")
        self.plus = self.magic == 0x20b
        dd = self.opt + (112 if self.plus else 96)
        self.subsystem = _u16(data, self.opt + 68)
        self.entry = _u32(data, self.opt + 16)
        self.image_size = _u32(data, self.opt + 56)
        self.checksum = _u32(data, self.opt + 64)
        self.ndd = _u32(data, self.opt + (108 if self.plus else 92))
        self.dd = []
        for i in range(min(self.ndd, 16)):
            o = dd + 8 * i
            if o + 8 > len(data):
                break
            self.dd.append((_u32(data, o), _u32(data, o + 4)))
        st = self.opt + self.opt_size
        self.sections = []
        for i in range(self.nsec):
            o = st + 40 * i
            if o + 40 > len(data):
                break
            name = data[o:o + 8].rstrip(b"\0").decode("latin-1")
            vsize, va, rsize, rptr = struct.unpack_from("<IIII", data, o + 8)
            self.sections.append((name, vsize, va, rsize, rptr))

    def rva_to_off(self, rva):
        for name, vsize, va, rsize, rptr in self.sections:
            if va <= rva < va + max(vsize, rsize):
                return rptr + (rva - va)
        raise PEError("Resource address outside every section")

    def dir_entry(self, i):
        return self.dd[i] if i < len(self.dd) else (0, 0)

    def version_resource(self):
        """Bytes of the RT_VERSION resource, or None."""
        rva, size = self.dir_entry(2)
        if not rva:
            return None
        base = self.rva_to_off(rva)
        d = self.d

        def entries(off):
            nnamed, nid = _u16(d, off + 12), _u16(d, off + 14)
            out = []
            for i in range(nnamed + nid):
                o = off + 16 + 8 * i
                name, val = _u32(d, o), _u32(d, o + 4)
                out.append((name, val & 0x7FFFFFFF, bool(val & 0x80000000), i >= nnamed))
            return out
        for name, sub, is_dir, is_id in entries(base):
            if is_id and name == 16 and is_dir:                 # RT_VERSION
                for n2, sub2, dir2, _ in entries(base + sub):
                    if not dir2:
                        continue
                    for n3, sub3, dir3, _ in entries(base + sub2):
                        if dir3:
                            continue
                        eo = base + sub3
                        rva_d, sz = _u32(d, eo), _u32(d, eo + 4)
                        o = self.rva_to_off(rva_d)
                        return d[o:o + sz]
                    break
        return None


def _block(d, off, limit):
    """Parse the header of a version-info block. Returns dict or None."""
    if off + 6 > limit:
        return None
    length, vlen, typ = struct.unpack_from("<HHH", d, off)
    if length < 6 or off + length > limit + 0:
        length = min(length, limit - off)
    key, p = _utf16z(d, off + 6, off + length)
    val = _align4(p)
    return dict(off=off, length=length, vlen=vlen, type=typ, key=key, val=val, end=off + length)


def parse_version_resource(res):
    """-> dict(fixed=..., strings={table: {name: value}}, translations=[...])."""
    out = {"fixed": {}, "strings": {}, "translations": []}
    root = _block(res, 0, len(res))
    if not root or root["key"] != "VS_VERSION_INFO":
        raise PEError("Malformed version resource")
    p = root["val"]
    if root["vlen"] >= 52 and p + 52 <= len(res) and _u32(res, p) == 0xFEEF04BD:
        (sig, sv, fvms, fvls, pvms, pvls, fmask, fflags, fos, ftype, fsub, dms, dls) = struct.unpack_from("<13I", res, p)
        out["fixed"] = dict(
            file_version=f"{fvms >> 16}.{fvms & 0xffff}.{fvls >> 16}.{fvls & 0xffff}",
            product_version=f"{pvms >> 16}.{pvms & 0xffff}.{pvls >> 16}.{pvls & 0xffff}",
            flags=", ".join(n for bit, n in FLAGS if fflags & fmask & bit) or "None",
            os=FILE_OS.get(fos, f"0x{fos:x}"), type=FILE_TYPE.get(ftype, f"0x{ftype:x}"))
        p += root["vlen"]
    p = _align4(p)
    while p + 6 <= root["end"]:
        b = _block(res, p, root["end"])
        if not b or b["length"] <= 6:
            break
        if b["key"] == "StringFileInfo":
            q = b["val"]
            while q + 6 <= b["end"]:
                t = _block(res, q, b["end"])
                if not t or t["length"] <= 6:
                    break
                strings = {}
                s = t["val"]
                while s + 6 <= t["end"]:
                    e = _block(res, s, t["end"])
                    if not e or e["length"] <= 6:
                        break
                    value = ""
                    if e["vlen"]:
                        value = _utf16z(res, e["val"], min(e["end"], e["val"] + e["vlen"] * 2 + 2))[0]
                    strings[e["key"]] = value
                    s = _align4(e["end"])
                out["strings"][t["key"]] = strings
                q = _align4(t["end"])
        elif b["key"] == "VarFileInfo":
            q = b["val"]
            while q + 6 <= b["end"]:
                v = _block(res, q, b["end"])
                if not v or v["length"] <= 6:
                    break
                if v["key"] == "Translation":
                    for i in range(0, v["vlen"] // 4):
                        lang, cp = struct.unpack_from("<HH", res, v["val"] + 4 * i)
                        out["translations"].append(f"{lang:04X}{cp:04X}")
                q = _align4(v["end"])
        p = _align4(b["end"])
    return out


def read_info(path):
    """Everything we can say about a PE file as ordered (group, name, value) rows."""
    with open(path, "rb") as f:
        data = f.read()
    pe = PE(data)
    rows = []
    kind = "DLL" if pe.chars & 0x2000 else ("Driver" if pe.subsystem == 1 else "Executable")
    rows.append(("File", "Size", f"{len(data):,} bytes"))
    rows.append(("File", "Type", kind))
    rows.append(("File", "Machine", MACHINES.get(pe.machine, f"0x{pe.machine:04x}")))
    rows.append(("File", "Subsystem", SUBSYSTEMS.get(pe.subsystem, str(pe.subsystem))))
    rows.append(("File", "Compiled (header timestamp)", time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(pe.timestamp)) if pe.timestamp else "n/a"))
    rows.append(("File", "Sections", ", ".join(s[0] for s in pe.sections)))
    rows.append(("File", ".NET assembly", "Yes" if pe.dir_entry(14)[0] else "No"))
    rows.append(("File", "Digitally signed", "Yes" if pe.dir_entry(4)[1] else "No"))
    rows.append(("File", "ASLR (dynamic base)", "Yes" if _u16(data, pe.opt + 70) & 0x40 else "No"))
    rows.append(("File", "DEP (NX compatible)", "Yes" if _u16(data, pe.opt + 70) & 0x100 else "No"))
    try:
        res = pe.version_resource()
    except (PEError, struct.error):
        res = None
    if res is None:
        rows.append(("Version", "Version resource", "None"))
        return rows
    try:
        v = parse_version_resource(res)
    except (PEError, struct.error) as e:
        rows.append(("Version", "Version resource", f"Unreadable ({e})"))
        return rows
    f = v["fixed"]
    for key, label in (("file_version", "File version"), ("product_version", "Product version"), ("flags", "File flags"),
                       ("os", "Target OS"), ("type", "File type")):
        if key in f:
            rows.append(("Fixed version info", label, f[key]))
    if v["translations"]:
        rows.append(("Version", "Translations", ", ".join(v["translations"])))
    order = ["CompanyName", "FileDescription", "FileVersion", "InternalName", "LegalCopyright", "LegalTrademarks",
             "OriginalFilename", "ProductName", "ProductVersion", "Comments", "PrivateBuild", "SpecialBuild"]
    for table, strings in v["strings"].items():
        grp = "Strings" if len(v["strings"]) == 1 else f"Strings [{table}]"
        for k in order:
            if k in strings:
                rows.append((grp, k, strings[k]))
        for k, val in strings.items():
            if k not in order:
                rows.append((grp, k, val))
    return rows
