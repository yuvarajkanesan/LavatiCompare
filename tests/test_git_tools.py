"""Level-1 git integration: the command-line forms git calls for difftool / mergetool, driven through main.main()."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

DRIVER = r'''
import os, sys
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, %(root)r)
from PySide6.QtWidgets import QApplication, QMessageBox
import main
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
MODE = sys.argv[1]
def fake_exec(*a):
    app = QApplication.instance()
    win = [w for w in app.topLevelWidgets() if w.__class__.__name__ == "MainWindow"][0]
    v = win.tabs.currentWidget()
    if MODE == "save":
        v.save()
    elif MODE == "titles":
        print("TITLES", win.tabs.tabText(win.tabs.currentIndex()), "|", v.headers["L"].path.text(), "|", v.headers["R"].path.text())
    elif MODE == "mergetitles":
        print("TITLES", win.tabs.tabText(win.tabs.currentIndex()), "|", v.meta_labels["ours"].text(), "|", v.meta_labels["theirs"].text(), "|", sorted(v.meta_labels))
    elif MODE == "tab":
        print("TAB", win.tabs.tabText(win.tabs.currentIndex()), type(v).__name__)
    return 0
QApplication.exec = fake_exec
rc = main.main(["main.py"] + sys.argv[2:])
print("RC", rc)
'''


def drive(mode, *args, home):
    env = dict(os.environ, APPDATA=home, XDG_CONFIG_HOME=home)
    r = subprocess.run([sys.executable, "-c", DRIVER % {"root": ROOT}, mode, *args], capture_output=True, text=True, env=env, timeout=120)
    out = r.stdout
    rc = int(out.split("RC")[-1].split()[0]) if "RC" in out else None
    return rc, out, r.stderr


class GitToolTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def w(self, name, text):
        p = os.path.join(self.d, name)
        with open(p, "w", newline="") as f:
            f.write(text)
        return p

    def test_mergetool_saved_exits_zero(self):
        base, local, remote = self.w("M_BASE_1.txt", "a\nb\nc\n"), self.w("M_LOCAL_1.txt", "A\nb\nc\n"), self.w("M_REMOTE_1.txt", "a\nb\nC\n")
        merged = os.path.join(self.d, "M.txt")
        rc, out, err = drive("save", "--merge", local, remote, base, "--output", merged,
                             "--title1", "M.txt (local)", "--title2", "M.txt (remote)", "--title3", "M.txt (base)", home=self.d)
        self.assertEqual(rc, 0, out + err)
        with open(merged, newline="") as f:
            self.assertEqual(f.read(), "A\nb\nC\n")

    def test_mergetool_unsaved_or_conflicting_exits_nonzero(self):
        base, local, remote = self.w("B.txt", "a\n"), self.w("L.txt", "x\n"), self.w("R.txt", "y\n")
        merged = os.path.join(self.d, "M2.txt")
        rc, out, err = drive("save", "--merge", local, remote, base, "--output", merged, home=self.d)      # conflict: nothing saved
        self.assertEqual(rc, 1, out + err)
        self.assertFalse(os.path.exists(merged))
        rc, out, err = drive("noop", "--merge", local, remote, base, "--output", merged, home=self.d)      # window closed unsaved
        self.assertEqual(rc, 1, out + err)

    def test_mergetool_without_base(self):
        local, remote = self.w("L.txt", "one\nlocal\n"), self.w("R.txt", "one\nremote\n")
        rc, out, err = drive("mergetitles", "--merge", local, remote, os.path.join(self.d, "missing_base"), "--output",
                             os.path.join(self.d, "o.txt"), "--title1", "Mine", "--title2", "Theirs", home=self.d)
        self.assertEqual(rc, 1, out + err)
        self.assertIn("TITLES", out)
        self.assertIn("Mine", out)

    def test_difftool_titles_and_exit(self):
        a, b = self.w("A_LOCAL_9.kt", "x\n"), self.w("A_REMOTE_9.kt", "y\n")
        rc, out, err = drive("titles", a, b, "--title1", "A.kt (before)", "--title2", "A.kt (after)", home=self.d)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("TITLES A.kt | A.kt (before) | A.kt (after)", out)

    def test_dir_diff_opens_folder_compare(self):
        l, r = os.path.join(self.d, "l"), os.path.join(self.d, "r")
        for p in (l, r):
            os.makedirs(p)
        self.w("l/a.txt", "1")
        self.w("r/a.txt", "2")
        rc, out, err = drive("tab", l, r, "--title1", "before", "--title2", "after", home=self.d)
        self.assertEqual(rc, 0, out + err)
        self.assertIn("TAB before vs after FolderCompareView", out)


if __name__ == "__main__":
    unittest.main()
