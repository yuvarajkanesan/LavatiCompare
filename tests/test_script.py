import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lavati import config, script  # noqa: E402

T = 1_700_000_000


def w(path, data, mtime=T):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(data)
    os.utime(path, (mtime, mtime))


def rd(path):
    with open(path) as f:
        return f.read()


class ScriptTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.L, self.R = os.path.join(self.d, "L"), os.path.join(self.d, "R")
        w(f"{self.L}/a.txt", "new", T + 100)
        w(f"{self.R}/a.txt", "old", T)
        w(f"{self.L}/only.txt", "o")
        w(f"{self.R}/gone.txt", "g")
        w(f"{self.L}/skip.tmp", "t")
        self.cfg = config.Config(os.path.join(self.d, "cfg.json"))
        self.cfg["ignore_patterns"] = []
        self.out = []

    def tearDown(self):
        shutil.rmtree(self.d, ignore_errors=True)

    def run_script(self, text, **vars):
        r = script.ScriptRunner(self.cfg, self.out.append, vars, base_dir=self.d)
        code = r.run(text)
        return code, r

    def test_tokenize_and_options(self):
        self.assertEqual(script.tokenize('load "C:\\a b\\x" D:\\y'), ["load", "C:\\a b\\x", "D:\\y"])
        self.assertEqual(script.split_opts(["C:\\x", "k=v", "output-to:C:\\o.html", "update:both", "ftp://h/x"]),
                         (["C:\\x", "update:both", "ftp://h/x"], {"k": "v", "output-to": "C:\\o.html"}))
        with self.assertRaises(script.ScriptError):
            script.tokenize('a "b')

    def test_sync_with_report_and_backup(self):
        code, r = self.run_script(f'''
            # nightly job
            set SRC="{self.L}"
            load "%SRC%" "{self.R}"
            exclude "*.tmp"
            criteria timestamp
            folder-report output-to:"{self.d}/rep-%DATE%.html" format=html show=differences
            sync update:left->right backup=on trash=off
            fail-if differences
            ''')
        self.assertEqual(code, 1, "\n".join(self.out))       # differences existed at compare time
        self.assertEqual(rd(f"{self.R}/a.txt"), "new")
        self.assertEqual(rd(f"{self.R}/only.txt"), "o")
        self.assertTrue(os.path.exists(f"{self.R}/gone.txt"))           # update never deletes
        self.assertFalse(os.path.exists(f"{self.R}/skip.tmp"))          # excluded
        reps = [f for f in os.listdir(self.d) if f.startswith("rep-")]
        self.assertEqual(len(reps), 1)
        self.assertIn("a.txt", rd(os.path.join(self.d, reps[0])))
        bk = os.path.join(self.R, ".lavati-backup")
        self.assertTrue(os.path.isdir(bk))

    def test_mirror_and_selection_ops(self):
        code, r = self.run_script(f'''
            load "{self.L}" "{self.R}"
            exclude "*.tmp"
            select right-only
            delete right trash=off
            select left-only
            copy left->right
            compare
            fail-if differences
            ''')
        self.assertEqual(code, 1)            # a.txt still differs (newer), the rest was handled
        self.assertFalse(os.path.exists(f"{self.R}/gone.txt"))
        self.assertTrue(os.path.exists(f"{self.R}/only.txt"))
        code, r = self.run_script(f'''
            load "{self.L}" "{self.R}"
            exclude "*.tmp"
            sync mirror:left->right backup=off trash=off
            compare
            fail-if differences
            echo clean
            ''')
        self.assertEqual(code, 0, "\n".join(self.out))
        self.assertIn("clean", self.out[-1])

    def test_dry_run_and_errors(self):
        code, r = self.run_script(f'''
            load "{self.L}" "{self.R}"
            exclude "*.tmp"
            sync update:left->right dry-run=on
            ''')
        self.assertEqual(code, 0)
        self.assertEqual(rd(f"{self.R}/a.txt"), "old")
        self.assertTrue(any("would" in l for l in self.out))
        code, r = self.run_script("bogus-command 1 2")
        self.assertEqual(code, 2)
        code, r = self.run_script('load "a" "b"\nsync nonsense')
        self.assertEqual(code, 2)
        code, r = self.run_script(f'load "{self.d}/nope" "{self.R}"\ncompare')
        self.assertEqual(code, 2)
        code, r = self.run_script("echo %NOT_DEFINED_ANYWHERE%")
        self.assertEqual(code, 2)

    def test_file_report_snapshot_log(self):
        a, b = os.path.join(self.d, "a.txt"), os.path.join(self.d, "b.txt")
        w(a, "x\ny\nz\n")
        w(b, "x\nY\nz\n")
        snap = os.path.join(self.d, "s.lsnap")
        logf = os.path.join(self.d, "run.log")
        code, r = self.run_script(f'''
            log "{logf}"
            file-report "{a}" "{b}" output-to:"{self.d}/f.xml" format=xml
            load "{self.L}" "{self.R}"
            snapshot left "{snap}" crc=on
            ''')
        self.assertEqual(code, 0, "\n".join(self.out))
        import xml.dom.minidom
        xml.dom.minidom.parse(os.path.join(self.d, "f.xml"))
        self.assertTrue(os.path.exists(snap))
        self.assertIn("snapshot", rd(logf))

    def test_rules_command(self):
        w(f"{self.L}/r.txt", "a  b\n")
        w(f"{self.R}/r.txt", "a b\n")
        code, r = self.run_script(f'''
            load "{self.L}" "{self.R}"
            exclude "*.tmp"
            rules ws=changes
            compare
            select same
            ''')
        self.assertEqual(code, 0)
        self.assertIn("r.txt", [n.rel for n in r.selected])

    def test_run_file_and_template(self):
        p = os.path.join(self.d, "job.lcs")
        with open(p, "w") as f:
            f.write(f'load "{self.L}" "{self.R}"\ncompare\nexit 7\necho never\n')
        self.assertEqual(script.run_file(p, out=self.out.append, cfg=self.cfg), 7)
        self.assertNotIn("never", " ".join(self.out))
        self.assertIn("load", script.TEMPLATE)


if __name__ == "__main__":
    unittest.main()
