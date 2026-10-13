"""Dialogs for remote locations: connect to a server, ask for missing secrets, confirm SSH host keys."""
import urllib.parse

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QApplication, QComboBox, QDialog, QDialogButtonBox, QFileDialog, QFormLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QMessageBox, QPushButton, QSpinBox, QStackedWidget,
                               QVBoxLayout, QWidget)

from . import remote, theme, vfs

TYPES = [
    ("ftp", "FTP", "Plain FTP (no encryption)"),
    ("ftps", "FTPS", "FTP over TLS (explicit)"),
    ("sftp", "SFTP", "SSH file transfer - needs the 'paramiko' package"),
    ("dav", "WebDAV", "Nextcloud, ownCloud, IIS, Apache mod_dav ... (use https:// for encryption)"),
    ("s3", "Amazon S3", "S3 and compatible services (MinIO, Wasabi, Backblaze B2 ...)"),
    ("dropbox", "Dropbox", "Needs an OAuth access token"),
    ("onedrive", "OneDrive", "Needs a Microsoft Graph access token"),
    ("svn", "Subversion", "Read-only; needs the 'svn' command line client"),
]


def _edit(placeholder="", password=False):
    e = QLineEdit()
    e.setPlaceholderText(placeholder)
    if password:
        e.setEchoMode(QLineEdit.Password)
    return e


class ConnectDialog(QDialog):
    """Builds a location URL. Secrets are kept in memory only (never put into the URL / sessions)."""

    def __init__(self, parent=None, side="left"):
        super().__init__(parent)
        self.setWindowTitle(f"Connect to a server ({side} side)")
        self.setMinimumWidth(560)
        self.url = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 20, 24, 16)
        lay.setSpacing(10)
        t = QLabel("Connect to a server")
        t.setStyleSheet("font-size:15pt; font-weight:700;")
        lay.addWidget(t)
        self.kind = QComboBox()
        for key, label, _ in TYPES:
            self.kind.addItem(label, key)
        lay.addWidget(self.kind)
        self.hint = QLabel()
        self.hint.setWordWrap(True)
        self.hint.setStyleSheet(f"color:{theme.MUTED};")
        lay.addWidget(self.hint)
        self.stack = QStackedWidget()
        self.fields = {}
        for key, label, _ in TYPES:
            w = QWidget()
            f = QFormLayout(w)
            f.setContentsMargins(0, 6, 0, 0)
            self.fields[key] = self._build(key, f)
            self.stack.addWidget(w)
        lay.addWidget(self.stack)
        self.result_lbl = QLabel()
        self.result_lbl.setWordWrap(True)
        lay.addWidget(self.result_lbl)
        row = QHBoxLayout()
        self.b_test = QPushButton("Test connection")
        self.b_test.clicked.connect(self.test)
        row.addWidget(self.b_test)
        row.addStretch(1)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Ok).setText("Use this location")
        bb.button(QDialogButtonBox.Ok).setProperty("primary", True)
        bb.accepted.connect(self._ok)
        bb.rejected.connect(self.reject)
        row.addWidget(bb)
        lay.addLayout(row)
        self.kind.currentIndexChanged.connect(self._kind_changed)
        self._kind_changed()

    def _kind_changed(self):
        i = self.kind.currentIndex()
        self.stack.setCurrentIndex(i)
        self.hint.setText(TYPES[i][2])
        self.result_lbl.setText("")

    def _build(self, key, f):
        d = {}
        if key in ("ftp", "ftps", "sftp"):
            d["host"] = _edit("ftp.example.com")
            d["port"] = QSpinBox()
            d["port"].setRange(1, 65535)
            d["port"].setValue(22 if key == "sftp" else 21)
            d["user"] = _edit("user name" + (" (blank = anonymous)" if key != "sftp" else ""))
            d["password"] = _edit("password", True)
            d["path"] = _edit("/folder")
            f.addRow("Host", d["host"])
            f.addRow("Port", d["port"])
            f.addRow("User", d["user"])
            f.addRow("Password", d["password"])
            if key == "sftp":
                d["keyfile"] = _edit("optional private key file")
                row = QHBoxLayout()
                row.addWidget(d["keyfile"], 1)
                b = QPushButton("Browse")
                b.clicked.connect(lambda: self._pick_key(d["keyfile"]))
                row.addWidget(b)
                f.addRow("Key file", row)
            f.addRow("Folder", d["path"])
        elif key == "dav":
            d["base"] = _edit("https://cloud.example.com/remote.php/dav/files/me")
            d["user"] = _edit("user name")
            d["password"] = _edit("password or app password", True)
            d["path"] = _edit("/ (optional sub folder)")
            f.addRow("Address", d["base"])
            f.addRow("User", d["user"])
            f.addRow("Password", d["password"])
            f.addRow("Folder", d["path"])
        elif key == "s3":
            d["bucket"] = _edit("my-bucket")
            d["prefix"] = _edit("optional/folder")
            d["region"] = _edit("us-east-1")
            d["endpoint"] = _edit("leave empty for Amazon, e.g. https://minio.example:9000")
            d["access"] = _edit("access key id")
            d["secret"] = _edit("secret access key", True)
            for lab, k in (("Bucket", "bucket"), ("Folder", "prefix"), ("Region", "region"), ("Endpoint", "endpoint"),
                           ("Access key", "access"), ("Secret", "secret")):
                f.addRow(lab, d[k])
        elif key in ("dropbox", "onedrive"):
            d["token"] = _edit("OAuth access token", True)
            d["path"] = _edit("/folder")
            f.addRow("Access token", d["token"])
            f.addRow("Folder", d["path"])
        else:
            d["base"] = _edit("https://svn.example.com/repo/trunk   or   svn://host/repo")
            d["user"] = _edit("user name (optional)")
            d["password"] = _edit("password (optional)", True)
            d["rev"] = _edit("revision (optional, default HEAD)")
            f.addRow("Repository URL", d["base"])
            f.addRow("User", d["user"])
            f.addRow("Password", d["password"])
            f.addRow("Revision", d["rev"])
        return d

    def _pick_key(self, edit):
        p, _ = QFileDialog.getOpenFileName(self, "Private key file")
        if p:
            edit.setText(p)

    def build(self):
        """Returns (url, secret store actions). Raises ValueError for incomplete input."""
        key = self.kind.currentData()
        f = self.fields[key]
        txt = lambda k: f[k].text().strip() if k in f else ""
        if key in ("ftp", "ftps", "sftp"):
            host = txt("host")
            if not host:
                raise ValueError("Enter the host name")
            user = txt("user")
            port = f["port"].value()
            path = "/" + txt("path").strip("/")
            netloc = (urllib.parse.quote(user, safe="") + "@" if user else "") + host + (f":{port}" if port != (22 if key == "sftp" else 21) else "")
            url = f"{key}://{netloc}{urllib.parse.quote(path, safe='/')}"
            if key == "sftp" and txt("keyfile"):
                url += "?key=" + urllib.parse.quote(txt("keyfile"), safe="")
            if f["password"].text():
                remote.set_credential(remote.cred_key(key, user, host, port if port != (22 if key == "sftp" else 21) else None),
                                      f["password"].text(), {"key_file": txt("keyfile")})
            return url
        if key == "dav":
            base = txt("base")
            u = urllib.parse.urlparse(base if "://" in base else "https://" + base)
            if not u.netloc:
                raise ValueError("Enter the WebDAV address")
            user = txt("user")
            scheme = "davs" if u.scheme == "https" else "dav"
            netloc = (urllib.parse.quote(user, safe="") + "@" if user else "") + u.netloc
            path = (u.path.rstrip("/") + "/" + txt("path").strip("/")).rstrip("/") or "/"
            if f["password"].text():
                remote.set_credential(remote.cred_key("dav", user, u.hostname, u.port), f["password"].text())
            return f"{scheme}://{netloc}{urllib.parse.quote(path, safe='/')}"
        if key == "s3":
            bucket = txt("bucket")
            if not bucket:
                raise ValueError("Enter the bucket name")
            q = {}
            for k in ("region", "endpoint"):
                if txt(k):
                    q[k] = txt(k)
            if txt("access") and f["secret"].text():
                remote.set_credential(f"s3://{bucket}", f"{txt('access')}:{f['secret'].text()}")
            return f"s3://{bucket}/{txt('prefix').strip('/')}" + ("?" + urllib.parse.urlencode(q) if q else "")
        if key in ("dropbox", "onedrive"):
            if not f["token"].text().strip():
                raise ValueError("Paste an access token")
            remote.set_credential(key, f["token"].text().strip())
            return f"{key}:///{txt('path').strip('/')}"
        base = txt("base")
        u = urllib.parse.urlparse(base)
        if not u.scheme:
            raise ValueError("Enter the repository URL")
        scheme = u.scheme if u.scheme == "svn" else "svn+" + u.scheme
        user = txt("user")
        netloc = (urllib.parse.quote(user, safe="") + "@" if user else "") + u.netloc
        if f["password"].text():
            remote.set_credential(remote.cred_key("svn", user, u.hostname, u.port), f["password"].text())
        url = f"{scheme}://{netloc}{u.path}"
        if txt("rev"):
            url += "?rev=" + txt("rev")
        return url

    def test(self):
        try:
            url = self.build()
        except ValueError as e:
            self.result_lbl.setText(f"<span style='color:{theme.RED}'>{e}</span>")
            return
        QApplication.setOverrideCursor(Qt.WaitCursor)
        try:
            for _ in range(3):
                try:
                    fs, root = remote.open_url(url)
                    n = len(fs.scandir(root))
                    fs.close()
                    self.result_lbl.setText(f"<span style='color:{theme.GREEN}'>Connected – {n} item(s) in the folder.</span>")
                    break
                except (remote.NeedCredentials, remote.UnknownHost) as e:
                    QApplication.restoreOverrideCursor()
                    ok = prompt_for(self, e)
                    QApplication.setOverrideCursor(Qt.WaitCursor)
                    if not ok:
                        self.result_lbl.setText("Cancelled")
                        break
        except Exception as e:
            self.result_lbl.setText(f"<span style='color:{theme.RED}'>{e}</span>")
        finally:
            QApplication.restoreOverrideCursor()

    def _ok(self):
        try:
            self.url = self.build()
        except ValueError as e:
            QMessageBox.warning(self, "Connect", str(e))
            return
        self.accept()


def prompt_for(parent, exc):
    """Ask the user for what an open_url() failure needs. Returns True when the caller should retry."""
    if isinstance(exc, remote.UnknownHost):
        r = QMessageBox.question(
            parent, "Unknown SSH host",
            f"The authenticity of {exc.host} can't be verified.\n\n{exc.key_type} fingerprint:\n{exc.fingerprint}\n\n"
            "Trust this host and continue?")
        if r == QMessageBox.Yes:
            remote.accept_host_key(exc.host, exc.port, exc.key)
            return True
        return False
    if isinstance(exc, remote.NeedCredentials):
        if exc.kind == "s3":
            dlg = QDialog(parent)
            dlg.setWindowTitle("Amazon S3 credentials")
            f = QFormLayout(dlg)
            a, s = _edit("access key id"), _edit("secret access key", True)
            f.addRow(QLabel(str(exc)))
            f.addRow("Access key", a)
            f.addRow("Secret", s)
            bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
            bb.accepted.connect(dlg.accept)
            bb.rejected.connect(dlg.reject)
            f.addRow(bb)
            if dlg.exec() and a.text() and s.text():
                remote.set_credential(exc.key, f"{a.text().strip()}:{s.text()}")
                return True
            return False
        label = "Access token" if exc.kind == "token" else "Password"
        text, ok = QInputDialog.getText(parent, "Credentials needed", f"{exc}\n\n{label} for {exc.key}:", QLineEdit.Password)
        if ok and text:
            remote.set_credential(exc.key, text)
            return True
    return False


def open_with_prompts(parent, spec):
    """Open a location, asking for secrets / host-key confirmation as needed. Returns (fs, root) or None."""
    for _ in range(4):
        try:
            return vfs.open_location(spec)
        except (remote.NeedCredentials, remote.UnknownHost) as e:
            if not prompt_for(parent, e):
                return None
    return None
