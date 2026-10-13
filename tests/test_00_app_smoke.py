"""End-to-end smoke test: open every kind of session in the real main window (offscreen) and poke at it."""
import os
import shutil
import sys
import tempfile
import unittest
import zipfile

_TMP = tempfile.mkdtemp(prefix="lavati-smoke-")
os.environ["APPDATA"] = _TMP                       # keep the user's real settings untouched
os.environ["XDG_CONFIG_HOME"] = _TMP
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
from PySide6.QtWidgets import QMessageBox  # noqa: E402

# a modal box would block an unattended run forever: answer them automatically
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.Yes)
QMessageBox.warning = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.information = staticmethod(lambda *a, **k: QMessageBox.Ok)
QMessageBox.critical = staticmethod(lambda *a, **k: QMessageBox.Ok)
from lavati import config, engine, theme  # noqa: E402
from lavati.app import MainWindow  # noqa: E402

sys.path.insert(0, os.path.dirname(__file__))
import test_special as ts  # noqa: E402

T = 1_700_000_000


def w(path, data, mtime=T):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb" if isinstance(data, bytes) else "w") as f:
        f.write(data)
    os.utime(path, (mtime, mtime))


def wait(cond, timeout=15000):
    """Pump the event loop until cond() holds. time.sleep releases the GIL so worker threads keep running
    (QTest.qWait would starve them)."""
    import time
    end = time.time() + timeout / 1000
    while not cond() and time.time() < end:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()
    return cond()


def settled(v, action):
    """Run action() (which triggers a refresh) and wait for the new comparison to be displayed."""
    g = v.generation
    action()
    assert wait(lambda: v.generation > g), "comparison did not finish"


class Smoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = tempfile.mkdtemp(prefix="lavati-data-")
        cls.cfg = config.Config(os.path.join(cls.d, "cfg.json"))
        cls.win = MainWindow(cls.cfg)
        cls.win.resize(1400, 900)
        cls.win.show()
        d = cls.d
        cls.L, cls.R = os.path.join(d, "L"), os.path.join(d, "R")
        w(f"{cls.L}/same.txt", "same\n")
        w(f"{cls.R}/same.txt", "same\n")
        w(f"{cls.L}/code.py", "def f():\n    return 1\n", T + 100)
        w(f"{cls.R}/code.py", "def f():\n    return 2\n", T)
        w(f"{cls.L}/lonly.txt", "l\n")
        w(f"{cls.R}/ronly.txt", "r\n")
        w(f"{cls.L}/sub/deep/x.txt", "x\n")
        w(f"{cls.L}/data.csv", "id,name,qty\n1,a,10\n2,b,20\n")
        w(f"{cls.R}/data.csv", "id,name,qty\n1,a,11\n3,c,30\n")
        cls.paths = {}

    @classmethod
    def tearDownClass(cls):
        cls.win.close()
        shutil.rmtree(cls.d, ignore_errors=True)
        shutil.rmtree(_TMP, ignore_errors=True)

    def tab(self):
        return self.win.tabs.currentWidget()

    def test_01_folder_compare(self):
        v = self.win.open_compare("folder", self.L, self.R)
        self.assertIsNotNone(v)
        self.assertTrue(wait(lambda: v.S is not None), "folder compare did not finish")
        self.assertEqual(v.counter.get("newer"), 1)
        self.assertEqual(v.counter.get("left"), 2)       # lonly.txt and sub/deep/x.txt
        self.assertIn("code.py", v.items)
        # filters
        v._set_filter("same")
        v._filter_timer.timeout.emit()
        self.assertIn("same.txt", v.items)
        self.assertNotIn("code.py", v.items)
        v._set_filter("newer", True)
        v._filter_timer.timeout.emit()
        self.assertIn("code.py", v.items)
        v._set_filter("all")
        v.search.setText("*.py")
        v._filter_timer.timeout.emit()
        self.assertNotIn("same.txt", v.items)
        self.assertIn("code.py", v.items)
        v.search.setText("")
        v._filter_timer.timeout.emit()
        # flatten
        v.b_flat.setChecked(True)
        self.assertIn("sub/deep/x.txt", v.items)
        v.b_flat.setChecked(False)
        # select by status
        v._set_filter("all")
        v._filter_timer.timeout.emit()
        v.select_status("left")
        self.assertEqual(len(v._sel_nodes()), 2)
        # compare method change refreshes
        settled(v, lambda: v.set_method("crc"))
        v.grab()
        self.assertEqual(v.session_extra()["options"]["method"], "crc")
        # case-insensitive / archives toggles
        settled(v, lambda: v.act_arch.setChecked(True))
        settled(v, lambda: v.act_arch.setChecked(False))
        # align two orphans
        w(f"{self.L}/a_v1.txt", "q\n")
        w(f"{self.R}/a_v2.txt", "q\n")
        settled(v, v.refresh)
        settled(v, lambda: v.align(v.items["a_v1.txt"].data(0, Qt.UserRole), v.items["a_v2.txt"].data(0, Qt.UserRole)))
        self.assertEqual(v.items["a_v1.txt"].data(0, Qt.UserRole).status, "same")
        settled(v, v.clear_aligns)
        os.remove(f"{self.L}/a_v1.txt")
        os.remove(f"{self.R}/a_v2.txt")
        # report
        from lavati import reports
        out = os.path.join(self.d, "rep.html")
        n = reports.write_folder_report(out, "html", self.L, self.R, v.nodes, "differences", v.counter)
        self.assertGreater(n, 2)
        # sync dialog builds a preview (not executed)
        from lavati.sync_dialog import SyncDialog
        dlg = SyncDialog(v.nodes, v.S, [], self.cfg, 0, self.win, self.L, self.R, v.opts.to_dict())
        for c in dlg.cards:
            c.radio.setChecked(True)
            self.assertGreaterEqual(dlg.table.rowCount(), 0)
        dlg.grab()
        dlg.close()

    def test_02_folder_ops_via_view(self):
        v = self.win.open_compare("folder", self.L, self.R)
        wait(lambda: v.S is not None)
        n = v.items["lonly.txt"].data(0, Qt.UserRole)
        from lavati import fsops
        done, errs = fsops.copy_nodes([n], "R", v.S)
        self.assertEqual(errs, [])
        self.assertTrue(os.path.exists(f"{self.R}/lonly.txt"))
        os.remove(f"{self.R}/lonly.txt")

    def test_03_text_and_merge(self):
        v = self.win.open_compare("text", f"{self.L}/code.py", f"{self.R}/code.py")
        self.assertEqual(len(v.hunks), 1)
        v.take("right")
        self.assertEqual(v.panes["R"].lines, v.panes["L"].lines)
        self.win.tabs.setCurrentWidget(v)
        self.win._cur("undo")
        v.grab()
        # 3-way and 2-way merge views
        base, ours, theirs = (os.path.join(self.d, n) for n in ("base.txt", "ours.txt", "theirs.txt"))
        w(base, "a\nb\nc\n")
        w(ours, "A\nb\nc\n")
        w(theirs, "a\nb\nC\n")
        m = self.win.open_merge(ours, base, theirs, os.path.join(self.d, "out.txt"))
        self.assertEqual(len(m.conf), 0)
        m.save()
        with open(os.path.join(self.d, "out.txt")) as f:
            self.assertEqual(f.read(), "A\nb\nC\n")
        m.toggle_auto(m.segs[0] if m.segs[0].kind == "auto" else [s for s in m.segs if s.kind == "auto"][0])
        m.grab()
        m2 = self.win.open_merge(ours, None, theirs, os.path.join(self.d, "out2.txt"))
        self.assertTrue(m2.two_way)
        m2.resolve_all("theirs")
        m2.save()
        m2.grab()

    def test_04_folder_merge(self):
        j = lambda n: os.path.join(self.d, n)
        for r, content in (("fm_b", "a\n"), ("fm_o", "A\n"), ("fm_t", "a\n")):
            w(f"{j(r)}/f.txt", content)
        m = self.win.open_merge(j("fm_o"), j("fm_b"), j("fm_t"), j("fm_out"))
        self.assertTrue(wait(lambda: m.entries))
        self.assertEqual(m.entries[0].action, "ours")
        m.grab()
        m.write()
        self.assertTrue(wait(lambda: os.path.exists(os.path.join(self.d, "fm_out", "f.txt"))))
        with open(os.path.join(self.d, "fm_out", "f.txt")) as f:
            self.assertEqual(f.read(), "A\n")

    def test_05_table_hex_image(self):
        tv = self.win.open_compare("table", f"{self.L}/data.csv", f"{self.R}/data.csv")
        self.assertEqual(tv.result.counts["changed"], 1)
        self.assertEqual(tv.result.counts["left"], 1)
        tv.grab()
        import openpyxl
        p1, p2 = j2 = (os.path.join(self.d, "s1.xlsx"), os.path.join(self.d, "s2.xlsx"))
        for p, val in ((p1, 1), (p2, 2)):
            wb = openpyxl.Workbook()
            wb.active.title = "One"
            wb.active.append(["k", "v"])
            wb.active.append(["a", val])
            ws = wb.create_sheet("Two")
            ws.append(["k", "v"])
            ws.append(["z", 9])
            wb.save(p)
        xv = self.win.open_compare("table", p1, p2)
        self.assertEqual(xv.sources["L"], ["One", "Two"])
        xv.sheet_combos["L"].setCurrentText("Two")
        wait(lambda: xv.sheet["R"] == "Two")
        self.assertEqual(xv.result.counts["same"], 1)
        xv.grab()
        a, b = os.path.join(self.d, "a.bin"), os.path.join(self.d, "b.bin")
        w(a, bytes(range(256)) * 300)
        w(b, bytes(range(256)) * 299 + bytes([1, 2, 3]) * 80)
        hv = self.win.open_compare("hex", a, b)
        self.assertTrue(wait(lambda: hv.ndiff > 0 or hv.diff_rows))
        self.assertGreater(hv.ndiff, 0)
        hv.step(1)
        hv.step(-1)
        hv.grab()
        from PIL import Image
        i1, i2 = os.path.join(self.d, "i1.png"), os.path.join(self.d, "i2.png")
        Image.new("RGB", (40, 30), (10, 20, 30)).save(i1)
        im = Image.new("RGB", (40, 30), (10, 20, 30))
        im.putpixel((5, 5), (250, 20, 30))
        im.save(i2)
        iv = self.win.open_compare("image", i1, i2)
        self.assertIn("pixels differ", iv.info.text())
        iv.tol_spin.setValue(250)
        iv._recompute()
        self.assertIn("0.00%", iv.info.text())
        for m in range(5):
            iv.seg.group.button(m).click()
            iv.grab()

    def test_06_special_kinds(self):
        # version
        v = self.win.open_compare("version", sys.executable, sys.executable)
        self.assertTrue(v.rows)
        v.grab()
        # media
        a, b = os.path.join(self.d, "a.mp3"), os.path.join(self.d, "b.mp3")
        w(a, ts.mp3_bytes("One"))
        w(b, ts.mp3_bytes("Two"))
        mv = self.win.open_compare("media", a, b)
        self.assertTrue(any(r[1] == "Title" and r[4] == "changed" for r in mv.rows))
        mv.grab()
        # registry
        ra, rb = os.path.join(self.d, "a.reg"), os.path.join(self.d, "b.reg")
        w(ra, ts.REG_A)
        w(rb, ts.REG_B)
        rv = self.win.open_compare("registry", ra, rb)
        self.assertTrue(wait(lambda: rv.nodes))
        rv.grab()
        # document (docx -> text compare)
        for name, txt in (("d1.docx", "Hello"), ("d2.docx", "Hello world")):
            with zipfile.ZipFile(os.path.join(self.d, name), "w") as z:
                z.writestr("word/document.xml", '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                                                f'<w:body><w:p><w:r><w:t>{txt}</w:t></w:r></w:p></w:body></w:document>')
        dv = self.win.open_compare("doc", os.path.join(self.d, "d1.docx"), os.path.join(self.d, "d2.docx"))
        self.assertTrue(dv.sides["L"].readonly)
        self.assertEqual(len(dv.hunks), 1)

    def test_07_archives_and_snapshot_as_folders(self):
        zl, zr = os.path.join(self.d, "l.zip"), os.path.join(self.d, "r.zip")
        for p, c in ((zl, "one"), (zr, "two")):
            with zipfile.ZipFile(p, "w") as z:
                z.writestr("in/f.txt", c)
        from lavati.home import detect
        self.assertEqual(detect(zl, zr)[0], "folder")
        v = self.win.open_compare("folder", zl, zr)
        self.assertTrue(wait(lambda: v.S is not None))
        settled(v, lambda: v.set_method("crc"))
        self.assertIn(v.items["in/f.txt"].data(0, Qt.UserRole).status, ("changed", "newer", "older"))
        from lavati import vfs
        snap = os.path.join(self.d, "x.lsnap")
        vfs.capture_snapshot(vfs.LocalFS(self.L), self.L, snap)
        sv = self.win.open_compare("folder", snap, self.R)
        self.assertTrue(wait(lambda: sv.S is not None))
        sv.grab()

    def test_08_home_pages_and_tabs(self):
        h = self.win.home
        for i in range(5):
            h.show_page(i)
            h.grab()
        h._fill_formats()
        h._fill_scripts()
        self.win.tabs.setCurrentIndex(0)
        self.win._update_actions()
        count = self.win.tabs.count()
        self.assertGreater(count, 10)
        self.win._close_others(1)
        self.assertEqual(self.win.tabs.count(), 2)

    def test_10_git_browser(self):
        import subprocess
        from lavati import gitvfs
        if gitvfs.check_git():
            self.skipTest("git not available")
        repo = os.path.join(self.d, "grepo")
        os.makedirs(repo)
        env = dict(os.environ, GIT_AUTHOR_NAME="T", GIT_AUTHOR_EMAIL="t@x", GIT_COMMITTER_NAME="T", GIT_COMMITTER_EMAIL="t@x",
                   GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull)

        def g(*a):
            subprocess.run(["git", "-C", repo, "-c", "commit.gpgsign=false", *a], check=True, capture_output=True, env=env)
        g("init", "-q", "-b", "main")
        body = "".join(f"a long enough line number {i} for similarity\n" for i in range(25))
        w(f"{repo}/keep.txt", "keep\n")
        w(f"{repo}/old/name.txt", body)
        g("add", "-A")
        g("commit", "-q", "-m", "first")
        os.remove(f"{repo}/old/name.txt")
        w(f"{repo}/new/name2.txt", body + "one more\n")
        g("add", "-A")
        g("commit", "-q", "-m", "second")
        a, b = gitvfs.make_spec(repo, "HEAD~1"), gitvfs.make_spec(repo, "HEAD")
        v = self.win.open_compare("folder", a, b, {"git_renames": True})
        self.assertTrue(wait(lambda: v.S is not None))
        node = v.items["new/name2.txt"].data(0, Qt.UserRole)
        self.assertEqual(node.renamed_from, "old/name.txt")
        v.grab()
        settled(v, lambda: v._set_renames(False))
        self.assertIn("old/name.txt", v.items)
        from lavati.git_view import GitCompareDialog, HistoryDialog
        dlg = GitCompareDialog(self.win, repo)
        self.assertIn("main", dlg.refs["branches"])
        dlg.src["L"].setCurrentIndex(dlg.src["L"].findData("commit"))
        dlg.val["L"].setEditText("HEAD~1")
        dlg.src["R"].setCurrentIndex(dlg.src["R"].findData("head"))
        dlg._go(False)
        self.assertEqual(dlg.result_specs, (a, b))
        dlg.grab()
        h = HistoryDialog(repo, "HEAD", self.win)
        self.assertEqual([c[4] for c in h.commits], ["second", "first"])
        h.table.selectRow(0)
        h._ok()
        self.assertEqual(len(h.picked), 2)
        h.grab()
        m = self.win.open_merge(a, gitvfs.make_spec(repo, "HEAD~1"), b)
        self.assertTrue(wait(lambda: m.entries))
        self.assertEqual(m.output, "")
        self.win.home.show_page(4)
        self.win.home._git_refresh()

    def test_09_settings_persist(self):
        self.cfg["compare_method"] = "binary"
        c2 = config.Config(self.cfg.path)
        self.assertEqual(c2["compare_method"], "binary")


if __name__ == "__main__":
    unittest.main()
