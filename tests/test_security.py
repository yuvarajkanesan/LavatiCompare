import os
import sys
import unittest
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from lavati import gitvfs, remote


class SecurityTests(unittest.TestCase):
    def test_revision_cannot_be_an_option(self):
        for bad in ("--output=x", "-p", "a\nb", ""):
            with self.assertRaises(gitvfs.GitError):
                gitvfs.safe_rev(bad)
        self.assertEqual(gitvfs.safe_rev("main"), "main")

    def test_password_not_sent_over_plain_http(self):
        with self.assertRaises(remote.RemoteError):
            remote.refuse_cleartext_login("http://files.example.com/dav")
        remote.refuse_cleartext_login("https://files.example.com/dav")
        remote.refuse_cleartext_login("http://127.0.0.1:8080/dav")

    def test_redirect_to_other_host_drops_credentials(self):
        h = remote._SafeRedirect()
        req = urllib.request.Request("https://a.example.com/x", headers={"Authorization": "Bearer s", "X-Keep": "1"})
        new = h.redirect_request(req, None, 302, "Found", {}, "https://b.example.com/y")
        self.assertNotIn("Authorization", {k.title() for k in new.headers})
        self.assertIn("X-keep", {k.capitalize() for k in new.headers})
        same = h.redirect_request(req, None, 302, "Found", {}, "https://a.example.com/z")
        self.assertIn("Authorization", {k.title() for k in same.headers})
        self.assertIsNone(h.redirect_request(req, None, 302, "Found", {}, "http://a.example.com/z"))


if __name__ == "__main__":
    unittest.main()


class LargeDiffTests(unittest.TestCase):
    def test_anchor_diff_reconstructs_and_matches_small_path(self):
        import random
        from lavati import engine
        random.seed(5)
        a = [f"l{i}" for i in range(3000)] + ["dup"] * 5
        b = list(a)
        for _ in range(60):
            b[random.randrange(len(b))] += "x"
        for _ in range(30):
            del b[random.randrange(len(b))]
        for _ in range(30):
            b.insert(random.randrange(len(b)), "new")
        old = engine.SMALL_CHUNK
        try:
            engine.SMALL_CHUNK = 1000                      # force the anchor-splitting path
            ops = engine.diff_opcodes(a, b)
        finally:
            engine.SMALL_CHUNK = old
        out = []
        for tag, i1, i2, j1, j2 in ops:
            out += a[i1:i2] if tag == "equal" else b[j1:j2]
        self.assertEqual(out, b)
        self.assertEqual(ops[0][1], 0)
        self.assertEqual(ops[-1][2], len(a))

    def test_oversize_text_refused(self):
        import tempfile
        from lavati import engine
        p = os.path.join(tempfile.mkdtemp(), "big.txt")
        open(p, "w").write("x")
        old = engine.MAX_TEXT_BYTES
        try:
            engine.MAX_TEXT_BYTES = 0
            with self.assertRaises(engine.TooLarge):
                engine.load_text_file(p)
        finally:
            engine.MAX_TEXT_BYTES = old
