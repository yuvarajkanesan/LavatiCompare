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
