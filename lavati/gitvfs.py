"""Git repositories as comparable file systems (read-only trees at any revision, the index, the working tree).

Location syntax:   git:<repo path>#<revision>
    revisions: WORKTREE (the folder on disk), INDEX (staged), HEAD, a branch / tag / commit, main~3, v1.0, stash@{0} ...
Trees are listed lazily with `git ls-tree`; changed files are found by comparing blob ids, so no file content is read
until you open a file. Uses the installed git command line (honours the user's config, credentials and LFS setup).
No Qt imports here.
"""
import hashlib
import io
import os
import posixpath
import re
import shutil
import subprocess
import time

from . import engine, vfs
from .vfs import Entry

MIN_GIT = (2, 30)
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
SPECIAL = ("WORKTREE", "INDEX")
_git_exe = None


class GitError(vfs.VFSError):
    pass


def tools_dir():
    from . import config
    return os.path.join(config.APP_DIR, "tools")


def _private_git():
    """Portable git that this app downloaded itself (see download_git)."""
    for sub in ("mingit\\cmd\\git.exe", "mingit/cmd/git.exe", "mingit/bin/git"):
        p = os.path.join(tools_dir(), *sub.replace("\\", "/").split("/"))
        if os.path.exists(p):
            return p
    return ""


def git_path():
    global _git_exe
    if _git_exe is None:
        _git_exe = shutil.which("git") or _private_git() or ""
        if not _git_exe and os.name == "nt":
            for base in (os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"), os.environ.get("LOCALAPPDATA")):
                if base:
                    for sub in ("Git\\cmd\\git.exe", "Programs\\Git\\cmd\\git.exe"):
                        p = os.path.join(base, sub)
                        if os.path.exists(p):
                            _git_exe = p
    return _git_exe


def can_download_git():
    return os.name == "nt"


def download_git(progress=None):
    """Download MinGit (Git for Windows' portable build) into the app's own folder. No admin rights, no installer.

    progress(done_bytes, total_bytes) is called while downloading. Returns the path of git.exe."""
    import json
    import urllib.request
    import zipfile
    global _git_exe
    if not can_download_git():
        raise GitError("Install Git with your package manager (for example: sudo apt install git, or brew install git).")
    api = "https://api.github.com/repos/git-for-windows/git/releases/latest"
    try:
        req = urllib.request.Request(api, headers={"User-Agent": "LavatiCompare", "Accept": "application/vnd.github+json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            rel = json.load(r)
        arch = "arm64" if "ARM" in os.environ.get("PROCESSOR_ARCHITECTURE", "").upper() else "64-bit"
        asset = next(a for a in rel["assets"] if re.fullmatch(rf"MinGit-[\d.]+-{arch}\.zip", a["name"]))
    except (OSError, StopIteration, ValueError, KeyError) as e:
        raise GitError(f"Could not find the Git download online: {e}") from e
    dest = os.path.join(tools_dir(), "mingit")
    os.makedirs(tools_dir(), exist_ok=True)
    tmp_zip = os.path.join(tools_dir(), asset["name"] + ".part")
    try:
        req = urllib.request.Request(asset["browser_download_url"], headers={"User-Agent": "LavatiCompare"})
        with urllib.request.urlopen(req, timeout=60) as r, open(tmp_zip, "wb") as f:
            total, done = int(r.headers.get("Content-Length") or 0), 0
            while True:
                b = r.read(1 << 18)
                if not b:
                    break
                f.write(b)
                done += len(b)
                if progress:
                    progress(done, total)
        shutil.rmtree(dest, ignore_errors=True)
        root = os.path.realpath(dest)
        with zipfile.ZipFile(tmp_zip) as z:
            for m in z.infolist():
                target = os.path.realpath(os.path.join(dest, m.filename))
                if not target.startswith(root + os.sep) and target != root:
                    raise GitError("The download contained an unsafe path")        # zip-slip guard
            z.extractall(dest)
    except (OSError, zipfile.BadZipFile) as e:
        raise GitError(f"Download failed: {e}") from e
    finally:
        try:
            os.remove(tmp_zip)
        except OSError:
            pass
    _git_exe = None
    exe = git_path()
    if not exe:
        raise GitError("Git was downloaded but could not be started.")
    return exe


def run(repo, *args, input=None, check=True, text=False):
    exe = git_path()
    if not exe:
        raise GitError("Git is not available. Use 'Download Git' in the Git dialog or Settings (no installation needed).")
    cmd = [exe, "-c", "core.quotepath=off", "-C", repo, *args]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        r = subprocess.run(cmd, input=input, capture_output=True, timeout=300, creationflags=flags)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise GitError(f"git failed: {e}") from e
    if check and r.returncode != 0:
        raise GitError((r.stderr.decode("utf-8", "replace").strip() or f"git exited with {r.returncode}")[:400])
    return r.stdout.decode("utf-8", "replace") if text else r.stdout


def version():
    exe = git_path()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.TimeoutExpired):
        return None
    m = re.search(r"(\d+)\.(\d+)(?:\.(\d+))?", out)
    return (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)) if m else None


def check_git():
    """Returns '' when git is usable, otherwise a message for the user."""
    v = version()
    if v is None:
        return "Git was not found." + (" Click 'Download Git' to fetch a portable copy (about 50 MB, no installation)." if can_download_git() else " Install it with your package manager.")
    if v[:2] < MIN_GIT:
        return f"Git {v[0]}.{v[1]} is too old - Lavati Compare needs Git {MIN_GIT[0]}.{MIN_GIT[1]} or newer."
    return ""


def find_repo(path):
    """Top-level folder of the repository containing `path` (handles sub-folders, worktrees, bare repos), or ''."""
    p = path if os.path.isdir(path) else os.path.dirname(path)
    if not p or not os.path.isdir(p) or not git_path():
        return ""
    try:
        top = run(p, "rev-parse", "--show-toplevel", text=True, check=False).strip()
        if top:
            return os.path.normpath(top)
        gd = run(p, "rev-parse", "--absolute-git-dir", text=True, check=False).strip()      # bare repository
        if gd and run(p, "rev-parse", "--is-bare-repository", text=True, check=False).strip() == "true":
            return os.path.normpath(gd)
    except GitError:
        pass
    return ""


def make_spec(repo, rev):
    return f"git:{repo}#{rev}"


def is_git_spec(spec):
    return isinstance(spec, str) and spec.lower().startswith("git:") and "#" in spec


def parse_spec(spec):
    body = spec[4:]
    repo, _, rev = body.rpartition("#")
    return repo, rev or "HEAD"


# ---------------------------------------------------------------- refs / history
def list_refs(repo):
    """{'branches': [...], 'remotes': [...], 'tags': [...], 'stashes': [...]}"""
    out = {"branches": [], "remotes": [], "tags": [], "stashes": []}
    fmt = "%(refname)%09%(objectname:short)%09%(subject)"
    for line in run(repo, "for-each-ref", f"--format={fmt}", text=True).splitlines():
        ref, _, rest = line.partition("\t")
        if ref.startswith("refs/heads/"):
            out["branches"].append(ref[11:])
        elif ref.startswith("refs/remotes/"):
            if not ref.endswith("/HEAD"):
                out["remotes"].append(ref[13:])
        elif ref.startswith("refs/tags/"):
            out["tags"].append(ref[10:])
    s = run(repo, "stash", "list", "--format=%gd%x09%s", text=True, check=False)
    out["stashes"] = [l.split("\t")[0] + "  " + l.split("\t")[-1] for l in s.splitlines() if l]
    return out


def current_branch(repo):
    b = run(repo, "rev-parse", "--abbrev-ref", "HEAD", text=True, check=False).strip()
    return "" if b in ("", "HEAD") else b          # '' = detached


def log(repo, rev="HEAD", limit=500, path=None):
    """[(sha, short, author, iso date, subject, parents)]"""
    fmt = "%H%x1f%an%x1f%aI%x1f%s%x1f%P"
    args = ["log", f"--format={fmt}", f"-n{limit}", rev, "--"]
    if path:
        args.append(path)
    out = []
    for line in run(repo, *args, text=True, check=False).splitlines():
        p = line.split("\x1f")
        if len(p) >= 5:
            out.append((p[0], p[0][:8], p[1], p[2], p[3], p[4].split()))
    return out


def merge_base(repo, a, b):
    r = run(repo, "merge-base", a, b, text=True, check=False).strip()
    if not r:
        raise GitError(f"{a} and {b} have no common ancestor")
    return r.splitlines()[0]


def resolve(repo, rev):
    return run(repo, "rev-parse", "--verify", "--quiet", rev + "^{commit}", text=True, check=False).strip()


def commit_time(repo, rev):
    t = run(repo, "show", "-s", "--format=%ct", rev, text=True, check=False).strip()
    return float(t) if t.isdigit() else 0.0


def detect_renames(repo, a, b, similarity=50):
    """[(old path, new path)] for files git considers renamed between two revisions (-M)."""
    out = run(repo, "diff", "--name-status", "-z", f"-M{similarity}%", a, b, text=True, check=False)
    parts = out.split("\0")
    pairs, i = [], 0
    while i < len(parts):
        st = parts[i]
        if st.startswith("R") and i + 2 < len(parts):
            pairs.append((parts[i + 1], parts[i + 2]))
            i += 3
        elif st:
            i += 2
        else:
            i += 1
    return pairs


def detect_renames_specs(repo, a, b, similarity=50):
    """Rename pairs between any two sides (commit-ish, INDEX or WORKTREE)."""
    if a == "WORKTREE" or (a == "INDEX" and b not in ("WORKTREE",)):
        a, b = b, a                                    # git diff wants the older side first; the pairs are swapped back below
        swapped = True
    else:
        swapped = False
    if b == "WORKTREE":
        args = ["diff", "--name-status", "-z", f"-M{similarity}%"] + ([a] if a != "INDEX" else [])
    elif b == "INDEX":
        args = ["diff", "--cached", "--name-status", "-z", f"-M{similarity}%", a]
    else:
        return detect_renames(repo, a, b, similarity) if not swapped else \
            [(n, o) for o, n in detect_renames(repo, a, b, similarity)]
    parts = run(repo, *args, text=True, check=False).split("\0")
    pairs, i = [], 0
    while i < len(parts):
        if parts[i].startswith("R") and i + 2 < len(parts):
            pairs.append((parts[i + 1], parts[i + 2]))
            i += 3
        else:
            i += 2 if parts[i] else 1
    return [(n, o) for o, n in pairs] if swapped else pairs


GIT_KEYS = ("diff.tool", "difftool.lavati.cmd", "difftool.prompt", "merge.tool", "mergetool.lavati.cmd",
            "mergetool.lavati.trustExitCode", "mergetool.keepBackup")


def config_commands(launch):
    """The `git config --global` settings that make Lavati Compare the difftool and mergetool."""
    return [("diff.tool", "lavati"),
            ("difftool.lavati.cmd", f'{launch} "$LOCAL" "$REMOTE" --title1 "$MERGED (before)" --title2 "$MERGED (after)"'),
            ("difftool.prompt", "false"),
            ("merge.tool", "lavati"),
            ("mergetool.lavati.cmd", f'{launch} --merge "$LOCAL" "$REMOTE" "$BASE" --output "$MERGED" '
                                     '--title1 "$MERGED (local)" --title2 "$MERGED (remote)" --title3 "$MERGED (base)"'),
            ("mergetool.lavati.trustExitCode", "true"),
            ("mergetool.keepBackup", "false")]


def configure_global(launch):
    """Write the settings into the user's global git config. Returns the list of keys written."""
    msg = check_git()
    if msg:
        raise GitError(msg)
    done = []
    for k, v in config_commands(launch):
        r = subprocess.run([git_path(), "config", "--global", k, v], capture_output=True, text=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            raise GitError(r.stderr.strip() or f"git config {k} failed")
        done.append(k)
    return done


def is_configured():
    exe = git_path()
    if not exe:
        return False
    r = subprocess.run([exe, "config", "--global", "--get", "merge.tool"], capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    return r.stdout.strip() == "lavati"


def blob_sha_of_bytes(data):
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


# ------------------------------------------------------------------- file system
class GitTreeFS(vfs.VFS):
    """A commit's tree (or the index) as a read-only folder tree. Paths use '/', the root is ''."""
    scheme = "git"
    readonly = True
    cheap_algo = "git"

    def __init__(self, repo, rev):
        super().__init__("")
        self.repo, self.rev = repo, rev
        msg = check_git()
        if msg:
            raise GitError(msg)
        if rev == "INDEX":
            self.mtime = time.time()
        else:
            if not resolve(repo, rev):
                raise GitError(f"'{rev}' is not a known branch, tag or commit in {repo}")
            self.mtime = commit_time(repo, rev)
        self._tree = {"": {}}
        self._sha = {}
        self._mode = {}
        self._load()

    def display(self, path):
        return f"git:{os.path.basename(self.repo)}@{self.rev}/{path}" if path else f"git:{os.path.basename(self.repo)}@{self.rev}"

    def _add(self, path, size, sha, mode):
        parts = path.split("/")
        for i in range(len(parts) - 1):
            parent = "/".join(parts[:i])
            self._tree.setdefault(parent, {}).setdefault(parts[i], Entry(parts[i], True, 0, self.mtime))
            self._tree.setdefault("/".join(parts[:i + 1]), {})
        parent = "/".join(parts[:-1])
        self._tree.setdefault(parent, {})[parts[-1]] = Entry(parts[-1], False, size, self.mtime)
        self._sha[path] = sha
        self._mode[path] = mode

    def _load(self):
        if self.rev == "INDEX":
            out = run(self.repo, "ls-files", "-s", "-z", text=True)
            rows = []
            for rec in out.split("\0"):
                if not rec:
                    continue
                meta, _, path = rec.partition("\t")
                mode, sha, stage = meta.split()
                if stage != "0":
                    continue                             # unmerged entries: keep the first stage only
                rows.append((path, sha, mode))
            sizes = self._sizes([r[1] for r in rows])
            for path, sha, mode in rows:
                self._add(path, sizes.get(sha, 0), sha, mode)
            return
        out = run(self.repo, "ls-tree", "-r", "-l", "-z", self.rev, text=True)
        for rec in out.split("\0"):
            if not rec:
                continue
            meta, _, path = rec.partition("\t")
            mode, typ, sha, size = meta.split(None, 3)
            if typ == "commit":                          # submodule: shown as an empty file named after the commit
                self._add(path, 0, sha, mode)
            else:
                self._add(path, int(size.strip() or 0), sha, mode)

    def _sizes(self, shas):
        uniq = list(dict.fromkeys(shas))
        if not uniq:
            return {}
        out = run(self.repo, "cat-file", "--batch-check=%(objectname) %(objectsize)", input=("\n".join(uniq) + "\n").encode(), text=True)
        res = {}
        for l in out.splitlines():
            p = l.split()
            if len(p) == 2 and p[1].isdigit():
                res[p[0]] = int(p[1])
        return res

    def scandir(self, path):
        return list(self._tree.get(path.strip("/"), {}).values())

    def stat(self, path):
        path = path.strip("/")
        if not path:
            return Entry("", True, 0, self.mtime)
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        return self._tree.get(parent, {}).get(path.rsplit("/", 1)[-1])

    def sha(self, path):
        return self._sha.get(path.strip("/"))

    def is_submodule(self, path):
        return self._mode.get(path.strip("/")) == "160000"

    def checksum(self, path, algo="crc32", ctx=None):
        if algo == "git":
            return self.sha(path)
        return super().checksum(path, algo, ctx)

    def open(self, path):
        sha = self.sha(path)
        if sha is None:
            raise GitError(f"{path}: not in {self.rev}")
        if self.is_submodule(path):
            return io.BytesIO(f"Subproject commit {sha}\n".encode())
        return io.BytesIO(run(self.repo, "cat-file", "blob", sha))

    def local_path(self, path):
        path = path.strip("/")
        dst = vfs.new_temp_path(posixpath.basename(path))
        with open(dst, "wb") as f:
            f.write(self.open(path).read())
        return dst


# --------------------------------------------------------------------- factory
def open_git(spec):
    """(fs, root) for 'git:<repo>#<rev>'. WORKTREE is the plain folder on disk."""
    repo, rev = parse_spec(spec)
    if not os.path.isdir(repo):
        raise GitError(f"Repository folder not found: {repo}")
    msg = check_git()
    if msg:
        raise GitError(msg)
    if rev.upper() == "WORKTREE":
        top = find_repo(repo) or repo
        return GitWorkFS(top), top
    return GitTreeFS(repo, rev.upper() if rev.upper() == "INDEX" else rev), ""


class GitWorkFS(vfs.LocalFS):
    """The working tree: a normal folder (the .git directory is skipped), with git blob ids for fast comparison."""
    cheap_algo = "git"

    def scandir(self, path):
        return [e for e in super().scandir(path) if e.name != ".git"]

    def checksum(self, path, algo="crc32", ctx=None):
        if algo == "git":
            with open(path, "rb") as f:
                data = f.read()
            return blob_sha_of_bytes(data)
        return super().checksum(path, algo, ctx)


# ------------------------------------------------------------ rename pairing
def apply_renames(nodes, pairs):
    """Merge a 'left only' file and a 'right only' file that git reports as a rename into one row.

    The merged node keeps the right path/name (rel) and remembers the old left name (name); its status is
    'changed' unless the blob ids match. Returns the number of pairs applied."""
    index = {}

    def walk(lst, parent):
        for n in lst:
            index[n.rel] = (lst, n, parent)
            walk(n.children, n)
    walk(nodes, None)
    done = 0
    for old, new in pairs:
        a, b = index.get(old), index.get(new)
        if not a or not b:
            continue
        lst_a, na, _ = a
        lst_b, nb, _ = b
        if na.is_dir or nb.is_dir or na.status != "left" or nb.status != "right":
            continue
        lfs, rfs = na.lfs, nb.rfs
        same = False
        try:
            ca = lfs.checksum(na.left["path"], "git")
            cb = rfs.checksum(nb.right["path"], "git")
            same = ca is not None and ca == cb
        except (OSError, vfs.VFSError):
            pass
        nb.left, nb.lfs = na.left, lfs
        nb.rname = nb.name
        nb.name = na.name
        nb.status = "same" if same else "changed"
        nb.renamed_from = old
        lst_a.remove(na)
        index.pop(old, None)
        done += 1
    if done:
        engine.settle_dirs(nodes)
    return done
