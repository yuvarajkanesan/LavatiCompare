import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
for extra in filter(None, os.environ.get("LAVATI_TESTDEPS", "").split(os.pathsep)):
    sys.path.append(extra)
from lavati import engine, fsops, remote, vfs  # noqa: E402

try:
    import paramiko
    HAVE = True
except Exception:                       # pragma: no cover
    HAVE = False

T = 1_700_000_000


def w(path, data, mtime=T):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(data)
    os.utime(path, (mtime, mtime))


if HAVE:
    class Server(paramiko.ServerInterface):
        def check_auth_password(self, username, password):
            return paramiko.AUTH_SUCCESSFUL if (username, password) == ("bob", "pw") else paramiko.AUTH_FAILED

        def get_allowed_auths(self, username):
            return "password"

        def check_channel_request(self, kind, chanid):
            return paramiko.OPEN_SUCCEEDED

    class Handle(paramiko.SFTPHandle):
        def stat(self):
            try:
                return paramiko.SFTPAttributes.from_stat(os.fstat(self.readfile.fileno()))
            except OSError as e:
                return paramiko.SFTPServer.convert_errno(e.errno)

        def chattr(self, attr):
            try:
                paramiko.SFTPServer.set_file_attr(self.filename, attr)
                return paramiko.SFTP_OK
            except OSError as e:
                return paramiko.SFTPServer.convert_errno(e.errno)

    class SFTPImpl(paramiko.SFTPServerInterface):
        ROOT = ""

        def _real(self, path):
            return self.ROOT + self.canonicalize(path)

        def list_folder(self, path):
            p = self._real(path)
            try:
                out = []
                for fn in os.listdir(p):
                    a = paramiko.SFTPAttributes.from_stat(os.stat(os.path.join(p, fn)))
                    a.filename = fn
                    out.append(a)
                return out
            except OSError as e:
                return paramiko.SFTPServer.convert_errno(e.errno)

        def stat(self, path):
            try:
                return paramiko.SFTPAttributes.from_stat(os.stat(self._real(path)))
            except OSError as e:
                return paramiko.SFTPServer.convert_errno(e.errno)

        lstat = stat

        def open(self, path, flags, attr):
            p = self._real(path)
            try:
                binary_flag = getattr(os, "O_BINARY", 0)
                flags |= binary_flag
                mode = getattr(attr, "st_mode", None)
                fd = os.open(p, flags, mode if mode is not None else 0o666)
            except OSError as e:
                return paramiko.SFTPServer.convert_errno(e.errno)
            if flags & os.O_WRONLY:
                fstr = "ab" if flags & os.O_APPEND else "wb"
            elif flags & os.O_RDWR:
                fstr = "a+b" if flags & os.O_APPEND else "r+b"
            else:
                fstr = "rb"
            f = os.fdopen(fd, fstr)
            h = Handle(flags)
            h.filename = p
            h.readfile = f
            h.writefile = f
            return h

        def remove(self, path):
            try:
                os.remove(self._real(path))
            except OSError as e:
                return paramiko.SFTPServer.convert_errno(e.errno)
            return paramiko.SFTP_OK

        def rename(self, old, new):
            try:
                os.replace(self._real(old), self._real(new))
            except OSError as e:
                return paramiko.SFTPServer.convert_errno(e.errno)
            return paramiko.SFTP_OK

        def mkdir(self, path, attr):
            try:
                os.mkdir(self._real(path))
            except OSError as e:
                return paramiko.SFTPServer.convert_errno(e.errno)
            return paramiko.SFTP_OK

        def rmdir(self, path):
            try:
                os.rmdir(self._real(path))
            except OSError as e:
                return paramiko.SFTPServer.convert_errno(e.errno)
            return paramiko.SFTP_OK

        def chattr(self, path, attr):
            try:
                paramiko.SFTPServer.set_file_attr(self._real(path), attr)
            except OSError as e:
                return paramiko.SFTPServer.convert_errno(e.errno)
            return paramiko.SFTP_OK


@unittest.skipUnless(HAVE, "paramiko not installed")
class SFTPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = paramiko.RSAKey.generate(2048)

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.root = os.path.join(self.d, "srv")
        w(f"{self.root}/f/a.txt", "AAA")
        w(f"{self.root}/f/sub/b.txt", "BB")
        SFTPImpl.ROOT = self.root.replace("\\", "/")
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.port = self.sock.getsockname()[1]
        self.alive = True
        threading.Thread(target=self._accept, daemon=True).start()
        self._kh = remote.known_hosts_path
        remote.known_hosts_path = lambda: os.path.join(self.d, "known_hosts")

    def _accept(self):
        while self.alive:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            t = paramiko.Transport(conn)
            t.add_server_key(self.key)
            t.set_subsystem_handler("sftp", paramiko.SFTPServer, SFTPImpl)
            try:
                t.start_server(server=Server())
            except Exception:
                pass

    def tearDown(self):
        self.alive = False
        self.sock.close()
        remote.known_hosts_path = self._kh
        shutil.rmtree(self.d, ignore_errors=True)

    def connect(self):
        return remote.open_url(f"sftp://bob:pw@127.0.0.1:{self.port}/f")

    def test_host_key_confirmation_and_flow(self):
        with self.assertRaises(remote.UnknownHost) as cm:
            self.connect()
        e = cm.exception
        self.assertTrue(e.fingerprint.startswith("SHA256:"))
        remote.accept_host_key(e.host, e.port, e.key)
        fs, root = self.connect()
        self.assertEqual(sorted((x.name, x.is_dir, x.size) for x in fs.scandir(root)), [("a.txt", False, 3), ("sub", True, 0)])
        self.assertEqual(fs.stat("/f/a.txt").mtime, T)
        with fs.open("/f/sub/b.txt") as f:
            self.assertEqual(f.read(), b"BB")
        src = os.path.join(self.d, "u.txt")
        w(src, "UP", T + 60)
        fs.put_file(src, "/f/x/y/u.txt", T + 60)
        p = f"{self.root}/f/x/y/u.txt"
        self.assertEqual(open(p).read(), "UP")
        self.assertEqual(int(os.path.getmtime(p)), T + 60)
        fs.rename("/f/x/y/u.txt", "/f/x/z/u2.txt")
        self.assertTrue(os.path.exists(f"{self.root}/f/x/z/u2.txt"))
        fs.remove("/f/x")
        self.assertFalse(os.path.exists(f"{self.root}/f/x"))
        fs.set_mtime("/f/a.txt", T + 5)
        self.assertEqual(int(os.path.getmtime(f"{self.root}/f/a.txt")), T + 5)

    def test_wrong_password(self):
        with self.assertRaises(remote.UnknownHost) as cm:
            self.connect()
        remote.accept_host_key(cm.exception.host, cm.exception.port, cm.exception.key)
        with self.assertRaises(remote.RemoteError):
            remote.open_url(f"sftp://bob:bad@127.0.0.1:{self.port}/f")


if __name__ == "__main__":
    unittest.main()
