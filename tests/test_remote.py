import datetime
import hashlib
import http.server
import json
import os
import re
import shutil
import socketserver
import sys
import tempfile
import threading
import time
import unittest
import urllib.parse
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
for extra in filter(None, os.environ.get("LAVATI_TESTDEPS", "").split(os.pathsep)):
    sys.path.append(extra)
from lavati import engine, fsops, remote, vfs  # noqa: E402

T = 1_700_000_000


def w(path, data, mtime=T):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(data)
    os.utime(path, (mtime, mtime))


class Quiet(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass


def serve(handler):
    srv = socketserver.ThreadingTCPServer(("127.0.0.1", 0), handler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Helpers(unittest.TestCase):
    def test_sigv4_known_vector(self):
        # AWS Signature V4 test suite: get-vanilla
        h = remote.sigv4_headers("GET", "example.amazonaws.com", "/", {}, "us-east-1", "service", "AKIDEXAMPLE",
                                 "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY", None, None,
                                 now=datetime.datetime(2015, 8, 30, 12, 36, 0, tzinfo=datetime.timezone.utc),
                                 content_sha_header=False)
        self.assertIn("Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31", h["Authorization"])
        self.assertIn("SignedHeaders=host;x-amz-date", h["Authorization"])

    def test_sigv4_s3_docs_example(self):
        # AWS S3 docs: GET Object with Range header
        h = remote.sigv4_headers("GET", "examplebucket.s3.amazonaws.com", "/test.txt", {}, "us-east-1", "s3",
                                 "AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", None,
                                 hashlib.sha256(b"").hexdigest(),
                                 now=datetime.datetime(2013, 5, 24, 0, 0, 0, tzinfo=datetime.timezone.utc),
                                 extra_headers={"range": "bytes=0-9"})
        self.assertIn("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41", h["Authorization"])

    def test_ftp_list_parsing(self):
        e = remote.parse_ftp_list("-rw-r--r--   1 user group     1234 Jan 15 2023 report final.txt")
        self.assertEqual((e.name, e.is_dir, e.size), ("report final.txt", False, 1234))
        e = remote.parse_ftp_list("drwxr-xr-x   2 user group     4096 Mar  3 12:30 sub")
        self.assertTrue(e.is_dir)
        e = remote.parse_ftp_list("lrwxrwxrwx   1 u g 4 Jan 15 2023 link -> target")
        self.assertEqual(e.name, "link")
        e = remote.parse_ftp_list("01-15-23  02:30PM       <DIR>          Docs")
        self.assertTrue(e.is_dir)
        e = remote.parse_ftp_list("01-15-2023  02:30PM             2048 a b.txt")
        self.assertEqual((e.name, e.size), ("a b.txt", 2048))
        self.assertIsNone(remote.parse_ftp_list("total 12"))
        self.assertEqual(remote.parse_mlsx_time("20240102030405"), 1704164645)

    def test_urls_and_secrets(self):
        self.assertEqual(remote.split_secrets("ftp://bob:pw%40x@host:2121/dir"), ("ftp://bob@host:2121/dir", "pw@x"))
        self.assertEqual(remote.split_secrets("ftp://host/dir"), ("ftp://host/dir", None))
        self.assertTrue(vfs.is_remote_spec("sftp://h/x"))
        self.assertFalse(vfs.is_remote_spec("C:\\dir"))
        with self.assertRaises(remote.RemoteError):
            remote.open_url("gopher://x/")
        with self.assertRaises(remote.NeedCredentials):
            remote.open_url("dropbox:///")
        remote.set_credential("dropbox", "tok")
        self.assertEqual(remote.get_credential("dropbox"), "tok")
        self.assertEqual(remote.parse_iso("2024-01-02T03:04:05Z"), 1704164645)
        self.assertEqual(remote.parse_iso("2024-01-02T05:04:05+02:00"), 1704164645)


# --------------------------------------------------------------------- WebDAV
class DavHandler(Quiet):
    store = {}          # path -> bytes | None (dir)
    mtimes = {}

    def _path(self):
        return urllib.parse.unquote(urllib.parse.urlparse(self.path).path).rstrip("/") or "/"

    def _auth_ok(self):
        return self.headers.get("Authorization") == "Basic dXNlcjpwdw=="       # user:pw

    def _deny(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="x"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_PROPFIND(self):
        if not self._auth_ok():
            return self._deny()
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        p = self._path()
        S = self.store
        if p not in S:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        items = [p]
        if S[p] is None and self.headers.get("Depth") != "0":
            items += [q for q in S if q != p and q.rsplit("/", 1)[0] == (p if p != "/" else "") and q != "/"]
        out = ['<?xml version="1.0"?><d:multistatus xmlns:d="DAV:">']
        for q in items:
            isdir = S[q] is None
            lm = time.strftime("%a, %d %b %Y %H:%M:%S GMT", time.gmtime(self.mtimes.get(q, T)))
            out.append(f"<d:response><d:href>{urllib.parse.quote(q)}{'/' if isdir and q != '/' else ''}</d:href><d:propstat><d:prop>"
                       f"<d:resourcetype>{'<d:collection/>' if isdir else ''}</d:resourcetype>"
                       f"{'' if isdir else f'<d:getcontentlength>{len(S[q])}</d:getcontentlength>'}"
                       f"<d:getlastmodified>{lm}</d:getlastmodified></d:prop><d:status>HTTP/1.1 200 OK</d:status></d:propstat></d:response>")
        out.append("</d:multistatus>")
        body = "".join(out).encode()
        self.send_response(207)
        self.send_header("Content-Type", "application/xml")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self._auth_ok():
            return self._deny()
        p = self._path()
        if self.store.get(p) is None:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        d = self.store[p]
        self.send_response(200)
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)

    def do_PUT(self):
        if not self._auth_ok():
            return self._deny()
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n)
        p = self._path()
        if p.rsplit("/", 1)[0] not in ("",) and p.rsplit("/", 1)[0] not in self.store:
            self.send_response(409)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.store[p] = data
        if self.headers.get("X-OC-Mtime"):
            self.mtimes[p] = int(self.headers["X-OC-Mtime"])
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_MKCOL(self):
        if not self._auth_ok():
            return self._deny()
        p = self._path()
        code = 405 if p in self.store else 201
        self.store.setdefault(p, None)
        self.send_response(code)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_DELETE(self):
        if not self._auth_ok():
            return self._deny()
        p = self._path()
        for q in [q for q in self.store if q == p or q.startswith(p + "/")]:
            del self.store[q]
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_MOVE(self):
        if not self._auth_ok():
            return self._deny()
        p = self._path()
        dest = urllib.parse.unquote(urllib.parse.urlparse(self.headers["Destination"]).path).rstrip("/")
        for q in [q for q in self.store if q == p or q.startswith(p + "/")]:
            self.store[dest + q[len(p):]] = self.store.pop(q)
        self.send_response(201)
        self.send_header("Content-Length", "0")
        self.end_headers()


class WebDAVTests(unittest.TestCase):
    def setUp(self):
        DavHandler.store = {"/": None, "/data": None, "/data/a.txt": b"hello", "/data/sub": None, "/data/sub/b.txt": b"bee"}
        DavHandler.mtimes = {"/data/a.txt": T}
        self.srv = serve(DavHandler)
        self.url = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        self.srv.shutdown()
        shutil.rmtree(self.d, ignore_errors=True)

    def test_list_read_write(self):
        fs, root = remote.open_url(self.url.replace("http://", "dav://user:pw@") + "/data")
        self.assertEqual(root, "/data")
        names = sorted((e.name, e.is_dir, e.size) for e in fs.scandir(root))
        self.assertEqual(names, [("a.txt", False, 5), ("sub", True, 0)])
        self.assertEqual(fs.stat("/data/a.txt").mtime, T)
        with fs.open("/data/sub/b.txt") as f:
            self.assertEqual(f.read(), b"bee")
        src = os.path.join(self.d, "up.txt")
        w(src, "uploaded", T + 5)
        fs.put_file(src, "/data/new/deep/up.txt", T + 5)
        self.assertEqual(DavHandler.store["/data/new/deep/up.txt"], b"uploaded")
        self.assertEqual(DavHandler.mtimes["/data/new/deep/up.txt"], T + 5)
        fs.rename("/data/new/deep/up.txt", "/data/moved/up.txt")
        self.assertIn("/data/moved/up.txt", DavHandler.store)
        fs.remove("/data/new")
        self.assertNotIn("/data/new/deep", DavHandler.store)
        self.assertEqual(fs.stat("/data/nothing"), None)

    def test_bad_password(self):
        with self.assertRaises(remote.RemoteError):
            remote.open_url(self.url.replace("http://", "dav://user:wrong@") + "/data")

    def test_compare_and_sync_local_vs_dav(self):
        local = os.path.join(self.d, "L")
        w(f"{local}/a.txt", "hello", T)             # same size+time as remote
        w(f"{local}/only.txt", "local only", T)
        w(f"{local}/sub/b.txt", "BEE!", T + 100)    # newer
        fs, root = remote.open_url(self.url.replace("http://", "dav://user:pw@") + "/data")
        S = fsops.Sides(vfs.LocalFS(), local, fs, root)
        nodes = engine.compare_tree(S.lfs, local, S.rfs, root, engine.CompareOptions(time_tol=2))
        st = {n.rel: n.status for n in engine.walk(nodes)}
        self.assertEqual(st["a.txt"], "same")
        self.assertEqual(st["only.txt"], "left")
        self.assertEqual(st["sub/b.txt"], "newer")
        ops = fsops.build_plan(nodes, "update_right")
        done, errors = fsops.run_plan(ops, S, backup=True, trash=False, stamp="S")
        self.assertEqual(errors, [])
        self.assertEqual(DavHandler.store["/data/only.txt"], b"local only")
        self.assertEqual(DavHandler.store["/data/sub/b.txt"], b"BEE!")
        self.assertEqual(DavHandler.store["/data/.lavati-backup/S/sub/b.txt"], b"bee")      # remote backup
        # crc method downloads and compares content
        nodes = engine.compare_tree(S.lfs, local, S.rfs, root, engine.CompareOptions(method="crc", patterns=[]))
        self.assertTrue(all(n.status == "same" for n in engine.walk(nodes) if not n.is_dir))


# ----------------------------------------------------------------------- S3
class S3Handler(Quiet):
    objs = {}
    region = "us-east-1"
    bad_sigs = []

    def _verify(self, body_hash):
        auth = self.headers.get("Authorization", "")
        m = re.search(r"SignedHeaders=([^,]+), Signature=(\w+)", auth)
        signed = m.group(1).split(";")
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}
        extra = {h: self.headers[h] for h in signed if h not in ("host", "x-amz-date", "x-amz-content-sha256", "x-amz-security-token")}
        now = datetime.datetime.strptime(self.headers["x-amz-date"], "%Y%m%dT%H%M%SZ").replace(tzinfo=datetime.timezone.utc)
        h = remote.sigv4_headers(self.command, self.headers["Host"], u.path, q, self.region, "s3", "AK", "SK", None,
                                 self.headers["x-amz-content-sha256"], now=now, extra_headers=extra)
        ok = h["Authorization"] == auth
        if not ok:
            self.bad_sigs.append(self.path)
        return ok

    def _send(self, code, body=b"", ctype="application/xml", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _key(self):
        p = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        return p[len("/bkt/"):] if p.startswith("/bkt/") else ""

    def do_GET(self):
        if not self._verify(None):
            return self._send(403)
        u = urllib.parse.urlparse(self.path)
        q = {k: v[0] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}
        if "list-type" in q:
            prefix, delim = q.get("prefix", ""), q.get("delimiter")
            contents, prefixes = [], set()
            for k in sorted(self.objs):
                if not k.startswith(prefix):
                    continue
                rest = k[len(prefix):]
                if delim and delim in rest:
                    prefixes.add(prefix + rest.split(delim)[0] + delim)
                else:
                    contents.append(k)
            maxk = int(q.get("max-keys", 1000))
            xml = ['<?xml version="1.0"?><ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">',
                   "<IsTruncated>false</IsTruncated>"]
            for k in contents[:maxk]:
                data, mt = self.objs[k]
                lm = time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(mt))
                xml.append(f"<Contents><Key>{k}</Key><LastModified>{lm}</LastModified><ETag>&quot;{hashlib.md5(data).hexdigest()}&quot;</ETag><Size>{len(data)}</Size></Contents>")
            for p in sorted(prefixes):
                xml.append(f"<CommonPrefixes><Prefix>{p}</Prefix></CommonPrefixes>")
            xml.append("</ListBucketResult>")
            return self._send(200, "".join(xml).encode())
        k = self._key()
        if k not in self.objs:
            return self._send(404, b"<Error><Code>NoSuchKey</Code></Error>")
        self._send(200, self.objs[k][0], "application/octet-stream")

    def do_PUT(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n)
        if not self._verify(None):
            return self._send(403)
        k = self._key()
        src = self.headers.get("x-amz-copy-source")
        if src:
            sk = urllib.parse.unquote(src)[len("/bkt/"):]
            self.objs[k] = (self.objs[sk][0], T)
            return self._send(200, b"<CopyObjectResult/>")
        if self.headers.get("x-amz-content-sha256") != hashlib.sha256(data).hexdigest():
            return self._send(400, b"<Error><Code>XAmzContentSHA256Mismatch</Code></Error>")
        mt = int(self.headers.get("x-amz-meta-lavati-mtime") or T)
        self.objs[k] = (data, time.time())
        self._send(200)

    def do_DELETE(self):
        if not self._verify(None):
            return self._send(403)
        self.objs.pop(self._key(), None)
        self._send(204)


class S3Tests(unittest.TestCase):
    def setUp(self):
        S3Handler.objs = {"dir/a.txt": (b"alpha", T), "dir/sub/b.txt": (b"beta", T), "top.txt": (b"top", T)}
        S3Handler.bad_sigs = []
        self.srv = serve(S3Handler)
        self.endpoint = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self.d = tempfile.mkdtemp()
        remote.set_credential("s3://bkt", "AK:SK")

    def tearDown(self):
        self.srv.shutdown()
        shutil.rmtree(self.d, ignore_errors=True)

    def open(self, prefix=""):
        return remote.open_url(f"s3://bkt/{prefix}?endpoint={urllib.parse.quote(self.endpoint)}")

    def test_list_download_upload(self):
        fs, root = self.open("dir")
        self.assertEqual(root, "/dir")
        self.assertEqual(sorted((e.name, e.is_dir) for e in fs.scandir(root)), [("a.txt", False), ("sub", True)])
        with fs.open("/dir/sub/b.txt") as f:
            self.assertEqual(f.read(), b"beta")
        # cheap checksum from the ETag, no download needed
        self.assertEqual(fs.checksum("/dir/a.txt", "md5"), hashlib.md5(b"alpha").hexdigest())
        src = os.path.join(self.d, "u.txt")
        w(src, "payload with spaces & symbols")
        fs.put_file(src, "/dir/new folder/u v.txt", T)
        self.assertEqual(S3Handler.objs["dir/new folder/u v.txt"][0], b"payload with spaces & symbols")
        fs.rename("/dir/a.txt", "/dir/moved.txt")
        self.assertIn("dir/moved.txt", S3Handler.objs)
        self.assertNotIn("dir/a.txt", S3Handler.objs)
        fs.remove("/dir/sub")
        self.assertNotIn("dir/sub/b.txt", S3Handler.objs)
        self.assertEqual(S3Handler.bad_sigs, [])           # every request carried a valid signature
        with self.assertRaises(remote.RemoteError):
            fs.set_mtime("/dir/moved.txt", 1)

    def test_compare_uses_etag(self):
        local = os.path.join(self.d, "L")
        w(f"{local}/a.txt", "alpha")
        w(f"{local}/sub/b.txt", "BETA")
        fs, root = self.open("dir")
        nodes = engine.compare_tree(vfs.LocalFS(), local, fs, root, engine.CompareOptions(method="crc"))
        st = {n.rel: n.status for n in engine.walk(nodes) if not n.is_dir}
        self.assertEqual(st["a.txt"], "same")
        self.assertNotEqual(st["sub/b.txt"], "same")

    def test_missing_credentials(self):
        remote._creds.pop("s3://bkt", None)
        for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
            os.environ.pop(k, None)
        with self.assertRaises(remote.NeedCredentials):
            self.open("dir")


# ---------------------------------------------------------------- Dropbox
class DropboxHandler(Quiet):
    files = {}
    uploads = []

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(n)

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        if self.headers.get("Authorization") != "Bearer good":
            return self._json(401, {"error": "bad token"})
        body = self._body()
        p = self.path
        if p.endswith("get_current_account"):
            return self._json(200, {"name": "x"})
        if p.endswith("files/list_folder"):
            path = json.loads(body)["path"]
            prefix = path + "/"
            ents = []
            for k, (data, mt) in sorted(self.files.items()):
                if k.startswith(prefix) and "/" not in k[len(prefix):]:
                    ents.append({".tag": "file", "name": k[len(prefix):], "size": len(data),
                                 "client_modified": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mt))})
            subs = {k[len(prefix):].split("/")[0] for k in self.files if k.startswith(prefix) and "/" in k[len(prefix):]}
            ents += [{".tag": "folder", "name": s} for s in sorted(subs)]
            return self._json(200, {"entries": ents, "has_more": False, "cursor": "c"})
        if p.endswith("files/download"):
            arg = json.loads(self.headers["Dropbox-API-Arg"])
            d = self.files[arg["path"]][0]
            self.send_response(200)
            self.send_header("Content-Length", str(len(d)))
            self.end_headers()
            self.wfile.write(d)
            return
        if p.endswith("files/upload"):
            arg = json.loads(self.headers["Dropbox-API-Arg"])
            self.uploads.append(arg)
            mt = calendar_ts(arg.get("client_modified")) if arg.get("client_modified") else time.time()
            self.files[arg["path"]] = (body, mt)
            return self._json(200, {"name": "x"})
        if p.endswith("files/delete_v2"):
            path = json.loads(body)["path"]
            for k in [k for k in self.files if k == path or k.startswith(path + "/")]:
                del self.files[k]
            return self._json(200, {})
        if p.endswith("files/move_v2"):
            a = json.loads(body)
            self.files[a["to_path"]] = self.files.pop(a["from_path"])
            return self._json(200, {})
        if p.endswith("create_folder_v2"):
            return self._json(200, {})
        self._json(404, {"error": "x"})


def calendar_ts(s):
    import calendar
    return calendar.timegm(time.strptime(s, "%Y-%m-%dT%H:%M:%SZ"))


class DropboxTests(unittest.TestCase):
    def setUp(self):
        DropboxHandler.files = {"/docs/a.txt": (b"AAA", T), "/docs/sub/b.txt": (b"B", T)}
        DropboxHandler.uploads = []
        self.srv = serve(DropboxHandler)
        base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        self._old = (remote.DROPBOX_API, remote.DROPBOX_CONTENT)
        remote.DROPBOX_API, remote.DROPBOX_CONTENT = base + "/2", base + "/2"
        self.d = tempfile.mkdtemp()

    def tearDown(self):
        remote.DROPBOX_API, remote.DROPBOX_CONTENT = self._old
        self.srv.shutdown()
        shutil.rmtree(self.d, ignore_errors=True)

    def test_flow(self):
        remote.set_credential("dropbox", "good")
        fs, root = remote.open_url("dropbox:///docs")
        self.assertEqual(sorted((e.name, e.is_dir) for e in fs.scandir(root)), [("a.txt", False), ("sub", True)])
        self.assertEqual(fs.stat("/docs/a.txt").mtime, T)
        with fs.open("/docs/a.txt") as f:
            self.assertEqual(f.read(), b"AAA")
        src = os.path.join(self.d, "n.txt")
        w(src, "new", T + 9)
        fs.put_file(src, "/docs/n.txt", T + 9)
        self.assertEqual(DropboxHandler.files["/docs/n.txt"][1], T + 9)           # client_modified preserved
        fs.rename("/docs/n.txt", "/docs/m.txt")
        fs.remove("/docs/sub")
        self.assertNotIn("/docs/sub/b.txt", DropboxHandler.files)

    def test_bad_token(self):
        remote.set_credential("dropbox", "bad")
        with self.assertRaises(remote.NeedCredentials):
            remote.open_url("dropbox:///docs")


# ------------------------------------------------------------------ OneDrive
class GraphHandler(Quiet):
    files = {}
    base = ""

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _path(self):
        u = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        m = re.match(r"/v1\.0/me/drive/root(?::(/[^:]*):)?(/children|/content|/createUploadSession)?$", u)
        return (m.group(1) or "/", m.group(2)) if m else (None, None)

    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer good":
            return self._json(401, {})
        if urllib.parse.urlparse(self.path).path == "/v1.0/me/drive":
            return self._json(200, {"id": "d"})
        p, kind = self._path()
        if p is None:
            return self._json(404, {})
        if kind == "/children":
            prefix = p.rstrip("/") + "/"
            ents, subs = [], set()
            for k, (d, mt) in sorted(self.files.items()):
                if k.startswith(prefix):
                    rest = k[len(prefix):]
                    if "/" in rest:
                        subs.add(rest.split("/")[0])
                    else:
                        ents.append({"name": rest, "size": len(d), "fileSystemInfo": {"lastModifiedDateTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mt))}, "file": {}})
            ents += [{"name": s, "folder": {}} for s in sorted(subs)]
            return self._json(200, {"value": ents})
        if kind == "/content":
            if p not in self.files:
                return self._json(404, {})
            # redirect like the real service; the target must NOT receive the bearer token
            self.send_response(302)
            self.send_header("Location", f"{self.base}/blob{p}")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if p in self.files or any(k.startswith(p.rstrip('/') + '/') for k in self.files) or p == "/":
            return self._json(200, {"id": "id" + p})
        self._json(404, {})

    def do_PUT(self):
        n = int(self.headers.get("Content-Length") or 0)
        data = self.rfile.read(n)
        if self.headers.get("Authorization") != "Bearer good":
            return self._json(401, {})
        p, kind = self._path()
        self.files[p] = (data, time.time())
        self._json(201, {})

    def do_PATCH(self):
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}")
        p, kind = self._path()
        if "fileSystemInfo" in body and p in self.files:
            self.files[p] = (self.files[p][0], calendar_ts(body["fileSystemInfo"]["lastModifiedDateTime"]))
        self._json(200, {})

    def do_DELETE(self):
        p, kind = self._path()
        for k in [k for k in self.files if k == p or k.startswith(p + "/")]:
            del self.files[k]
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def do_POST(self):
        n = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(n)
        self._json(201, {"id": "x"})


class BlobHandler(Quiet):
    saw_auth = []

    def do_GET(self):
        BlobHandler.saw_auth.append(self.headers.get("Authorization"))
        p = self.path[len("/blob"):]
        d = GraphHandler.files[p][0]
        self.send_response(200)
        self.send_header("Content-Length", str(len(d)))
        self.end_headers()
        self.wfile.write(d)


class OneDriveTests(unittest.TestCase):
    def setUp(self):
        self.blob = serve(BlobHandler)
        GraphHandler.files = {"/Docs/a.txt": (b"AAA", T), "/Docs/sub/b.txt": (b"B", T)}
        GraphHandler.base = f"http://127.0.0.1:{self.blob.server_address[1]}"
        self.srv = serve(GraphHandler)
        self._old = remote.GRAPH
        remote.GRAPH = f"http://127.0.0.1:{self.srv.server_address[1]}/v1.0"
        self.d = tempfile.mkdtemp()
        BlobHandler.saw_auth = []

    def tearDown(self):
        remote.GRAPH = self._old
        self.srv.shutdown()
        self.blob.shutdown()
        shutil.rmtree(self.d, ignore_errors=True)

    def test_flow(self):
        remote.set_credential("onedrive", "good")
        fs, root = remote.open_url("onedrive:///Docs")
        self.assertEqual(sorted((e.name, e.is_dir) for e in fs.scandir(root)), [("a.txt", False), ("sub", True)])
        with fs.open("/Docs/a.txt") as f:
            self.assertEqual(f.read(), b"AAA")
        self.assertEqual(BlobHandler.saw_auth, [None])            # bearer token not leaked to the download host
        src = os.path.join(self.d, "n.txt")
        w(src, "new", T + 7)
        fs.put_file(src, "/Docs/n.txt", T + 7)
        self.assertEqual(GraphHandler.files["/Docs/n.txt"][0], b"new")
        self.assertEqual(GraphHandler.files["/Docs/n.txt"][1], T + 7)
        fs.remove("/Docs/n.txt")
        self.assertNotIn("/Docs/n.txt", GraphHandler.files)

    def test_bad_token(self):
        remote.set_credential("onedrive", "nope")
        with self.assertRaises(remote.NeedCredentials):
            remote.open_url("onedrive:///Docs")


# ----------------------------------------------------------------------- FTP
try:
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import FTPServer
    HAVE_FTPD = True
except Exception:                                    # pragma: no cover
    HAVE_FTPD = False


@unittest.skipUnless(HAVE_FTPD, "pyftpdlib not installed")
class FTPTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.root = os.path.join(self.d, "srv")
        w(f"{self.root}/f/a.txt", "AAA", T)
        w(f"{self.root}/f/sub/b.txt", "BB", T)
        auth = DummyAuthorizer()
        auth.add_user("bob", "pw", self.root, perm="elradfmwMT")
        handler = type("H", (FTPHandler,), {"authorizer": auth})
        handler.use_sendfile = False
        self.srv = FTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.srv.serve_forever, kwargs={"timeout": 0.1, "blocking": True}, daemon=True).start()
        self.port = self.srv.address[1]

    def tearDown(self):
        self.srv.close_all()
        shutil.rmtree(self.d, ignore_errors=True)

    def test_flow(self):
        fs, root = remote.open_url(f"ftp://bob:pw@127.0.0.1:{self.port}/f")
        self.assertEqual(sorted((e.name, e.is_dir, e.size) for e in fs.scandir(root)), [("a.txt", False, 3), ("sub", True, 0)])
        self.assertAlmostEqual(fs.stat("/f/a.txt").mtime, T, delta=2)
        with fs.open("/f/sub/b.txt") as f:
            self.assertEqual(f.read(), b"BB")
        src = os.path.join(self.d, "u.txt")
        w(src, "UP", T + 60)
        fs.put_file(src, "/f/x/y/u.txt", T + 60)
        self.assertTrue(os.path.exists(f"{self.root}/f/x/y/u.txt"))
        self.assertAlmostEqual(os.path.getmtime(f"{self.root}/f/x/y/u.txt"), T + 60, delta=2)       # MFMT
        fs.rename("/f/x/y/u.txt", "/f/x/z/u2.txt")
        self.assertTrue(os.path.exists(f"{self.root}/f/x/z/u2.txt"))
        fs.remove("/f/x")
        self.assertFalse(os.path.exists(f"{self.root}/f/x"))
        fs.set_mtime("/f/a.txt", T + 1000)
        self.assertAlmostEqual(os.path.getmtime(f"{self.root}/f/a.txt"), T + 1000, delta=2)

    def test_compare_dir(self):
        local = os.path.join(self.d, "L")
        w(f"{local}/a.txt", "AAA", T)
        w(f"{local}/sub/b.txt", "bb", T)
        fs, root = remote.open_url(f"ftp://bob:pw@127.0.0.1:{self.port}/f")
        nodes = engine.compare_tree(vfs.LocalFS(), local, fs, root, engine.CompareOptions(time_tol=2, method="crc"))
        st = {n.rel: n.status for n in engine.walk(nodes) if not n.is_dir}
        self.assertEqual(st["a.txt"], "same")
        self.assertNotEqual(st["sub/b.txt"], "same")

    def test_bad_login(self):
        with self.assertRaises(remote.RemoteError):
            remote.open_url(f"ftp://bob:nope@127.0.0.1:{self.port}/f")


if __name__ == "__main__":
    unittest.main()
