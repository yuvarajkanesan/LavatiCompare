"""Virtual file systems: local folders, archives (zip/tar/gz/jar...), snapshots.

Remote back-ends (FTP, SFTP, WebDAV, S3, Dropbox, OneDrive, Subversion) live in remote.py and are
created through open_location(). Paths inside a VFS always use "/" as separator.
No Qt imports here.
"""
import atexit
import gzip
import hashlib
import json
import os
import shutil
import tarfile
import tempfile
import time
import zipfile
import zlib
from dataclasses import dataclass

from . import platform_utils

ARCHIVE_EXTS = (".zip", ".jar", ".war", ".ear", ".aar", ".apk", ".whl", ".nupkg", ".vsix",
                ".tar", ".tgz", ".tar.gz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz", ".gz")
SNAPSHOT_EXT = ".lsnap"
CHUNK = 1024 * 1024
MAX_EXTRACT_BYTES = 4 * 1024 ** 3          # refuse to extract a single member bigger than this
_temp_root = None


def temp_dir():
    """Per-process scratch folder for downloaded / extracted files (removed on exit)."""
    global _temp_root
    if _temp_root is None or not os.path.isdir(_temp_root):
        _temp_root = tempfile.mkdtemp(prefix="lavati-")
        atexit.register(lambda: shutil.rmtree(_temp_root, ignore_errors=True))
    return _temp_root


def new_temp_path(name="file"):
    d = tempfile.mkdtemp(dir=temp_dir())
    return os.path.join(d, os.path.basename(name) or "file")


def is_archive_name(name):
    n = name.lower()
    return any(n.endswith(e) for e in ARCHIVE_EXTS)


def is_archive_file(path):
    return os.path.isfile(path) and is_archive_name(path)


class VFSError(OSError):
    pass


@dataclass
class Entry:
    name: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0


class VFS:
    """Interface + shared helpers. Subclasses override the primitives."""
    scheme = "file"
    readonly = False
    has_content = True            # False for snapshots (metadata only)
    slow = False                  # remote: content comparisons are expensive
    cheap_algo = None             # checksum algorithm available without reading the file (e.g. "md5")

    def __init__(self, root=""):
        self.root = root

    # --- naming
    def join(self, base, rel):
        if not rel:
            return base
        if not base:
            return rel
        return base.rstrip("/") + "/" + rel.lstrip("/")

    def parent(self, path):
        return path.rsplit("/", 1)[0] if "/" in path else ""

    def basename(self, path):
        return path.rstrip("/").rsplit("/", 1)[-1]

    def display(self, path):
        return path

    # --- reading
    def scandir(self, path):
        raise NotImplementedError

    def stat(self, path):
        raise NotImplementedError

    def exists(self, path):
        return self.stat(path) is not None

    def open(self, path):
        raise NotImplementedError

    def local_path(self, path):
        """A real local file with this content (downloaded/extracted for non-local back-ends)."""
        dst = new_temp_path(self.basename(path))
        with self.open(path) as src, open(dst, "wb") as out:
            shutil.copyfileobj(src, out, CHUNK)
        st = self.stat(path)
        if st and st.mtime:
            try:
                os.utime(dst, (st.mtime, st.mtime))
            except OSError:
                pass
        return dst

    def checksum(self, path, algo="crc32", ctx=None):
        """Hex digest of the file; None if this back-end can't provide one."""
        if algo == "git":                      # git blob id: sha1("blob <size>\0" + content)
            st = self.stat(path)
            h = hashlib.sha1(b"blob %d\0" % (st.size if st else 0))
            f = self.open(path)
            try:
                for b in iter(lambda: f.read(CHUNK), b""):
                    h.update(b)
            finally:
                f.close()
            return h.hexdigest()
        return stream_checksum(self.open(path), algo, ctx)

    # --- writing (read-only back-ends raise)
    def _ro(self):
        raise VFSError(f"{self.scheme}: this location is read-only")

    def makedirs(self, path):
        self._ro()

    def put_file(self, local, path, mtime=None):
        self._ro()

    def remove(self, path, trash=False):
        self._ro()

    def rename(self, path, new_path):
        self._ro()

    def set_mtime(self, path, mtime):
        self._ro()

    def close(self):
        pass


def stream_checksum(f, algo="crc32", ctx=None):
    try:
        if algo == "crc32":
            crc = 0
            while True:
                b = f.read(CHUNK)
                if not b:
                    break
                crc = zlib.crc32(b, crc)
                if ctx is not None and ctx.cancel:
                    raise_cancel()
            return f"{crc & 0xffffffff:08x}"
        h = hashlib.new(algo)
        while True:
            b = f.read(CHUNK)
            if not b:
                break
            h.update(b)
            if ctx is not None and ctx.cancel:
                raise_cancel()
        return h.hexdigest()
    finally:
        try:
            f.close()
        except Exception:
            pass


def raise_cancel():
    from .engine import Cancelled
    raise Cancelled()


# ====================================================================== local
class LocalFS(VFS):
    scheme = "file"

    def join(self, base, rel):
        if not rel:
            return base
        return os.path.join(base, *rel.split("/")) if base else os.path.join(*rel.split("/"))

    def parent(self, path):
        return os.path.dirname(path)

    def basename(self, path):
        return os.path.basename(path.rstrip("\\/"))

    def scandir(self, path):
        out = []
        try:
            with os.scandir(path) as it:
                for e in it:
                    try:
                        d = e.is_dir(follow_symlinks=False)
                        st = e.stat()
                        out.append(Entry(e.name, d, 0 if d else st.st_size, st.st_mtime))
                    except OSError:
                        continue
        except OSError:
            pass
        return out

    def stat(self, path):
        try:
            st = os.stat(path)
        except OSError:
            return None
        import stat as _s
        d = _s.S_ISDIR(st.st_mode)
        return Entry(self.basename(path), d, 0 if d else st.st_size, st.st_mtime)

    def open(self, path):
        return open(path, "rb")

    def local_path(self, path):
        return path

    def makedirs(self, path):
        os.makedirs(path, exist_ok=True)

    def put_file(self, local, path, mtime=None):
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = path + ".lavati-tmp"
        try:
            shutil.copyfile(local, tmp)
            if mtime is None:
                shutil.copystat(local, tmp)
            else:
                os.utime(tmp, (mtime, mtime))
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass

    def remove(self, path, trash=False):
        if trash:
            if not platform_utils.move_to_trash(path):
                raise OSError("Could not move to the Recycle Bin / Trash")
        elif not platform_utils.delete_permanently(path):
            raise OSError(f"Could not delete {path}")

    def rename(self, path, new_path):
        os.makedirs(os.path.dirname(new_path) or ".", exist_ok=True)
        shutil.move(path, new_path)

    def set_mtime(self, path, mtime):
        st = os.stat(path)
        os.utime(path, (st.st_atime, mtime))


# ===================================================================== archive
class ArchiveFS(VFS):
    """Read-only view of a zip/tar/gz archive as a folder tree. Root path is ''."""
    scheme = "archive"
    readonly = True

    def __init__(self, archive_path):
        super().__init__("")
        self.archive_path = archive_path
        self._tree = {"": {}}          # dir path -> {name: Entry}
        self._members = {}             # file path -> member handle
        self._zip = self._tar = None
        self._gz_name = None
        self._extracted = {}
        self._open()

    def display(self, path):
        return f"{self.archive_path}!/{path}" if path else self.archive_path

    def _add(self, path, is_dir, size, mtime, member=None):
        path = path.strip("/")
        if not path:
            return
        parts = path.split("/")
        for i in range(len(parts) - 1):
            d = "/".join(parts[:i + 1])
            parent = "/".join(parts[:i])
            self._tree.setdefault(parent, {}).setdefault(parts[i], Entry(parts[i], True, 0, mtime))
            self._tree.setdefault(d, {})
        parent = "/".join(parts[:-1])
        ent = Entry(parts[-1], is_dir, 0 if is_dir else size, mtime)
        self._tree.setdefault(parent, {})[parts[-1]] = ent
        if is_dir:
            self._tree.setdefault(path, {})
        else:
            self._members[path] = member

    def _open(self):
        p = self.archive_path
        low = p.lower()
        try:
            if zipfile.is_zipfile(p):
                self._zip = zipfile.ZipFile(p)
                for zi in self._zip.infolist():
                    try:
                        mt = time.mktime(zi.date_time + (0, 0, -1))
                    except (OverflowError, ValueError):
                        mt = 0.0
                    self._add(zi.filename, zi.is_dir(), zi.file_size, mt, zi)
            elif tarfile.is_tarfile(p):
                self._tar = tarfile.open(p)
                for m in self._tar.getmembers():
                    if m.isdir() or m.isfile():
                        self._add(m.name[2:] if m.name.startswith("./") else m.name, m.isdir(), m.size, m.mtime, m)
            elif low.endswith((".gz", ".gzip")):
                name = os.path.basename(p)
                name = name[:-3] if low.endswith(".gz") else name[:-5]
                self._gz_name = name or "data"
                size = 0
                try:
                    with open(p, "rb") as f:
                        f.seek(-4, os.SEEK_END)
                        size = int.from_bytes(f.read(4), "little")
                except OSError:
                    pass
                self._add(self._gz_name, False, size, os.path.getmtime(p), "gz")
            else:
                raise VFSError(f"Not a supported archive: {p}")
        except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError) as e:
            raise VFSError(f"Cannot read archive {p}: {e}") from e

    def scandir(self, path):
        return list(self._tree.get(path.strip("/"), {}).values())

    def stat(self, path):
        path = path.strip("/")
        if path == "":
            return Entry(os.path.basename(self.archive_path), True, 0, os.path.getmtime(self.archive_path))
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        return self._tree.get(parent, {}).get(path.rsplit("/", 1)[-1])

    def open(self, path):
        path = path.strip("/")
        m = self._members.get(path)
        if m is None:
            raise VFSError(f"{path}: not in archive")
        if self._zip is not None:
            return self._zip.open(m)
        if self._tar is not None:
            f = self._tar.extractfile(m)
            if f is None:
                raise VFSError(f"{path}: cannot read")
            return f
        return gzip.open(self.archive_path, "rb")

    def local_path(self, path):
        path = path.strip("/")
        if path in self._extracted and os.path.exists(self._extracted[path]):
            return self._extracted[path]
        ent = self.stat(path)
        if ent is None or ent.is_dir:
            raise VFSError(f"{path}: not a file in the archive")
        if ent.size > MAX_EXTRACT_BYTES:
            raise VFSError(f"{path}: too large to extract")
        dst = new_temp_path(self.basename(path))
        with self.open(path) as src, open(dst, "wb") as out:
            shutil.copyfileobj(src, out, CHUNK)
        if ent.mtime:
            try:
                os.utime(dst, (ent.mtime, ent.mtime))
            except OSError:
                pass
        self._extracted[path] = dst
        return dst

    def close(self):
        for h in (self._zip, self._tar):
            if h is not None:
                try:
                    h.close()
                except Exception:
                    pass


# ==================================================================== snapshot
class SnapshotFS(VFS):
    """A saved file listing (names, sizes, times and optionally CRC32). Metadata only."""
    scheme = "snapshot"
    readonly = True
    has_content = False

    def __init__(self, path=None, data=None):
        super().__init__("")
        if data is None:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        if data.get("format") != "lavati-snapshot":
            raise VFSError("Not a Lavati snapshot file")
        self.path = path
        self.meta = {k: data.get(k) for k in ("source", "created", "version")}
        self._tree = {"": {}}
        self._crc = {}
        for e in data.get("entries", []):
            p = e["p"].strip("/")
            parts = p.split("/")
            for i in range(len(parts) - 1):
                self._tree.setdefault("/".join(parts[:i]), {}).setdefault(parts[i], Entry(parts[i], True, 0, 0.0))
                self._tree.setdefault("/".join(parts[:i + 1]), {})
            parent = "/".join(parts[:-1])
            ent = Entry(parts[-1], bool(e.get("d")), 0 if e.get("d") else int(e.get("s", 0)), float(e.get("m", 0)))
            self._tree.setdefault(parent, {})[parts[-1]] = ent
            if ent.is_dir:
                self._tree.setdefault(p, {})
            elif e.get("c"):
                self._crc[p] = e["c"]

    def display(self, path):
        return f"snapshot:{os.path.basename(self.path or 'snapshot')}!/{path}" if path else f"snapshot:{self.path}"

    def scandir(self, path):
        return list(self._tree.get(path.strip("/"), {}).values())

    def stat(self, path):
        path = path.strip("/")
        if not path:
            return Entry("snapshot", True, 0, 0.0)
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        return self._tree.get(parent, {}).get(path.rsplit("/", 1)[-1])

    def open(self, path):
        raise VFSError("A snapshot stores no file content")

    def local_path(self, path):
        raise VFSError("A snapshot stores no file content")

    def checksum(self, path, algo="crc32", ctx=None):
        return self._crc.get(path.strip("/")) if algo == "crc32" else None


def capture_snapshot(fs, root, out_path, patterns=(), with_crc=True, ctx=None):
    """Walk `root` on `fs` and write a snapshot file. Returns the number of entries."""
    from .engine import is_ignored
    entries = []

    def walk(path, rel):
        for e in sorted(fs.scandir(path), key=lambda x: x.name.lower()):
            if ctx is not None and ctx.cancel:
                raise_cancel()
            if is_ignored(e.name, e.is_dir, patterns):
                continue
            r = f"{rel}/{e.name}" if rel else e.name
            full = fs.join(path, e.name)
            if e.is_dir:
                entries.append({"p": r, "d": 1, "m": e.mtime})
                walk(full, r)
            else:
                item = {"p": r, "s": e.size, "m": e.mtime}
                if with_crc and fs.has_content:
                    try:
                        item["c"] = fs.checksum(full, "crc32", ctx)
                    except OSError:
                        pass
                entries.append(item)
                if ctx is not None:
                    ctx.count += 1
    walk(root, "")
    data = {"format": "lavati-snapshot", "version": 1, "source": fs.display(root),
            "created": time.time(), "entries": entries}
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f)
    return len(entries)


# ================================================================= factory
def open_location(spec, password=None):
    """Return (fs, root) for a path/URL typed by the user.

    - plain folder            -> LocalFS
    - archive file            -> ArchiveFS
    - .lsnap file             -> SnapshotFS
    - ftp:// sftp:// dav:// s3:// dropbox:// onedrive:// svn:// ... -> remote.py
    """
    spec = (spec or "").strip().strip('"')
    if spec.lower().startswith("git:") and "#" in spec:
        from . import gitvfs
        return gitvfs.open_git(spec)
    if "://" in spec and not (len(spec) > 1 and spec[1] == ":"):
        from . import remote
        return remote.open_url(spec, password=password)
    if spec.lower().startswith("snapshot:"):
        spec = spec[9:]
    if os.path.isfile(spec):
        if spec.lower().endswith(SNAPSHOT_EXT):
            return SnapshotFS(spec), ""
        if is_archive_file(spec):
            return ArchiveFS(spec), ""
    return LocalFS(spec), spec


def is_remote_spec(spec):
    s = (spec or "").strip()
    return "://" in s and not (len(s) > 1 and s[1] == ":")


# ================================================================= transfers
def transfer_file(src_fs, src, dst_fs, dst, mtime=None):
    """Copy one file between any two file systems, preserving the modification time."""
    if mtime is None:
        st = src_fs.stat(src)
        mtime = st.mtime if st else None
    if isinstance(src_fs, LocalFS) and isinstance(dst_fs, LocalFS):
        dst_fs.put_file(src, dst, mtime if mtime else None)
        return
    local = src_fs.local_path(src)
    dst_fs.put_file(local, dst, mtime)


def transfer_tree(src_fs, src, dst_fs, dst, ctx=None):
    """Copy a folder (recursively) between file systems. Returns the number of files copied."""
    n = 0
    dst_fs.makedirs(dst)
    for e in src_fs.scandir(src):
        if ctx is not None and ctx.cancel:
            raise_cancel()
        s, d = src_fs.join(src, e.name), dst_fs.join(dst, e.name)
        if e.is_dir:
            n += transfer_tree(src_fs, s, dst_fs, d, ctx)
        else:
            transfer_file(src_fs, s, dst_fs, d, e.mtime)
            n += 1
    return n
