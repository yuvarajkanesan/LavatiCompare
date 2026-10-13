"""File operations on compared trees (copy, move, delete, rename, touch) and synchronisation plans.

Everything works through the VFS layer so the same code serves local folders and remote locations.
No Qt imports here.
"""
import os
import time
from dataclasses import dataclass

from . import engine, vfs

# ------------------------------------------------------------------ sync modes
MODES = [
    ("update_right", "Update right", "Copy new and newer files from left to right. Nothing is deleted."),
    ("update_left", "Update left", "Copy new and newer files from right to left. Nothing is deleted."),
    ("update_both", "Update both", "Newer file wins on each side; files that exist on one side only are copied across."),
    ("mirror_right", "Mirror left to right", "Make right identical to left, including deletes on the right."),
    ("mirror_left", "Mirror right to left", "Make left identical to right, including deletes on the left."),
]
MODE_KEYS = [m[0] for m in MODES]
MODE_ALIASES = {"two_way": "update_both", "update:left->right": "update_right", "update:right->left": "update_left",
                "update:both": "update_both", "mirror:left->right": "mirror_right", "mirror:right->left": "mirror_left"}


@dataclass
class Sides:
    lfs: object
    lroot: str
    rfs: object
    rroot: str

    def fs(self, side):
        return self.lfs if side == "L" else self.rfs

    def root(self, side):
        return self.lroot if side == "L" else self.rroot


def info(n, side):
    return n.left if side == "L" else n.right


def node_fs(S, n, side):
    """The VFS that holds this node's entry on `side` (archives carry their own)."""
    return (n.lfs if side == "L" else n.rfs) or S.fs(side)


def dest_path(n, direction, S):
    """Where node `n` goes when copied in `direction` ('R' = left->right, 'L' = right->left)."""
    dst_side = direction
    existing = info(n, dst_side)
    if existing:
        return existing["path"]
    return S.fs(dst_side).join(S.root(dst_side), n.rel)


def is_writable(S, side):
    return not S.fs(side).readonly


# --------------------------------------------------------------------- backups
def backup_existing(S, side, path, rel, stamp, backup_dir=""):
    """Copy the file about to be overwritten / deleted into a backup folder. Returns the backup path."""
    fs = S.fs(side)
    if not fs.exists(path):
        return None
    if backup_dir:
        target_fs, target = vfs.LocalFS(), os.path.join(backup_dir, stamp, *rel.split("/"))
    else:
        target_fs, target = fs, fs.join(S.root(side), f".lavati-backup/{stamp}/{rel}")
    st = fs.stat(path)
    if st and st.is_dir:
        vfs.transfer_tree(fs, path, target_fs, target)
    else:
        vfs.transfer_file(fs, path, target_fs, target)
    return target


def _remove(fs, path, trash):
    fs.remove(path, trash=trash and isinstance(fs, vfs.LocalFS))


# ----------------------------------------------------------------- operations
def copy_nodes(nodes, direction, S, ctx=None, backup=False, backup_dir="", move=False, stamp=None):
    """Copy (or move) nodes in `direction`. Returns (count, errors)."""
    stamp = stamp or time.strftime("%Y%m%d_%H%M%S")
    src_side = "L" if direction == "R" else "R"
    if not is_writable(S, direction):
        return 0, [f"The {'right' if direction == 'R' else 'left'} side is read-only"]
    done, errors = 0, []
    for n in nodes:
        if ctx is not None and ctx.cancel:
            break
        src = info(n, src_side)
        if not src:
            continue
        dst = dest_path(n, direction, S)
        try:
            sfs, dfs = node_fs(S, n, src_side), S.fs(direction)
            if n.is_dir and not n.archive:
                done += vfs.transfer_tree(sfs, src["path"], dfs, dst, ctx)
            else:
                if backup and dfs.exists(dst):
                    backup_existing(S, direction, dst, n.rel, stamp, backup_dir)
                vfs.transfer_file(sfs, src["path"], dfs, dst, src["mtime"])
                done += 1
            if move:
                if S.fs(src_side).readonly:
                    raise OSError("source is read-only, copied but not removed")
                _remove(sfs, src["path"], False)
        except (OSError, vfs.VFSError) as e:
            errors.append(f"{n.rel}: {e}")
    return done, errors


def delete_nodes(nodes, side, S, trash=True, ctx=None, backup=False, backup_dir="", stamp=None):
    stamp = stamp or time.strftime("%Y%m%d_%H%M%S")
    if not is_writable(S, side):
        return 0, [f"The {'left' if side == 'L' else 'right'} side is read-only"]
    done, errors = 0, []
    for n in nodes:
        if ctx is not None and ctx.cancel:
            break
        i = info(n, side)
        if not i:
            continue
        try:
            if backup:
                backup_existing(S, side, i["path"], n.rel, stamp, backup_dir)
            _remove(node_fs(S, n, side), i["path"], trash)
            done += 1
        except (OSError, vfs.VFSError) as e:
            errors.append(f"{n.rel}: {e}")
    return done, errors


def rename_node(node, side, new_name, S):
    """Rename the entry on one side. Returns an error string or ''."""
    i = info(node, side)
    if not i or not new_name or any(c in new_name for c in '/\\'):
        return "Invalid name"
    if not is_writable(S, side):
        return "That side is read-only"
    fs = node_fs(S, node, side)
    target = fs.join(fs.parent(i["path"]), new_name)
    if fs.exists(target) and target != i["path"]:
        return f"{new_name} already exists"
    try:
        fs.rename(i["path"], target)
    except (OSError, vfs.VFSError) as e:
        return str(e)
    return ""


def touch_nodes(nodes, side, S, mode="now", value=None):
    """Set modification times. mode: now | other (copy from the opposite side) | value (epoch seconds)."""
    if not is_writable(S, side):
        return 0, ["That side is read-only"]
    done, errors = 0, []
    other = "R" if side == "L" else "L"
    for n in nodes:
        i = info(n, side)
        if not i:
            continue
        if mode == "now":
            t = time.time()
        elif mode == "other":
            o = info(n, other)
            if not o:
                continue
            t = o["mtime"]
        else:
            t = value
        try:
            node_fs(S, n, side).set_mtime(i["path"], t)
            done += 1
        except (OSError, vfs.VFSError, NotImplementedError) as e:
            errors.append(f"{n.rel}: {e}")
    return done, errors


def make_folder(side, parent_path, name, S):
    fs = S.fs(side)
    if not is_writable(S, side):
        return "That side is read-only"
    try:
        fs.makedirs(fs.join(parent_path, name))
    except (OSError, vfs.VFSError) as e:
        return str(e)
    return ""


# ------------------------------------------------------------------ sync plan
def _op(action, direction, n, note="", kind="file"):
    src_side = "L" if direction == "R" else "R"
    src, dst = info(n, src_side), info(n, direction)
    return dict(action=action, dir=direction, rel=n.rel, note=note, kind=kind,
                size=(src or dst or {}).get("size") or 0,
                src=src["path"] if src else None, dst=dst["path"] if dst else None, node=n)


def build_plan(nodes, mode, only=None):
    """List of operations: action in copy | overwrite | delete | mkdir | skip, dir 'R' (to right) or 'L'."""
    mode = MODE_ALIASES.get(mode, mode)
    ops = []
    dir_deletes = []
    prune = []
    for n in engine.walk(nodes):
        if any(n.rel.startswith(p) for p in prune):
            continue
        if only is not None and n.rel not in only and not (n.is_dir and any(o.startswith(n.rel + "/") for o in only)):
            continue
        if n.is_dir and not n.archive:
            if n.status in ("left", "right"):
                side = "L" if n.status == "left" else "R"
                other = "R" if side == "L" else "L"
                if (mode == "mirror_right" and side == "R") or (mode == "mirror_left" and side == "L"):
                    dir_deletes.append(_op("delete", side, n, kind="dir"))      # the whole orphan folder at once
                    prune.append(n.rel + "/")
                elif not n.children:
                    if mode == "update_both" or (mode in ("update_right", "mirror_right") and side == "L")                             or (mode in ("update_left", "mirror_left") and side == "R"):
                        ops.append(_op("mkdir", other, n, kind="dir"))
            continue
        if n.status == "same":
            continue
        if mode == "update_both":
            if n.status == "left":
                ops.append(_op("copy", "R", n))
            elif n.status == "right":
                ops.append(_op("copy", "L", n))
            elif n.status == "newer":
                ops.append(_op("overwrite", "R", n))
            elif n.status == "older":
                ops.append(_op("overwrite", "L", n))
            else:
                ops.append(_op("skip", "R", n, "same time, different content"))
            continue
        fwd = "L" if mode in ("update_left", "mirror_left") else "R"
        src_side, dst_side = ("R", "L") if fwd == "L" else ("L", "R")
        mirror = mode.startswith("mirror")
        src_is_newer = "newer" if src_side == "L" else "older"      # node status when the source side is newer
        if n.status == ("left" if src_side == "L" else "right"):
            ops.append(_op("copy", fwd, n))
        elif n.status == dst_side_only(dst_side):
            if mirror:
                ops.append(_op("delete", dst_side, n))
        elif mirror:
            ops.append(_op("overwrite", fwd, n))
        elif n.status == src_is_newer:
            ops.append(_op("overwrite", fwd, n))
        elif n.status == "changed":
            ops.append(_op("skip", fwd, n, "same time, different content"))
        else:
            ops.append(_op("skip", fwd, n, f"{'right' if dst_side == 'R' else 'left'} newer"))
    dir_deletes.sort(key=lambda o: -o["rel"].count("/"))
    return ops + dir_deletes


def dst_side_only(side):
    return "left" if side == "L" else "right"


def plan_summary(ops):
    cnt = {"copy": 0, "overwrite": 0, "delete": 0, "mkdir": 0, "skip": 0}
    size = 0
    for o in ops:
        cnt[o["action"]] += 1
        if o["action"] in ("copy", "overwrite"):
            size += o["size"] or 0
    return cnt, size


def run_plan(ops, S, ctx=None, backup=True, backup_dir="", trash=True, progress=None, stamp=None):
    """Execute a plan. Returns (done, errors). `progress(i, total, op)` is called before each step."""
    stamp = stamp or time.strftime("%Y%m%d_%H%M%S")
    todo = [o for o in ops if o["action"] != "skip"]
    done, errors = 0, []
    for i, o in enumerate(todo):
        if ctx is not None and ctx.cancel:
            break
        if progress:
            progress(i, len(todo), o)
        n, d = o["node"], o["dir"]
        try:
            if o["action"] == "delete":
                side = d
                if not is_writable(S, side):
                    raise OSError("destination is read-only")
                if backup:
                    backup_existing(S, side, o["dst"], n.rel, stamp, backup_dir)
                _remove(node_fs(S, n, side), o["dst"], trash)
            elif o["action"] == "mkdir":
                if not is_writable(S, d):
                    raise OSError("destination is read-only")
                S.fs(d).makedirs(dest_path(n, d, S))
            else:
                if not is_writable(S, d):
                    raise OSError("destination is read-only")
                dst = dest_path(n, d, S)
                src_side = "L" if d == "R" else "R"
                dfs = S.fs(d)
                if o["action"] == "overwrite" and backup and dfs.exists(dst):
                    backup_existing(S, d, dst, n.rel, stamp, backup_dir)
                vfs.transfer_file(node_fs(S, n, src_side), o["src"], dfs, dst, info(n, src_side)["mtime"])
            done += 1
        except (OSError, vfs.VFSError) as e:
            errors.append(f"{o['rel']}: {e}")
    return done, errors


def plan_to_script(ops, S, mode, style=None):
    """A .bat (Windows) or .sh script performing a plan between two local folders; None if not local."""
    if not (isinstance(S.lfs, vfs.LocalFS) and isinstance(S.rfs, vfs.LocalFS)):
        return None
    win = (style or ("bat" if os.name == "nt" else "sh")) == "bat"
    lines = (["@echo off", f"rem Lavati Compare sync script - {mode}", "chcp 65001 >nul"] if win else
             ["#!/bin/sh", f"# Lavati Compare sync script - {mode}", "set -e"])
    q = (lambda p: f'"{p}"') if win else (lambda p: "'" + p.replace("'", "'\\''") + "'")
    for o in ops:
        if o["action"] == "skip":
            continue
        dst = o["dst"] or dest_path(o["node"], o["dir"], S)
        parent = os.path.dirname(dst)
        if o["action"] == "delete":
            if win:
                lines.append(f'rmdir /S /Q {q(dst)}' if o["kind"] == "dir" else f'del /F /Q {q(dst)}')
            else:
                lines.append(f"rm -rf {q(dst)}")
        elif o["action"] == "mkdir":
            lines.append(f'if not exist {q(dst)} mkdir {q(dst)}' if win else f"mkdir -p {q(dst)}")
        else:
            if win:
                lines.append(f'if not exist {q(parent)} mkdir {q(parent)}')
                lines.append(f'copy /Y {q(o["src"])} {q(dst)} >nul')
            else:
                lines.append(f"mkdir -p {q(parent)}")
                lines.append(f"cp -p {q(o['src'])} {q(dst)}")
    lines.append("echo Done." if win else "echo Done.")
    return ("\r\n" if win else "\n").join(lines) + ("\r\n" if win else "\n")
