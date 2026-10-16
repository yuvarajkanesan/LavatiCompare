"""Diagnostics (error log, problem report) and the update check."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.parse

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
from lavati import diagnostics, remote, updates  # noqa: E402


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="lavati-diag-")
        self.saved = (sys.excepthook, threading.excepthook, diagnostics._installed, diagnostics.LOG_DIR, diagnostics.LOG_PATH,
                      diagnostics.CRASH_PATH)
        diagnostics._installed = False
        diagnostics.last_error = ""

    def tearDown(self):
        import logging
        for h in list(diagnostics.log.handlers):
            h.close()
            diagnostics.log.removeHandler(h)
        sys.excepthook, threading.excepthook = self.saved[0], self.saved[1]
        diagnostics._installed = self.saved[2]
        diagnostics.LOG_DIR, diagnostics.LOG_PATH, diagnostics.CRASH_PATH = self.saved[3:]
        logging.shutdown()
        if diagnostics._fault_file:
            diagnostics._fault_file.close()
            diagnostics._fault_file = None
        shutil.rmtree(self.d, ignore_errors=True)

    def test_unhandled_exception_is_logged_and_reported(self):
        import faulthandler
        diagnostics.setup(self.d)
        seen = []
        diagnostics.on_error = seen.append
        try:
            raise ValueError("boom for the test")
        except ValueError:
            sys.excepthook(*sys.exc_info())
        diagnostics.on_error = None
        for h in diagnostics.log.handlers:
            h.flush()
        with open(os.path.join(self.d, "lavati.log"), encoding="utf-8") as f:
            text = f.read()
        self.assertIn("boom for the test", text)
        self.assertIn("starting on", text)
        self.assertIn("ValueError", diagnostics.last_error)
        self.assertEqual(len(seen), 1)
        self.assertIn("boom for the test", diagnostics.report_text())
        faulthandler.disable()

    def test_thread_exceptions_are_logged(self):
        diagnostics.setup(self.d)

        def boom():
            raise RuntimeError("in a worker thread")
        t = threading.Thread(target=boom, name="worker-x")
        t.start()
        t.join()
        self.assertIn("in a worker thread", diagnostics.last_error)
        import faulthandler
        faulthandler.disable()

    def test_report_hides_the_user_name_and_urls_stay_short(self):
        home = os.path.expanduser("~")
        shared = diagnostics.sanitize(f"error opening {home}\\Documents\\x.txt")
        self.assertNotIn(home, shared)
        url = diagnostics.issue_url("A title", "line\n" * 5000)
        self.assertTrue(url.startswith("https://github.com/yuvarajkanesan/LavatiCompare/issues/new?"))
        self.assertLess(len(url), 9000)
        q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        self.assertEqual(q["title"], ["A title"])
        self.assertIn("cut", q["body"][0])

    def test_report_dialog_shows_the_text_that_would_be_shared(self):
        from lavati.app import ReportProblemDialog
        diagnostics.last_error = "Traceback ... KeyError: 'demo'"
        dlg = ReportProblemDialog()
        txt = dlg.text.toPlainText()
        self.assertIn("What happened", txt)
        self.assertIn("KeyError: 'demo'", txt)
        self.assertIn("Lavati Compare", txt)


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.orig = remote.http_request

    def tearDown(self):
        remote.http_request = self.orig

    def answer(self, status, body=b""):
        remote.http_request = lambda *a, **k: (status, {}, body)

    def test_version_parsing_and_ordering(self):
        self.assertEqual(updates.parse_version("v2.10.1-beta"), (2, 10, 1))
        self.assertEqual(updates.parse_version("garbage"), ())
        self.assertTrue(updates.is_newer("2.1", "2.0"))
        self.assertTrue(updates.is_newer("v2.0.1", "2.0"))
        self.assertTrue(updates.is_newer("2.10", "2.9"))               # numbers, not text
        self.assertFalse(updates.is_newer("2.0", "2.0.0"))
        self.assertFalse(updates.is_newer("1.9", "2.0"))
        self.assertFalse(updates.is_newer("", "2.0"))

    def test_check_results(self):
        body = json.dumps({"tag_name": "v9.0", "html_url": "https://github.com/yuvarajkanesan/LavatiCompare/releases/tag/v9.0",
                           "body": "notes"}).encode()
        self.answer(200, body)
        r = updates.check("2.0")
        self.assertEqual((r["status"], r["version"]), ("newer", "9.0"))
        self.assertEqual(updates.check("9.0")["status"], "current")
        self.answer(404)
        self.assertEqual(updates.check("2.0")["status"], "none")
        self.answer(403)
        with self.assertRaises(updates.UpdateError):
            updates.check("2.0")
        self.answer(200, b"<html>not json")
        with self.assertRaises(updates.UpdateError):
            updates.check("2.0")

    def test_download_page_is_always_on_our_repository(self):
        self.answer(200, json.dumps({"tag_name": "v9.0", "html_url": "https://evil.example.com/x"}).encode())
        self.assertTrue(updates.check("2.0")["url"].startswith("https://github.com/yuvarajkanesan/LavatiCompare"))

    def test_offline_is_a_clean_error(self):
        def boom(*a, **k):
            raise remote.RemoteError("Network error: offline")
        remote.http_request = boom
        with self.assertRaises(updates.UpdateError):
            updates.check("2.0")

    def test_manual_check_through_the_window(self):
        import tempfile as tf
        from lavati import config
        from lavati.app import MainWindow
        d = tf.mkdtemp()
        try:
            win = MainWindow(config.Config(os.path.join(d, "c.json")))
            win._update_result = lambda *a, **k: None          # the real handler opens a modal box
            got = []
            win.updateChecked.connect(lambda res, manual: got.append((res, manual)))
            self.answer(200, json.dumps({"tag_name": "v9.0", "html_url": "https://github.com/yuvarajkanesan/LavatiCompare/releases"}).encode())
            win.check_for_updates(True)
            import time
            end = time.time() + 5
            while not got and time.time() < end:
                app.processEvents()
                time.sleep(0.02)
            self.assertEqual(got[0][0]["status"], "newer")
            self.assertTrue(got[0][1])
        finally:
            shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
