"""Two- and three-way folder merge engine. No Qt imports."""
import os
import tempfile
from dataclasses import dataclass

from . import engine, vfs


@dataclass
class MergeEntry:
    rel: str
    ours: object = None          # vfs.Entry or None
    base: object = None
    theirs: object = None
    opath: str = None            # path inside its file system
    bpath: str = None
    tpath: str = None
    otoken: str = None           # content identity ("<size>:<crc32>")
    btoken: str = None
    ttoken: str = None
    action: str = "keep"         # keep | ours | theirs | delete | merged | conflict
    why: str = ""
    resolution: str = None       # user choice: ours | theirs | base | delete | merged | skip
    merged_path: str = None      # temp file with merged text (auto or hand-made)
    text_ok: bool = False        # all present sides look like text files

    def effective(self):
        return self.resolution or (None if self.action == "conflict" else self.action)

    def unresolved(self):
        return self.effective() is None

    @property
    def three_way(self):
        return self.btoken is not None or self.base is not None


class Sides3:
    def __init__(self, ofs, oroot, bfs, broot, tfs, troot):
        self.ofs, self.oroot, self.bfs, self.broot, self.tfs, self.troot = ofs, oroot, bfs, broot, tfs, troot

    def fs(self, which):
        return {"ours": self.ofs, "base": self.bfs, "theirs": self.tfs}[which]

    def root(self, which):
        return {"ours": self.oroot, "base": self.broot, "theirs": self.troot}[which]


def _walk(fs, root, patterns, rel="", out=None):
    out = {} if out is None else out
    if fs is None:
        return out
    for e in fs.scandir(root):
        if engine.is_ignored(e.name, e.is_dir, patterns):
            continue
        r = f"{rel}/{e.name}" if rel else e.name
        p = fs.join(root, e.name)
        if e.is_dir:
            _walk(fs, p, patterns, r, out)
        else:
            out[r] = (e, p)
    return out


def _token(fs, path, entry, ctx):
    crc = fs.checksum(path, "crc32", ctx)
    return f"{entry.size}:{crc}" if crc else f"{entry.size}:{int(entry.mtime)}"


def _looks_text(fs, path, entry):
    if entry.size > 20 * 1024 * 1024:
        return False
    with fs.open(path) as f:
        return not engine.looks_binary(f.read(8192))


def scan(S, patterns=(), ctx=None, case_insensitive=False):
    """Classify every file. Returns a list of MergeEntry sorted by path."""
    O = _walk(S.ofs, S.oroot, patterns)
    B = _walk(S.bfs, S.broot, patterns) if S.bfs is not None else None
    T = _walk(S.tfs, S.troot, patterns)
    norm = (lambda s: s.lower()) if case_insensitive else (lambda s: s)
    keys = {}
    for d in (O, B or {}, T):
        for r in d:
            keys.setdefault(norm(r), r)
    omap = {norm(r): v for r, v in O.items()}
    bmap = {norm(r): v for r, v in (B or {}).items()}
    tmap = {norm(r): v for r, v in T.items()}
    entries = []
    for k in sorted(keys, key=str.lower):
        if ctx is not None:
            if ctx.cancel:
                raise engine.Cancelled()
            ctx.count += 1
        e = MergeEntry(rel=keys[k])
        for which, mp, fs in (("o", omap, S.ofs), ("b", bmap, S.bfs), ("t", tmap, S.tfs)):
            hit = mp.get(k)
            if hit:
                ent, path = hit
                setattr(e, {"o": "ours", "b": "base", "t": "theirs"}[which], ent)
                setattr(e, which + "path", path)
                setattr(e, which + "token", _token(fs, path, ent, ctx))
        if B is not None:
            e.action, e.why = engine.classify3(e.otoken, e.btoken, e.ttoken)
        else:
            if e.otoken == e.ttoken:
                e.action, e.why = ("keep" if e.otoken is not None else "delete"), "same on both sides"
            elif e.otoken is None:
                e.action, e.why = "theirs", "only in theirs"
            elif e.ttoken is None:
                e.action, e.why = "ours", "only in ours"
            else:
                e.action, e.why = "conflict", "different in each folder"
        if e.action == "conflict":
            present = [(S.ofs, e.opath, e.ours), (S.bfs, e.bpath, e.base), (S.tfs, e.tpath, e.theirs)]
            try:
                e.text_ok = bool(all(_looks_text(fs, p, en) for fs, p, en in present if en is not None) and e.ours and e.theirs)
            except OSError:
                e.text_ok = False
            if B is not None and e.text_ok and e.base is not None:
                try_auto_merge(S, e)
        entries.append(e)
    return entries


def _read_lines(fs, path):
    with fs.open(path) as f:
        text, enc, eol = engine.decode_bytes(f.read())
    return engine.split_lines(text), enc, eol


def try_auto_merge(S, e):
    """If the three versions merge without conflicts, store the result in a temp file."""
    try:
        o, enc, eol = _read_lines(S.ofs, e.opath)
        b, _, _ = _read_lines(S.bfs, e.bpath)
        t, _, _ = _read_lines(S.tfs, e.tpath)
    except OSError:
        return False
    segs = engine.merge3(o, b, t)
    if any(s.kind == "conflict" for s in segs):
        return False
    lines = [l for s in segs for l in engine.segment_output(s)]
    d = tempfile.mkdtemp(dir=vfs.temp_dir())
    path = os.path.join(d, os.path.basename(e.rel))
    engine.write_text(path, lines, enc, eol)
    e.merged_path, e.action, e.why = path, "merged", "text changes merged automatically"
    return True


def pick_newer(e):
    if e.ours is not None and e.theirs is not None:
        return "ours" if e.ours.mtime >= e.theirs.mtime else "theirs"
    return "ours" if e.ours is not None else ("theirs" if e.theirs is not None else None)


def summary(entries):
    c = {"keep": 0, "ours": 0, "theirs": 0, "delete": 0, "merged": 0, "conflict": 0}
    for e in entries:
        eff = e.effective()
        if eff is None:
            c["conflict"] += 1
        elif eff in c:
            c[eff] += 1
        else:
            c["keep"] += 1
    return c


def write_output(entries, S, out_fs, out_root, ctx=None):
    """Create the merged tree. Returns (written, errors). Unresolved entries abort with an error."""
    open_ = [e for e in entries if e.unresolved()]
    if open_:
        return 0, [f"{len(open_)} conflict(s) still need a decision"]
    done, errors = 0, []
    for e in entries:
        if ctx is not None and ctx.cancel:
            break
        res = e.effective()
        if res == "skip":
            continue
        dst = out_fs.join(out_root, e.rel)
        try:
            if res == "delete":
                if out_fs.exists(dst):
                    out_fs.remove(dst, trash=False)
                    done += 1
                continue
            if res == "merged":
                vfs.transfer_file(vfs.LocalFS(), e.merged_path, out_fs, dst, None)
            else:
                which = {"keep": "ours", "ours": "ours", "theirs": "theirs", "base": "base"}[res]
                if res == "keep" and e.ours is None:
                    which = "theirs"
                fs, path, ent = S.fs(which), getattr(e, which[0] + "path"), getattr(e, which)
                if ent is None:                      # the chosen side deleted this file
                    if out_fs.exists(dst):
                        out_fs.remove(dst, trash=False)
                        done += 1
                    continue
                if fs is out_fs and path == dst:
                    continue                 # merging in place: this side already holds the content
                vfs.transfer_file(fs, path, out_fs, dst, ent.mtime)
            done += 1
        except (OSError, vfs.VFSError) as ex:
            errors.append(f"{e.rel}: {ex}")
    return done, errors
