"""Persistent settings, sessions and rules (JSON in %APPDATA%)."""
import copy
import json
import os
import time

from . import platform_utils

APP_DIR = platform_utils.config_dir()
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
SCRIPTS_DIR = os.path.join(APP_DIR, "scripts")

DEFAULTS = {
    "ignore_patterns": [".git", "build/"],
    "ignore_whitespace": True,
    "ignore_case_names": platform_utils.IS_WIN,
    "backup_overwritten": True,
    "use_recycle_bin": True,
    "font_size": 10,
    "theme": "light",                 # light | dark | system
    "sessions": [],
    # folder compare
    "compare_method": "size_time",    # size_time | crc | binary | rules
    "time_tolerance": 2.0,            # seconds; FAT/NTFS/exFAT rounding
    "archives": False,                # treat zip/tar/gz/jar as folders in the tree
    "ignore_eol": True,
    "ignore_case_text": False,
    "unimportant": [],                # regexes whose differences are unimportant
    "replacements": [],               # [[regex, replacement], ...] applied before comparing
    "word_wrap": False,
    "text_layout": "side",            # side | over
    "file_formats": [],               # per-file-type rules, see fileformats.py
    "sync_sessions": [],              # saved synchronisation presets
    "sync_backup_dir": "",            # "" = .lavati-backup inside the destination root
}


class Config:
    def __init__(self, path=CONFIG_PATH):
        self.path = path
        self.data = copy.deepcopy(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                self.data.update(json.load(f))
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except OSError:
            pass

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v
        self.save()

    # sessions -----------------------------------------------------------
    def add_session(self, name, kind, left, right, result="", extra=None):
        sessions = self.data["sessions"]
        for s in sessions:
            if s["kind"] == kind and s["left"] == left and s["right"] == right:
                s.update(name=name, result=result, opened=time.time())
                if extra:
                    s["extra"] = extra
                break
        else:
            sessions.append(dict(name=name, kind=kind, left=left, right=right, result=result,
                                 opened=time.time(), pinned=False, extra=extra or {}))
        sessions.sort(key=lambda s: -s["opened"])
        del sessions[200:]
        self.save()

    def update_result(self, kind, left, right, result):
        for s in self.data["sessions"]:
            if s["kind"] == kind and s["left"] == left and s["right"] == right:
                s["result"] = result
        self.save()

    def toggle_pin(self, session):
        session["pinned"] = not session.get("pinned", False)
        self.save()

    def remove_session(self, session):
        if session in self.data["sessions"]:
            self.data["sessions"].remove(session)
            self.save()
