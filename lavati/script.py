"""Lavati script language for unattended jobs (nightly syncs, reports). No Qt imports - runs headless.

One command per line; '#' starts a comment; arguments may be "quoted". %NAME% / ${NAME} expand variables.

    set SRC="D:\\work"
    load "%SRC%" "E:\\backup\\work"          # any folders, archives, snapshots or URLs (ftp://, sftp://, s3:// ...)
    exclude "*.tmp; .git/; build/"
    include "*.kt; *.xml"                    # optional: compare only these
    criteria crc                             # size | timestamp | crc | binary | rules
    tolerance 2                              # timestamp tolerance in seconds
    rules ws=changes case=off eol=on unimportant="//.*$"
    compare
    folder-report output-to:"%SRC%\\report-%DATE%.html" format=html show=differences
    select left-newer                        # left-newer right-newer left-only right-only differences orphans same all none
    copy left->right                         # copy / move the selection
    sync update:left->right backup=on trash=on     # update:left->right|right->left|both  mirror:left->right|right->left
    delete right                             # delete the selection on one side (left | right | both)
    snapshot left "D:\\snaps\\work-%DATE%.lsnap" crc=on
    file-report "a.txt" "b.txt" output-to:"diff.html" format=html
    log "D:\\logs\\nightly.log"
    echo Finished %DATE%
    fail-if differences                      # exit code 1 when the compare found differences
"""
import os
import re
import time

from . import engine, fsops, reports, vfs

SELECTORS = {
    "left-newer": ("newer",), "right-newer": ("older",), "newer": ("newer",), "older": ("older",),
    "left-only": ("left",), "right-only": ("right",), "orphans": ("left", "right"),
    "differences": engine.DIFF_STATUSES, "different": ("changed", "newer", "older"), "same": ("same",),
    "all": engine.STATUSES,
}
METHOD_ALIASES = {"size": "size_time", "timestamp": "size_time", "size_time": "size_time", "size+time": "size_time",
                  "crc": "crc", "checksum": "crc", "binary": "binary", "bytes": "binary", "rules": "rules", "text": "rules"}


class ScriptError(Exception):
    def __init__(self, msg, line=None):
        super().__init__(msg if line is None else f"line {line}: {msg}")
        self.line = line


def tokenize(line):
    """Split on spaces, honouring "double quotes" (backslashes are kept as they are - Windows paths)."""
    out, cur, q, has = [], [], False, False
    i = 0
    while i < len(line):
        c = line[i]
        if c == '"':
            q = not q
            has = True
        elif c in " \t" and not q:
            if cur or has:
                out.append("".join(cur))
            cur, has = [], False
        else:
            cur.append(c)
        i += 1
    if q:
        raise ScriptError("unterminated quote")
    if cur or has:
        out.append("".join(cur))
    return out


def split_opts(args):
    """['a', 'k=v', 'output-to:x'] -> (['a'], {'k': 'v', 'output-to': 'x'})"""
    pos, kw = [], {}
    for a in args:
        m = re.match(r"^([A-Za-z][\w-]*)[=:](.*)$", a)
        if m and not re.match(r"^[A-Za-z]:[\\/]", a) and "://" not in a and not re.match(r"^(update|mirror):", a):
            kw[m.group(1).lower()] = m.group(2)
        else:
            pos.append(a)
    return pos, kw


def truthy(v, default=True):
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "on", "yes", "true", "y")


class ScriptRunner:
    def __init__(self, cfg=None, out=None, variables=None, base_dir=None):
        from . import config
        self.cfg = cfg or config.Config()
        self.out = out or (lambda s: print(s, flush=True))
        self.vars = {}
        self.base_dir = base_dir or os.getcwd()
        now = time.localtime()
        self.vars.update(DATE=time.strftime("%Y%m%d", now), TIME=time.strftime("%H%M%S", now),
                         TIMESTAMP=time.strftime("%Y%m%d_%H%M%S", now), HOME=os.path.expanduser("~"),
                         SCRIPTDIR=self.base_dir)
        self.vars.update(variables or {})
        self.logfile = None
        self.opts = engine.CompareOptions.from_config(self.cfg)
        self.left = self.right = ""
        self.S = None
        self.nodes = []
        self.counter = {}
        self.selected = []
        self.exit_code = 0
        self.ctx = engine.Ctx()
        self._open = {}
        self.stop = False

    # ------------------------------------------------------------ helpers
    def log(self, msg):
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}"
        self.out(line)
        if self.logfile:
            try:
                with open(self.logfile, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
            except OSError:
                pass

    def expand(self, s):
        def sub(m):
            name = m.group(1) or m.group(2)
            if name in self.vars:
                return str(self.vars[name])
            if name.upper() in self.vars:
                return str(self.vars[name.upper()])
            if name in os.environ:
                return os.environ[name]
            raise ScriptError(f"unknown variable {name}")
        return re.sub(r"%([A-Za-z_]\w*)%|\$\{([A-Za-z_]\w*)\}", sub, s)

    def path(self, s):
        if vfs.is_remote_spec(s) or os.path.isabs(s) or s.lower().startswith(("snapshot:", "git:")):
            return s
        return os.path.normpath(os.path.join(self.base_dir, s))

    # ---------------------------------------------------------------- run
    def run(self, text):
        lines = text.splitlines()
        for n, raw in enumerate(lines, 1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            try:
                self.execute(line)
            except ScriptError as e:
                e.line = e.line or n
                self.log(f"ERROR line {n}: {e}  [{line}]")
                self.exit_code = 2
                return self.exit_code
            except (OSError, vfs.VFSError, engine.Cancelled) as e:
                self.log(f"ERROR line {n}: {type(e).__name__}: {e}  [{line}]")
                self.exit_code = 2
                return self.exit_code
            if self.stop:
                break
        return self.exit_code

    def execute(self, line):
        args = tokenize(line)
        cmd, rest = args[0].lower(), args[1:]
        if cmd == "set":
            return self._set(line, rest)
        rest = [self.expand(a) for a in rest]
        fn = getattr(self, "cmd_" + cmd.replace("-", "_"), None)
        if fn is None:
            raise ScriptError(f"unknown command '{cmd}'")
        return fn(*split_opts(rest))

    def _set(self, line, rest):
        m = re.match(r"(?i)^set\s+([A-Za-z_]\w*)\s*[= ]\s*(.*)$", line)
        if not m:
            raise ScriptError("usage: set NAME=value")
        val = m.group(2).strip()
        if len(val) >= 2 and val[0] == val[-1] == '"':
            val = val[1:-1]
        self.vars[m.group(1)] = self.expand(val)

    # ----------------------------------------------------------- commands
    def cmd_echo(self, pos, kw):
        self.log(" ".join(pos))

    def cmd_log(self, pos, kw):
        pos = [p for p in pos if p.lower() != "verbose"]
        if not pos or pos[0].lower() == "off":
            self.logfile = None
            return
        self.logfile = self.path(pos[0])
        os.makedirs(os.path.dirname(self.logfile) or ".", exist_ok=True)

    def cmd_load(self, pos, kw):
        if len(pos) != 2:
            raise ScriptError("usage: load LEFT RIGHT")
        self.left, self.right = self.path(pos[0]), self.path(pos[1])
        self.S, self.nodes, self.selected = None, [], []
        self.log(f"load {self.left}  <->  {self.right}")

    def _patterns(self, pos):
        out = []
        for p in pos:
            out += [x.strip() for x in p.replace(",", ";").split(";") if x.strip()]
        return out

    def cmd_exclude(self, pos, kw):
        self.opts.patterns = self._patterns(pos)

    def cmd_include(self, pos, kw):
        self.opts.include = self._patterns(pos)

    def cmd_filter(self, pos, kw):
        inc, exc = [], []
        for p in self._patterns(pos):
            (exc if p.startswith("-") else inc).append(p.lstrip("-"))
        self.opts.include, self.opts.patterns = inc, exc

    def cmd_criteria(self, pos, kw):
        if not pos or pos[0].lower() not in METHOD_ALIASES:
            raise ScriptError("usage: criteria size|timestamp|crc|binary|rules")
        self.opts.method = METHOD_ALIASES[pos[0].lower()]

    def cmd_tolerance(self, pos, kw):
        try:
            self.opts.time_tol = float(pos[0])
        except (ValueError, IndexError):
            raise ScriptError("usage: tolerance SECONDS")

    def cmd_ignore_case(self, pos, kw):
        self.opts.ignore_case_names = truthy(pos[0] if pos else None)

    def cmd_archives(self, pos, kw):
        self.opts.archives = truthy(pos[0] if pos else None)

    def cmd_rules(self, pos, kw):
        r = self.opts.rules or engine.TextRules()
        if "ws" in kw:
            if kw["ws"] not in engine.WS_MODES:
                raise ScriptError("ws must be one of " + "|".join(engine.WS_MODES))
            r.ws = kw["ws"]
        if "case" in kw:
            r.ignore_case = truthy(kw["case"])
        if "eol" in kw:
            r.ignore_eol = truthy(kw["eol"])
        if "unimportant" in kw:
            r.unimportant = r.unimportant + [kw["unimportant"]]
        if "replace" in kw:
            a, sep, b = kw["replace"].partition("=>")
            r.replacements = r.replacements + [(a, b)]
        r._comp = None
        self.opts.rules = r
        if kw and self.opts.method != "rules":
            self.opts.method = "rules"

    def _open_spec(self, spec):
        if not spec:
            return None, None
        if spec not in self._open:
            if not vfs.is_remote_spec(spec) and not spec.lower().startswith(("snapshot:", "git:")) and not os.path.exists(spec):
                raise ScriptError(f"not found: {spec}")
            self._open[spec] = vfs.open_location(spec)
        return self._open[spec]

    def cmd_compare(self, pos=(), kw=None):
        if not self.left and not self.right:
            raise ScriptError("use 'load' before 'compare'")
        lfs, lroot = self._open_spec(self.left)
        rfs, rroot = self._open_spec(self.right)
        for fs in (lfs, rfs):
            if fs is not None and hasattr(fs, "invalidate"):
                fs.invalidate()
        self.counter = {}
        self.nodes = engine.compare_tree_parallel(lfs, lroot, rfs, rroot, self.opts, counter=self.counter, ctx=self.ctx)
        engine.compress(self.nodes)
        self.S = fsops.Sides(lfs, lroot, rfs, rroot)
        self.selected = []
        c = self.counter
        self.log("compare: " + ", ".join(f"{k} {c.get(k, 0)}" for k in engine.STATUSES))

    def _need(self):
        if self.S is None:
            self.cmd_compare()

    def cmd_select(self, pos, kw):
        self._need()
        if not pos:
            raise ScriptError("usage: select <" + "|".join(sorted(SELECTORS)) + "|none>")
        add = any(p.lower() == "add" for p in pos)
        kinds = [p.lower() for p in pos if p.lower() != "add"]
        picked = []
        for k in kinds:
            if k == "none":
                picked = []
                continue
            if k not in SELECTORS:
                raise ScriptError(f"unknown selection '{k}'")
            want = SELECTORS[k]
            picked += [n for n in engine.walk(self.nodes) if not n.is_dir and n.status in want]
        seen = {id(n) for n in (self.selected if add else [])}
        base = list(self.selected) if add else []
        for n in picked:
            if id(n) not in seen:
                base.append(n)
                seen.add(id(n))
        self.selected = base
        self.log(f"select: {len(self.selected)} file(s)")

    def _dir(self, pos):
        for p in pos:
            m = re.match(r"(?i)^(left|right)\s*(->|=>|to)\s*(left|right)$", p)
            if m:
                return "R" if m.group(1).lower() == "left" else "L"
            if p.lower() in ("left->right", "l->r"):
                return "R"
            if p.lower() in ("right->left", "r->l"):
                return "L"
        raise ScriptError("expected 'left->right' or 'right->left'")

    def _selection(self, pos):
        self._need()
        if any(p.lower() == "all" for p in pos):
            return [n for n in engine.walk(self.nodes) if not n.is_dir]
        return list(self.selected)

    def cmd_copy(self, pos, kw, move=False):
        d = self._dir(pos)
        nodes = self._selection(pos)
        done, errs = fsops.copy_nodes(nodes, d, self.S, self.ctx, truthy(kw.get("backup"), bool(self.cfg["backup_overwritten"])),
                                      kw.get("backup-dir", ""), move)
        self.log(f"{'move' if move else 'copy'}: {done} file(s), {len(errs)} error(s)")
        for e in errs:
            self.log("  " + e)
        if errs:
            self.exit_code = max(self.exit_code, 1)

    def cmd_move(self, pos, kw):
        self.cmd_copy(pos, kw, True)

    def cmd_delete(self, pos, kw):
        side = next((p.lower() for p in pos if p.lower() in ("left", "right", "both")), None)
        if side is None:
            raise ScriptError("usage: delete left|right|both [selected|all]")
        nodes = self._selection(pos)
        trash = truthy(kw.get("trash"), bool(self.cfg["use_recycle_bin"]))
        total, errs = 0, []
        for s in (("L", "R") if side == "both" else ("L" if side == "left" else "R",)):
            c, e = fsops.delete_nodes(nodes, s, self.S, trash, self.ctx, truthy(kw.get("backup"), False), kw.get("backup-dir", ""))
            total += c
            errs += e
        self.log(f"delete: {total} item(s), {len(errs)} error(s)")
        for e in errs:
            self.log("  " + e)
        if errs:
            self.exit_code = max(self.exit_code, 1)

    def cmd_sync(self, pos, kw):
        self._need()
        mode = None
        for p in pos:
            key = fsops.MODE_ALIASES.get(p.lower(), p.lower())
            if key in fsops.MODE_KEYS:
                mode = key
        if mode is None:
            raise ScriptError("usage: sync update:left->right | update:right->left | update:both | mirror:left->right | mirror:right->left")
        only = {n.rel for n in self.selected} if any(p.lower() == "selected" for p in pos) else None
        ops = fsops.build_plan(self.nodes, mode, only)
        cnt, size = fsops.plan_summary(ops)
        self.log(f"sync {mode}: copy {cnt['copy']}, overwrite {cnt['overwrite']}, delete {cnt['delete']}, skip {cnt['skip']}")
        if truthy(kw.get("dry-run"), False) or truthy(kw.get("preview"), False):
            for o in ops:
                if o["action"] != "skip":
                    self.log(f"  would {o['action']} {o['rel']}")
            return
        done, errs = fsops.run_plan(ops, self.S, self.ctx, truthy(kw.get("backup"), bool(self.cfg["backup_overwritten"])),
                                    kw.get("backup-dir", self.cfg["sync_backup_dir"]),
                                    truthy(kw.get("trash"), bool(self.cfg["use_recycle_bin"])))
        self.log(f"sync done: {done} operation(s), {len(errs)} error(s)")
        for e in errs:
            self.log("  " + e)
        if errs:
            self.exit_code = max(self.exit_code, 1)

    def cmd_folder_report(self, pos, kw):
        self._need()
        out = kw.get("output-to") or kw.get("output")
        if not out:
            raise ScriptError("folder-report needs output-to:\"file\"")
        out = self.path(out)
        fmt = kw.get("format") or ("xml" if out.lower().endswith(".xml") else "text" if out.lower().endswith(".txt") else "html")
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        n = reports.write_folder_report(out, {"txt": "text"}.get(fmt, fmt), self.left, self.right, self.nodes,
                                        kw.get("show", "differences"), self.counter)
        self.log(f"folder-report: {n} file(s) -> {out}")

    def cmd_file_report(self, pos, kw):
        if len(pos) != 2:
            raise ScriptError('usage: file-report "left" "right" output-to:"file"')
        out = kw.get("output-to") or kw.get("output")
        if not out:
            raise ScriptError("file-report needs output-to:\"file\"")
        lp, rp, out = self.path(pos[0]), self.path(pos[1]), self.path(out)
        a, b = engine.load_text_file(lp), engine.load_text_file(rp)
        rules = self.opts.rules or engine.TextRules.from_config(self.cfg)
        fmt = kw.get("format") or ("xml" if out.lower().endswith(".xml") else "text" if out.lower().endswith(".txt") else "html")
        ctxl = kw.get("context")
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        n = reports.write_text_report(out, {"txt": "text"}.get(fmt, fmt), lp, rp, a.lines, b.lines, rules,
                                      context=int(ctxl) if ctxl not in (None, "") else None)
        self.log(f"file-report: {n} hunk(s) -> {out}")
        if n:
            self.last_file_diff = n

    def cmd_snapshot(self, pos, kw):
        if len(pos) != 2 or pos[0].lower() not in ("left", "right"):
            raise ScriptError('usage: snapshot left|right "file.lsnap" [crc=on]')
        self._need()
        side = "L" if pos[0].lower() == "left" else "R"
        fs, root = self.S.fs(side), self.S.root(side)
        out = self.path(pos[1])
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        n = vfs.capture_snapshot(fs, root, out, self.opts.patterns, truthy(kw.get("crc"), True), self.ctx)
        self.log(f"snapshot: {n} entries -> {out}")

    def cmd_fail_if(self, pos, kw):
        self._need()
        what = (pos[0].lower() if pos else "differences")
        diffs = sum(self.counter.get(k, 0) for k in engine.DIFF_STATUSES)
        orphans = self.counter.get("left", 0) + self.counter.get("right", 0)
        trip = {"differences": diffs > 0, "orphans": orphans > 0, "same": self.counter.get("same", 0) > 0,
                "no-differences": diffs == 0}.get(what)
        if trip is None:
            raise ScriptError("fail-if differences|orphans|no-differences")
        if trip:
            self.log(f"fail-if {what}: condition met - stopping with exit code 1")
            self.exit_code = 1
            self.stop = True

    def cmd_exit(self, pos, kw):
        try:
            self.exit_code = int(pos[0]) if pos else self.exit_code
        except ValueError:
            raise ScriptError("usage: exit [code]")
        self.stop = True


def run_file(path, variables=None, out=None, cfg=None):
    """Run a script file. Returns the process exit code (0 ok, 1 differences/errors, 2 script error)."""
    with open(path, "r", encoding="utf-8-sig") as f:
        text = f.read()
    runner = ScriptRunner(cfg, out, variables, base_dir=os.path.dirname(os.path.abspath(path)))
    return runner.run(text)


TEMPLATE = r'''# Lavati Compare script - run with:  lavati --script this-file.lcs
# Commands: load, exclude, include, criteria, tolerance, rules, compare, select, copy, move, delete, sync,
#           folder-report, file-report, snapshot, log, echo, set, fail-if, exit
set SRC="C:\Projects\work"
set DST="D:\Backup\work"
log "%DST%\sync-%DATE%.log"
load "%SRC%" "%DST%"
exclude "*.tmp; .git/; build/; node_modules/"
criteria timestamp
tolerance 2
compare
folder-report output-to:"%DST%\report-%DATE%.html" format=html show=differences
sync update:left->right backup=on trash=on
echo Finished
'''
