import os
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
MAIN = os.path.join(ROOT, "main.py")
T = 1_700_000_000


def w(path, data, mtime=T):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(data)
    os.utime(path, (mtime, mtime))


def run(*args, env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([sys.executable, MAIN, *args], capture_output=True, text=True, env=e, timeout=120)


class CLI(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.env = {"APPDATA": self.d, "XDG_CONFIG_HOME": self.d}
        self.L, self.R = os.path.join(self.d, "L"), os.path.join(self.d, "R")
        w(f"{self.L}/a.txt", "new", T + 100)
        w(f"{self.R}/a.txt", "old", T)
        w(f"{self.L}/b.txt", "b")

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def test_version_and_help_do_not_load_qt(self):
        r = run("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("Lavati Compare", r.stdout)
        r = run("--help")
        self.assertEqual(r.returncode, 0)
        self.assertIn("--script", r.stdout)
        code = ("import sys; sys.path.insert(0, %r); sys.argv=['x','--script','/nonexistent.lcs']; import main; "
                "rc = main.headless(sys.argv[1:]); print(rc, 'PySide6' in sys.modules)" % ROOT)
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env={**os.environ, **self.env})
        self.assertEqual(r.stdout.split()[-1], "False", r.stdout + r.stderr)      # headless run never imports Qt

    def test_script_exit_codes(self):
        s = os.path.join(self.d, "job.lcs")
        with open(s, "w") as f:
            f.write(f'load "{self.L}" "{self.R}"\ncompare\nfolder-report output-to:"{self.d}/r.txt" format=text\nfail-if differences\n')
        r = run("--script", s, env=self.env)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("compare:", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.d, "r.txt")))
        with open(s, "w") as f:
            f.write("nonsense\n")
        self.assertEqual(run("--script", s, env=self.env).returncode, 2)
        self.assertEqual(run("--script", os.path.join(self.d, "missing.lcs"), env=self.env).returncode, 2)
        with open(s, "w") as f:
            f.write(f'load "{self.L}" "%OTHER%"\ncompare\n')
        r = run("--script", s, "--var", f"OTHER={self.R}", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_sync_and_report(self):
        r = run("--sync", "update-right", self.L, self.R, "--no-trash", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(f"{self.R}/a.txt") as f:
            self.assertEqual(f.read(), "new")
        self.assertTrue(os.path.exists(f"{self.R}/b.txt"))
        out = os.path.join(self.d, "rep.xml")
        r = run("--report", "xml", out, self.L, self.R, "--show", "all", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        import xml.dom.minidom
        doc = xml.dom.minidom.parse(out)
        self.assertEqual(doc.documentElement.getAttribute("type"), "folder")
        out2 = os.path.join(self.d, "f.html")
        r = run("--report", "html", out2, f"{self.L}/a.txt", f"{self.R}/b.txt", env=self.env)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(os.path.exists(out2))


if __name__ == "__main__":
    unittest.main()
