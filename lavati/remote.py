"""Remote file systems: FTP / FTPS, SFTP, WebDAV, Amazon S3, Dropbox, OneDrive and Subversion.

URL forms understood by open_url():
    ftp://user:pass@host:21/dir          ftps://host/dir          (explicit TLS)
    sftp://user@host:22/dir              (needs the optional 'paramiko' package)
    dav://host/path   davs://host/path   (WebDAV over http / https)
    s3://bucket/prefix?region=eu-west-1&endpoint=https://minio.example:9000
    dropbox:///folder     onedrive:///folder        (OAuth access token supplied by the user)
    svn://host/repo/trunk   svn+https://host/repo?rev=1234     (needs the 'svn' command line client)

Secrets never need to appear in URLs: set_credential() keeps them in memory for the session, and the
standard environment variables (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, DROPBOX_TOKEN, ONEDRIVE_TOKEN) are used
as fallbacks. No Qt imports here.
"""
import base64
import calendar
import datetime
import email.utils
import ftplib
import hashlib
import hmac
import json
import os
import posixpath
import re
import shutil
import socket
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from . import vfs
from .vfs import Entry

TIMEOUT = 30


class RemoteError(vfs.VFSError):
    pass


class NeedCredentials(RemoteError):
    """Raised when a secret is required; the UI asks the user and calls set_credential() then retries."""

    def __init__(self, key, kind, message):
        super().__init__(message)
        self.key, self.kind = key, kind


class UnknownHost(RemoteError):
    def __init__(self, host, port, key_type, fingerprint, key=None):
        super().__init__(f"Unknown SSH host key for {host}")
        self.host, self.port, self.key_type, self.fingerprint, self.key = host, port, key_type, fingerprint, key


# ------------------------------------------------------------- credentials
_creds = {}


def cred_key(scheme, user, host, port=None):
    return f"{scheme}://{user or ''}@{host}{':' + str(port) if port else ''}"


def set_credential(key, secret, extra=None):
    _creds[key] = (secret, extra or {})


def get_credential(key):
    return _creds.get(key, (None, {}))[0]


def get_credential_extra(key):
    return _creds.get(key, (None, {}))[1]


# ------------------------------------------------------------------ helpers
def _norm(path):
    path = "/" + (path or "").strip("/")
    return path


def _utc_ts(dt):
    if dt.tzinfo is None:
        return calendar.timegm(dt.timetuple())
    return dt.timestamp()


def parse_iso(s):
    """ISO-8601 (Z or offset, optional fraction) -> epoch seconds."""
    s = s.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = re.sub(r"(\.\d{6})\d+", r"\1", s)
    try:
        return datetime.datetime.fromisoformat(s).timestamp()
    except ValueError:
        return 0.0


def http_request(method, url, headers=None, data=None, timeout=60, follow=True, retries=2):
    """Returns (status, headers, body). Never raises for HTTP error codes."""
    hdrs = dict(headers or {})
    last = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, headers=hdrs, method=method)
        opener = urllib.request.build_opener(_SafeRedirect) if follow else urllib.request.build_opener(_NoRedirect)
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            if e.code >= 500 and attempt < retries:
                time.sleep(0.5 * (3 ** attempt))
                continue
            try:
                body = e.read()
            except Exception:
                body = b""
            finally:
                e.close()
            return e.code, dict(e.headers or {}), body
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
            last = e
            if attempt < retries:
                time.sleep(0.5 * (3 ** attempt))
                continue
    raise RemoteError(f"Network error: {last}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        return None


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    """Follow redirects, but never carry credentials to another host or down to plain http."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is None:
            return None
        old_u, new_u = urllib.parse.urlparse(req.full_url), urllib.parse.urlparse(newurl)
        if old_u.scheme == "https" and new_u.scheme != "https":
            return None                                          # no https -> http downgrade
        if (old_u.hostname or "").lower() != (new_u.hostname or "").lower() or old_u.scheme != new_u.scheme:
            for k in list(new.headers):
                if k.lower() in ("authorization", "cookie", "proxy-authorization"):
                    del new.headers[k]
            for k in list(new.unredirected_hdrs):
                if k.lower() in ("authorization", "cookie", "proxy-authorization"):
                    del new.unredirected_hdrs[k]
        return new


def _is_loopback(host):
    return (host or "").lower() in ("localhost", "127.0.0.1", "::1")


def http_download(url, headers, local, method="GET", data=None, timeout=120, strip_auth_on_redirect=True):
    """Stream a response body to `local`. Returns (status, headers)."""
    req = urllib.request.Request(url, data=data, headers=dict(headers or {}), method=method)
    opener = urllib.request.build_opener(_NoRedirect) if strip_auth_on_redirect else urllib.request.build_opener(_SafeRedirect)
    try:
        r = opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        code, hdrs = e.code, dict(e.headers or {})
        e.close()
        if code in (301, 302, 303, 307, 308) and strip_auth_on_redirect and hdrs.get("Location"):
            # follow redirects ourselves WITHOUT the Authorization header (pre-signed storage URLs)
            h2 = {k: v for k, v in (headers or {}).items() if k.lower() != "authorization"}
            return http_download(urllib.parse.urljoin(url, hdrs["Location"]), h2, local, "GET", None, timeout, False)
        return code, hdrs
    except (urllib.error.URLError, socket.timeout, ConnectionError, OSError) as e:
        raise RemoteError(f"Network error: {e}") from e
    with r, open(local, "wb") as out:
        shutil.copyfileobj(r, out, vfs.CHUNK)
        return r.status, dict(r.headers)


def refuse_cleartext_login(url, what="password"):
    """A password must not cross the network unencrypted (loopback is fine: nothing leaves the machine)."""
    u = urllib.parse.urlparse(url)
    if u.scheme == "http" and not _is_loopback(u.hostname):
        raise RemoteError(f"Refusing to send a {what} over plain http. Use an https:// address.")


# =============================================================== base class
class RemoteFS(vfs.VFS):
    slow = True
    cheap_algo = None          # checksum algorithm the server can supply without downloading

    def __init__(self, label, root="/"):
        super().__init__(root)
        self.label = label
        self._cache = {}
        self._dl = {}
        self._lock = threading.RLock()

    # --- naming (always absolute posix paths)
    def join(self, base, rel):
        if not rel:
            return base
        return posixpath.join(base or "/", rel.lstrip("/"))

    def parent(self, path):
        return posixpath.dirname(path.rstrip("/")) or "/"

    def basename(self, path):
        return posixpath.basename(path.rstrip("/"))

    def display(self, path):
        return self.label + _norm(path)

    # --- listing with a cache
    def _list(self, path):
        raise NotImplementedError

    def scandir(self, path):
        path = _norm(path)
        with self._lock:
            hit = self._cache.get(path)
        if hit is None:
            hit = self._list(path)
            with self._lock:
                self._cache[path] = hit
        return list(hit)

    def stat(self, path):
        path = _norm(path)
        if path == "/":
            return Entry("/", True, 0, 0.0)
        for e in self.scandir(self.parent(path)):
            if e.name == self.basename(path):
                return e
        return None

    def invalidate(self):
        with self._lock:
            self._cache.clear()
            self._dl.clear()

    # --- content
    def _download(self, path, local):
        raise NotImplementedError

    def local_path(self, path):
        path = _norm(path)
        st = self.stat(path)
        key = (path, st.size if st else None, st.mtime if st else None)
        hit = self._dl.get(key)
        if hit and os.path.exists(hit):
            return hit
        dst = vfs.new_temp_path(self.basename(path))
        try:
            self._download(path, dst)
        except OSError as e:
            raise RemoteError(f"{path}: {e}") from e
        if st and st.mtime:
            try:
                os.utime(dst, (st.mtime, st.mtime))
            except OSError:
                pass
        self._dl[key] = dst
        return dst

    def open(self, path):
        return open(self.local_path(path), "rb")

    def set_mtime(self, path, mtime):
        raise RemoteError("This kind of location can't change timestamps")

    def makedirs(self, path):
        raise NotImplementedError

    def _after_write(self):
        self.invalidate()


# ===================================================================== FTP
_UNIX_LS = re.compile(r"^([\-dlbcps])[rwxsStT\-]{9}[+@.]?\s+\d+\s+\S+\s+\S+\s+(\d+)\s+(\w{3})\s+(\d{1,2})\s+(\d{4}|\d{1,2}:\d{2})\s+(.+)$")
_UNIX_LS2 = re.compile(r"^([\-dlbcps])[rwxsStT\-]{9}[+@.]?\s+\d+\s+\S+\s+(\d+)\s+(\w{3})\s+(\d{1,2})\s+(\d{4}|\d{1,2}:\d{2})\s+(.+)$")
_DOS_LS = re.compile(r"^(\d{2}-\d{2}-\d{2,4})\s+(\d{1,2}:\d{2}\s*[AP]M)\s+(<DIR>|\d+)\s+(.+)$", re.I)
_MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def parse_ftp_list(line, now=None):
    """Parse one LIST line -> Entry or None."""
    now = now or time.time()
    m = _UNIX_LS.match(line) or _UNIX_LS2.match(line)
    if m:
        kind, size, mon, day, yt, name = m.groups()
        if " -> " in name:
            name = name.split(" -> ")[0]
        if name in (".", ".."):
            return None
        year = time.gmtime(now).tm_year
        if ":" in yt:
            hh, mm = map(int, yt.split(":"))
            ts = calendar.timegm((year, _MONTHS.get(mon.title(), 1), int(day), hh, mm, 0))
            if ts > now + 86400:
                ts = calendar.timegm((year - 1, _MONTHS.get(mon.title(), 1), int(day), hh, mm, 0))
        else:
            ts = calendar.timegm((int(yt), _MONTHS.get(mon.title(), 1), int(day), 0, 0, 0))
        return Entry(name, kind == "d", 0 if kind == "d" else int(size), float(ts))
    m = _DOS_LS.match(line)
    if m:
        d, t, size, name = m.groups()
        fmt = "%m-%d-%y" if len(d.split("-")[2]) == 2 else "%m-%d-%Y"
        try:
            ts = calendar.timegm(time.strptime(d + " " + t.replace(" ", ""), fmt + " %I:%M%p"))
        except ValueError:
            ts = 0
        return Entry(name, size.upper() == "<DIR>", 0 if size.upper() == "<DIR>" else int(size), float(ts))
    return None


def parse_mlsx_time(s):
    s = s.strip()
    frac = 0.0
    if "." in s:
        s, f = s.split(".", 1)
        frac = float("0." + f)
    try:
        return calendar.timegm(time.strptime(s[:14], "%Y%m%d%H%M%S")) + frac
    except ValueError:
        return 0.0


class FTPFS(RemoteFS):
    scheme = "ftp"

    def __init__(self, host, port, user, password, tls=False, root="/"):
        super().__init__(f"{'ftps' if tls else 'ftp'}://{host}" + (f":{port}" if port not in (21, None) else ""), root)
        self.host, self.port, self.user, self.password, self.tls = host, port or 21, user or "anonymous", password or "anonymous@", tls
        self._ftp = None
        self._mlsd = None
        self._connect()

    def _connect(self):
        try:
            ftp = ftplib.FTP_TLS() if self.tls else ftplib.FTP()
            ftp.encoding = "utf-8"
            ftp.connect(self.host, self.port, timeout=TIMEOUT)
            ftp.login(self.user, self.password)
            if self.tls:
                ftp.prot_p()
            self._ftp = ftp
        except ftplib.error_perm as e:
            raise RemoteError(f"FTP login failed: {e}") from e
        except (OSError, EOFError, ftplib.Error) as e:
            raise RemoteError(f"FTP connection failed: {e}") from e

    def _do(self, fn):
        with self._lock:
            for attempt in (0, 1):
                try:
                    return fn(self._ftp)
                except (EOFError, ConnectionError, socket.timeout, ftplib.error_temp, BrokenPipeError):
                    if attempt:
                        raise
                    try:
                        self._ftp.close()
                    except Exception:
                        pass
                    self._connect()

    def _list(self, path):
        def go(ftp):
            out = []
            if self._mlsd is not False:
                try:
                    for name, facts in ftp.mlsd(path, facts=["type", "size", "modify"]):
                        t = facts.get("type", "").lower()
                        if t in ("cdir", "pdir") or name in (".", ".."):
                            continue
                        isdir = t == "dir"
                        out.append(Entry(name, isdir, 0 if isdir else int(facts.get("size", 0) or 0),
                                         parse_mlsx_time(facts.get("modify", ""))))
                    self._mlsd = True
                    return out
                except ftplib.error_perm as e:
                    if str(e)[:3] not in ("500", "501", "502", "504", "421"):
                        raise                      # e.g. 550: the folder itself is the problem
                    self._mlsd = False
            lines = []
            ftp.retrlines("LIST " + path, lines.append)
            for ln in lines:
                e = parse_ftp_list(ln)
                if e:
                    out.append(e)
            return out
        try:
            return self._do(go)
        except ftplib.error_perm as e:
            raise RemoteError(f"{path}: {e}") from e

    def _download(self, path, local):
        def go(ftp):
            with open(local, "wb") as f:
                ftp.retrbinary("RETR " + path, f.write, blocksize=64 * 1024)
        self._do(go)

    def makedirs(self, path):
        path = _norm(path)

        def go(ftp):
            cur = ""
            for part in [p for p in path.split("/") if p]:
                cur += "/" + part
                try:
                    ftp.mkd(cur)
                except ftplib.error_perm:
                    pass            # exists
        self._do(go)
        self._after_write()

    def put_file(self, local, path, mtime=None):
        path = _norm(path)
        self.makedirs(self.parent(path))

        def go(ftp):
            with open(local, "rb") as f:
                ftp.storbinary("STOR " + path, f, blocksize=64 * 1024)
        self._do(go)
        if mtime:
            try:
                self.set_mtime(path, mtime)
            except RemoteError:
                pass
        self._after_write()

    def set_mtime(self, path, mtime):
        stamp = time.strftime("%Y%m%d%H%M%S", time.gmtime(mtime))
        try:
            self._do(lambda ftp: ftp.voidcmd(f"MFMT {stamp} {_norm(path)}"))
        except ftplib.error_perm as e:
            raise RemoteError("This FTP server does not support changing timestamps (MFMT)") from e
        self._after_write()

    def remove(self, path, trash=False):
        path = _norm(path)
        st = self.stat(path)
        if st is None:
            return

        def rec(ftp, p, is_dir):
            if is_dir:
                for e in self._list(p):
                    rec(ftp, posixpath.join(p, e.name), e.is_dir)
                ftp.rmd(p)
            else:
                ftp.delete(p)
        try:
            self._do(lambda ftp: rec(ftp, path, st.is_dir))
        except ftplib.error_perm as e:
            raise RemoteError(f"{path}: {e}") from e
        self._after_write()

    def rename(self, path, new_path):
        self.makedirs(self.parent(new_path))
        try:
            self._do(lambda ftp: ftp.rename(_norm(path), _norm(new_path)))
        except ftplib.error_perm as e:
            raise RemoteError(str(e)) from e
        self._after_write()

    def close(self):
        try:
            self._ftp.quit()
        except Exception:
            pass


# ==================================================================== SFTP
def known_hosts_path():
    from . import config
    return os.path.join(config.APP_DIR, "known_hosts")


def accept_host_key(host, port, key):
    import paramiko
    hk = paramiko.HostKeys()
    p = known_hosts_path()
    if os.path.exists(p):
        hk.load(p)
    name = host if port in (22, None) else f"[{host}]:{port}"
    hk.add(name, key.get_name(), key)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    hk.save(p)


class SFTPFS(RemoteFS):
    scheme = "sftp"

    def __init__(self, host, port, user, password, key_file=None, root="/"):
        super().__init__(f"sftp://{host}" + (f":{port}" if port not in (22, None) else ""), root)
        self.host, self.port, self.user, self.password, self.key_file = host, port or 22, user, password, key_file
        self._client = self._sftp = None
        self._connect()

    def _connect(self):
        try:
            import paramiko
        except ImportError as e:
            raise RemoteError("SFTP needs the optional 'paramiko' package:  pip install paramiko") from e
        host, port = self.host, self.port

        class Policy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                fp = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
                raise UnknownHost(host, port, key.get_name(), fp, key)
        client = paramiko.SSHClient()
        try:
            client.load_system_host_keys()
        except OSError:
            pass
        kh = known_hosts_path()
        if os.path.exists(kh):
            try:
                client.load_host_keys(kh)
            except OSError:
                pass
        client.set_missing_host_key_policy(Policy())
        try:
            client.connect(host, port, username=self.user or None, password=self.password or None,
                           key_filename=self.key_file or None, timeout=TIMEOUT, allow_agent=True, look_for_keys=True)
            self._sftp = client.open_sftp()
        except UnknownHost:
            raise
        except paramiko.AuthenticationException as e:
            raise NeedCredentials(cred_key("sftp", self.user, host, port if port != 22 else None), "password",
                                  "SSH authentication failed - password or key required") from e
        except (paramiko.SSHException, OSError, EOFError) as e:
            raise RemoteError(f"SFTP connection failed: {e}") from e
        self._client = client

    def _list(self, path):
        import stat as st
        try:
            return [Entry(a.filename, st.S_ISDIR(a.st_mode or 0), 0 if st.S_ISDIR(a.st_mode or 0) else (a.st_size or 0),
                          float(a.st_mtime or 0)) for a in self._sftp.listdir_attr(path)]
        except OSError as e:
            raise RemoteError(f"{path}: {e}") from e

    def _download(self, path, local):
        self._sftp.get(path, local)

    def makedirs(self, path):
        cur = ""
        for part in [p for p in _norm(path).split("/") if p]:
            cur += "/" + part
            try:
                self._sftp.stat(cur)
            except OSError:
                self._sftp.mkdir(cur)
        self._after_write()

    def put_file(self, local, path, mtime=None):
        path = _norm(path)
        self.makedirs(self.parent(path))
        self._sftp.put(local, path)
        if mtime:
            self._sftp.utime(path, (mtime, mtime))
        self._after_write()

    def set_mtime(self, path, mtime):
        self._sftp.utime(_norm(path), (mtime, mtime))
        self._after_write()

    def remove(self, path, trash=False):
        path = _norm(path)
        st = self.stat(path)
        if st is None:
            return

        def rec(p, is_dir):
            if is_dir:
                for e in self._list(p):
                    rec(posixpath.join(p, e.name), e.is_dir)
                self._sftp.rmdir(p)
            else:
                self._sftp.remove(p)
        try:
            rec(path, st.is_dir)
        except OSError as e:
            raise RemoteError(f"{path}: {e}") from e
        self._after_write()

    def rename(self, path, new_path):
        self.makedirs(self.parent(new_path))
        self._sftp.rename(_norm(path), _norm(new_path))
        self._after_write()

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass


# ================================================================== WebDAV
_DAV = "{DAV:}"


class WebDAVFS(RemoteFS):
    scheme = "dav"

    def __init__(self, base_url, user=None, password=None, label=None):
        u = urllib.parse.urlparse(base_url)
        self.base = f"{u.scheme}://{u.netloc}"
        self.base_path = urllib.parse.unquote(u.path).rstrip("/")
        super().__init__(label or base_url.rstrip("/"), "/")
        self.user, self.password = user, password
        self._auth = None
        if user is not None and password is not None:
            refuse_cleartext_login(base_url)
            self._auth = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
        self._digest_opener = None
        self._probe()

    def _url(self, path):
        return self.base + urllib.parse.quote(self.base_path + _norm(path).rstrip("/") + ("/" if _norm(path) == "/" else ""),
                                              safe="/~!$&'()*+,;=:@")

    def _hdrs(self, extra=None):
        h = {"User-Agent": "LavatiCompare"}
        if self._auth:
            h["Authorization"] = self._auth
        h.update(extra or {})
        return h

    def _req(self, method, path, headers=None, data=None, timeout=60):
        url = self._url(path)
        st, hd, body = http_request(method, url, self._hdrs(headers), data, timeout)
        if st == 401 and self.user is not None and self.password is not None and "digest" in hd.get("WWW-Authenticate", "").lower():
            st, hd, body = self._digest(method, url, headers, data, timeout)
        if st == 401:
            raise NeedCredentials(cred_key("dav", self.user, urllib.parse.urlparse(self.base).netloc), "password",
                                  "The server rejected the user name / password")
        return st, hd, body

    def _digest(self, method, url, headers, data, timeout):
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, self.base, self.user, self.password)
        opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(mgr))
        req = urllib.request.Request(url, data=data, headers={"User-Agent": "LavatiCompare", **(headers or {})}, method=method)
        try:
            with opener.open(req, timeout=timeout) as r:
                return r.status, dict(r.headers), r.read()
        except urllib.error.HTTPError as e:
            try:
                return e.code, dict(e.headers or {}), e.read()
            finally:
                e.close()
        except (urllib.error.URLError, OSError) as e:
            raise RemoteError(f"Network error: {e}") from e

    def _probe(self):
        st, _, _ = self._req("PROPFIND", "/", {"Depth": "0", "Content-Type": "application/xml"}, _PROPFIND)
        if st not in (200, 207):
            raise RemoteError(f"WebDAV server answered {st} - check the address and the user name / password")

    def _list(self, path):
        st, _, body = self._req("PROPFIND", path, {"Depth": "1", "Content-Type": "application/xml"}, _PROPFIND)
        if st == 404:
            return []
        if st not in (200, 207):
            raise RemoteError(f"{path}: WebDAV error {st}")
        try:
            root = ET.fromstring(body)
        except ET.ParseError as e:
            raise RemoteError(f"Invalid WebDAV response: {e}") from e
        me = (self.base_path + _norm(path)).rstrip("/")
        out = []
        for r in root.iter(_DAV + "response"):
            href = r.findtext(_DAV + "href") or ""
            hp = urllib.parse.unquote(urllib.parse.urlparse(href).path).rstrip("/")
            if hp == me or not hp.startswith(me + "/"):
                continue
            name = hp[len(me) + 1:]
            if "/" in name:
                continue
            prop = None
            for ps in r.iter(_DAV + "propstat"):
                if "200" in (ps.findtext(_DAV + "status") or "200"):
                    prop = ps.find(_DAV + "prop")
                    break
            if prop is None:
                continue
            isdir = prop.find(_DAV + "resourcetype/" + _DAV + "collection") is not None
            size = prop.findtext(_DAV + "getcontentlength")
            lm = prop.findtext(_DAV + "getlastmodified")
            try:
                mt = email.utils.parsedate_to_datetime(lm).timestamp() if lm else 0.0
            except (TypeError, ValueError):
                mt = 0.0
            out.append(Entry(name, isdir, 0 if isdir else int(size or 0), mt))
        return out

    def _download(self, path, local):
        st, _ = http_download(self._url(path), self._hdrs(), local, strip_auth_on_redirect=False)
        if st != 200:
            raise RemoteError(f"{path}: WebDAV GET returned {st}")

    def makedirs(self, path):
        cur = ""
        for part in [p for p in _norm(path).split("/") if p]:
            cur += "/" + part
            st, _, _ = self._req("MKCOL", cur)
            if st not in (201, 405, 301, 200):          # 405 = already exists
                raise RemoteError(f"{cur}: cannot create folder (WebDAV {st})")
        self._after_write()

    def put_file(self, local, path, mtime=None):
        path = _norm(path)
        self.makedirs(self.parent(path))
        extra = {"Content-Type": "application/octet-stream"}
        if mtime:
            extra["X-OC-Mtime"] = str(int(mtime))         # honoured by ownCloud / Nextcloud
        with open(local, "rb") as f:
            extra["Content-Length"] = str(os.path.getsize(local))
            st, _, _ = self._req("PUT", path, extra, f, timeout=3600)
        if st not in (200, 201, 204):
            raise RemoteError(f"{path}: upload failed (WebDAV {st})")
        self._after_write()

    def remove(self, path, trash=False):
        st, _, _ = self._req("DELETE", path)
        if st not in (200, 204, 404):
            raise RemoteError(f"{path}: delete failed (WebDAV {st})")
        self._after_write()

    def rename(self, path, new_path):
        self.makedirs(self.parent(new_path))
        dest = self._url(new_path)
        st, _, _ = self._req("MOVE", path, {"Destination": dest, "Overwrite": "F"})
        if st not in (200, 201, 204):
            raise RemoteError(f"{path}: rename failed (WebDAV {st})")
        self._after_write()


_PROPFIND = (b'<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/>'
             b'<d:getcontentlength/><d:getlastmodified/></d:prop></d:propfind>')


# ======================================================================= S3
def _hmac(key, msg):
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _q(s, safe="-_.~"):
    return urllib.parse.quote(s, safe=safe)


def sigv4_headers(method, host, path, query, region, service, access, secret, token=None, payload_hash=None,
                  now=None, extra_headers=None, content_sha_header=True):
    """AWS Signature Version 4. `path` must already be percent-encoded as sent on the wire; `query` is a dict."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date = amz_date[:8]
    payload_hash = payload_hash or hashlib.sha256(b"").hexdigest()
    headers = {"host": host, "x-amz-date": amz_date}
    if content_sha_header:
        headers["x-amz-content-sha256"] = payload_hash
    if token:
        headers["x-amz-security-token"] = token
    for k, v in (extra_headers or {}).items():
        headers[k.lower()] = str(v).strip()
    names = sorted(headers)
    canon_headers = "".join(f"{k}:{headers[k]}\n" for k in names)
    signed = ";".join(names)
    cq = "&".join(f"{_q(k)}={_q(str(v))}" for k, v in sorted(query.items()))
    creq = "\n".join([method, path or "/", cq, canon_headers, signed, payload_hash])
    scope = f"{date}/{region}/{service}/aws4_request"
    sts = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(creq.encode()).hexdigest()])
    k = _hmac(("AWS4" + secret).encode(), date)
    for part in (region, service, "aws4_request"):
        k = _hmac(k, part)
    sig = hmac.new(k, sts.encode(), hashlib.sha256).hexdigest()
    out = dict(headers)
    out["Authorization"] = f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={signed}, Signature={sig}"
    out.pop("host", None)
    return out


def _file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(vfs.CHUNK), b""):
            h.update(b)
    return h.hexdigest()


class S3FS(RemoteFS):
    scheme = "s3"
    cheap_algo = "md5"

    def __init__(self, bucket, prefix="", region=None, endpoint=None, access=None, secret=None, token=None, pathstyle=None):
        super().__init__(f"s3://{bucket}", "/" + prefix.strip("/") if prefix.strip("/") else "/")
        self.bucket = bucket
        self._etags = {}
        self.region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        self.access = access or os.environ.get("AWS_ACCESS_KEY_ID")
        self.secret = secret or os.environ.get("AWS_SECRET_ACCESS_KEY")
        self.token = token or os.environ.get("AWS_SESSION_TOKEN")
        if not self.access or not self.secret:
            raise NeedCredentials(f"s3://{bucket}", "s3", "AWS access key id and secret access key are required")
        self.endpoint = (endpoint or "").rstrip("/")
        self.pathstyle = bool(self.endpoint) if pathstyle is None else pathstyle
        if not self.endpoint and "." in bucket:
            self.pathstyle = True
        self._probe()

    def _target(self):
        if self.endpoint:
            u = urllib.parse.urlparse(self.endpoint)
            scheme, host = u.scheme, u.netloc
            base = u.path.rstrip("/")
        else:
            scheme = "https"
            host = f"s3.{self.region}.amazonaws.com" if self.region != "us-east-1" else "s3.amazonaws.com"
            base = ""
        if self.pathstyle:
            return scheme, host, f"{base}/{self.bucket}"
        return scheme, f"{self.bucket}.{host}", base

    def _request(self, method, key="", query=None, body=None, extra=None, local_in=None, local_out=None):
        query = dict(query or {})
        for attempt in (0, 1):
            scheme, host, base = self._target()
            path = urllib.parse.quote(f"{base}/{key}" if key else (base or "/"), safe="/~")
            if not path.startswith("/"):
                path = "/" + path
            if local_in is not None:
                ph = _file_sha256(local_in) if scheme == "http" else "UNSIGNED-PAYLOAD"
                size = os.path.getsize(local_in)
            else:
                ph = hashlib.sha256(body or b"").hexdigest()
                size = len(body or b"")
            hdrs = sigv4_headers(method, host, path, query, self.region, "s3", self.access, self.secret, self.token, ph,
                                 extra_headers=extra)
            hdrs["User-Agent"] = "LavatiCompare"
            url = f"{scheme}://{host}{path}"
            if query:
                url += "?" + "&".join(f"{_q(k)}={_q(str(v))}" for k, v in sorted(query.items()))
            if method in ("PUT", "POST"):
                hdrs["Content-Length"] = str(size)
            if local_out is not None:
                st, hd = http_download(url, hdrs, local_out, strip_auth_on_redirect=False)
                body_out = b""
            elif local_in is not None:
                with open(local_in, "rb") as f:
                    st, hd, body_out = http_request(method, url, hdrs, f, timeout=3600, retries=0)
            else:
                st, hd, body_out = http_request(method, url, hdrs, body)
            if st in (301, 400) and attempt == 0:
                region = {k.lower(): v for k, v in hd.items()}.get("x-amz-bucket-region")
                if region and region != self.region:
                    self.region = region
                    continue
            break
        if st == 403:
            raise RemoteError("S3 denied access (403) - check the keys, region and bucket policy")
        return st, hd, body_out

    def _probe(self):
        st, _, body = self._request("GET", "", {"list-type": "2", "max-keys": "1"})
        if st != 200:
            msg = re.sub(r"<[^>]+>", " ", body.decode("utf-8", "replace")).strip()[:200]
            raise RemoteError(f"S3 bucket not reachable ({st}): {msg}")

    def _key(self, path):
        return _norm(path).strip("/")

    def _list(self, path):
        key = self._key(path)
        prefix = key + "/" if key else ""
        out, token = [], None
        while True:
            q = {"list-type": "2", "delimiter": "/", "prefix": prefix}
            if token:
                q["continuation-token"] = token
            st, _, body = self._request("GET", "", q)
            if st != 200:
                raise RemoteError(f"{path}: S3 listing failed ({st})")
            root = ET.fromstring(body)
            ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
            for c in root.findall(f"{ns}Contents"):
                k = c.findtext(f"{ns}Key") or ""
                name = k[len(prefix):]
                if not name or "/" in name:
                    continue
                out.append(Entry(name, False, int(c.findtext(f"{ns}Size") or 0), parse_iso(c.findtext(f"{ns}LastModified") or "")))
                self._etags[k] = (c.findtext(f"{ns}ETag") or "").strip('"')
            for p in root.findall(f"{ns}CommonPrefixes"):
                name = (p.findtext(f"{ns}Prefix") or "")[len(prefix):].rstrip("/")
                if name:
                    out.append(Entry(name, True, 0, 0.0))
            if (root.findtext(f"{ns}IsTruncated") or "").lower() == "true":
                token = root.findtext(f"{ns}NextContinuationToken")
            else:
                break
        return out

    def checksum(self, path, algo="crc32", ctx=None):
        if algo == "md5":
            tag = self._etags.get(self._key(path), "")
            if tag and "-" not in tag and len(tag) == 32:
                return tag
            return None
        return super().checksum(path, algo, ctx)

    def _download(self, path, local):
        st, _, _ = self._request("GET", self._key(path), local_out=local)
        if st != 200:
            raise RemoteError(f"{path}: S3 download failed ({st})")

    def makedirs(self, path):
        pass            # S3 has no real folders; they appear when objects are written

    def put_file(self, local, path, mtime=None):
        size = os.path.getsize(local)
        if size > 5 * 1024 ** 3:
            raise RemoteError("Objects over 5 GB need multipart upload, which is not supported")
        extra = {"content-type": "application/octet-stream"}
        if mtime:
            extra["x-amz-meta-lavati-mtime"] = str(int(mtime))
        st, _, body = self._request("PUT", self._key(path), extra=extra, local_in=local)
        if st not in (200, 201):
            raise RemoteError(f"{path}: S3 upload failed ({st}) " + re.sub(r"<[^>]+>", " ", body.decode("utf-8", "replace"))[:160])
        self._after_write()

    def _keys_under(self, key):
        out, token = [], None
        while True:
            q = {"list-type": "2", "prefix": key + "/"}
            if token:
                q["continuation-token"] = token
            st, _, body = self._request("GET", "", q)
            if st != 200:
                raise RemoteError(f"S3 listing failed ({st})")
            root = ET.fromstring(body)
            ns = root.tag.split("}")[0] + "}" if root.tag.startswith("{") else ""
            out += [c.findtext(f"{ns}Key") for c in root.findall(f"{ns}Contents")]
            if (root.findtext(f"{ns}IsTruncated") or "").lower() == "true":
                token = root.findtext(f"{ns}NextContinuationToken")
            else:
                return out

    def remove(self, path, trash=False):
        key = self._key(path)
        st = self.stat(path)
        keys = self._keys_under(key) if (st and st.is_dir) else [key]
        for k in keys:
            s, _, _ = self._request("DELETE", k)
            if s not in (200, 204):
                raise RemoteError(f"{k}: S3 delete failed ({s})")
        self._after_write()

    def rename(self, path, new_path):
        src, dst = self._key(path), self._key(new_path)
        st = self.stat(path)
        pairs = ([(k, dst + k[len(src):]) for k in self._keys_under(src)] if (st and st.is_dir) else [(src, dst)])
        for a, b in pairs:
            s, _, _ = self._request("PUT", b, extra={"x-amz-copy-source": "/" + self.bucket + "/" + urllib.parse.quote(a, safe="/~")})
            if s != 200:
                raise RemoteError(f"{a}: S3 copy failed ({s})")
            self._request("DELETE", a)
        self._after_write()

    def set_mtime(self, path, mtime):
        raise RemoteError("S3 does not allow changing an object's modification time")



# ================================================================= Dropbox
DROPBOX_API = "https://api.dropboxapi.com/2"
DROPBOX_CONTENT = "https://content.dropboxapi.com/2"


class DropboxFS(RemoteFS):
    scheme = "dropbox"

    def __init__(self, token, root="/"):
        super().__init__("dropbox:/", root)
        if not token:
            raise NeedCredentials("dropbox", "token", "A Dropbox access token is required")
        self.token = token
        self._probe()

    def _h(self, extra=None):
        h = {"Authorization": f"Bearer {self.token}", "User-Agent": "LavatiCompare"}
        h.update(extra or {})
        return h

    def _rpc(self, endpoint, args):
        st, hd, body = http_request("POST", f"{DROPBOX_API}/{endpoint}", self._h({"Content-Type": "application/json"}),
                                    json.dumps(args).encode())
        if st == 401:
            raise NeedCredentials("dropbox", "token", "The Dropbox token was rejected or has expired")
        if st == 409:
            try:
                err = json.loads(body).get("error_summary", "")
            except ValueError:
                err = ""
            if "not_found" in err:
                return None
            raise RemoteError(f"Dropbox: {err or 'conflict'}")
        if st != 200:
            raise RemoteError(f"Dropbox error {st}: {body[:200].decode('utf-8', 'replace')}")
        return json.loads(body) if body else {}

    def _probe(self):
        st, _, body = http_request("POST", f"{DROPBOX_API}/users/get_current_account", self._h())
        if st == 401:
            raise NeedCredentials("dropbox", "token", "The Dropbox token was rejected or has expired")
        if st != 200:
            raise RemoteError(f"Dropbox error {st}")

    @staticmethod
    def _p(path):
        path = _norm(path)
        return "" if path == "/" else path

    def _list(self, path):
        res = self._rpc("files/list_folder", {"path": self._p(path), "limit": 2000})
        if res is None:
            return []
        out = []
        while True:
            for e in res.get("entries", []):
                tag = e.get(".tag")
                if tag == "folder":
                    out.append(Entry(e["name"], True, 0, 0.0))
                elif tag == "file":
                    out.append(Entry(e["name"], False, int(e.get("size", 0)), parse_iso(e.get("client_modified", ""))))
            if res.get("has_more"):
                res = self._rpc("files/list_folder/continue", {"cursor": res["cursor"]})
            else:
                return out

    def _download(self, path, local):
        st, _ = http_download(f"{DROPBOX_CONTENT}/files/download",
                              self._h({"Dropbox-API-Arg": json.dumps({"path": self._p(path)})}), local, method="POST")
        if st != 200:
            raise RemoteError(f"{path}: Dropbox download failed ({st})")

    def makedirs(self, path):
        p = self._p(path)
        if p:
            st, _, body = http_request("POST", f"{DROPBOX_API}/files/create_folder_v2", self._h({"Content-Type": "application/json"}),
                                       json.dumps({"path": p, "autorename": False}).encode())
            if st not in (200, 409):
                raise RemoteError(f"Dropbox error {st}")
        self._after_write()

    def put_file(self, local, path, mtime=None):
        size = os.path.getsize(local)
        commit = {"path": self._p(path), "mode": "overwrite", "mute": True}
        if mtime:
            commit["client_modified"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime))
        CH = 8 * 1024 * 1024
        if size <= 150 * 1024 * 1024:
            with open(local, "rb") as f:
                st, _, body = http_request("POST", f"{DROPBOX_CONTENT}/files/upload",
                                           self._h({"Dropbox-API-Arg": json.dumps(commit), "Content-Type": "application/octet-stream"}),
                                           f.read(), timeout=3600)
            if st != 200:
                raise RemoteError(f"{path}: Dropbox upload failed ({st}) {body[:160].decode('utf-8', 'replace')}")
        else:
            with open(local, "rb") as f:
                st, _, body = http_request("POST", f"{DROPBOX_CONTENT}/files/upload_session/start",
                                           self._h({"Dropbox-API-Arg": json.dumps({"close": False}), "Content-Type": "application/octet-stream"}),
                                           f.read(CH), timeout=3600)
                if st != 200:
                    raise RemoteError(f"Dropbox upload session failed ({st})")
                sid, offset = json.loads(body)["session_id"], CH
                while size - offset > CH:
                    arg = {"cursor": {"session_id": sid, "offset": offset}, "close": False}
                    st, _, _ = http_request("POST", f"{DROPBOX_CONTENT}/files/upload_session/append_v2",
                                            self._h({"Dropbox-API-Arg": json.dumps(arg), "Content-Type": "application/octet-stream"}),
                                            f.read(CH), timeout=3600)
                    if st != 200:
                        raise RemoteError(f"Dropbox upload failed ({st})")
                    offset += CH
                arg = {"cursor": {"session_id": sid, "offset": offset}, "commit": commit}
                st, _, _ = http_request("POST", f"{DROPBOX_CONTENT}/files/upload_session/finish",
                                        self._h({"Dropbox-API-Arg": json.dumps(arg), "Content-Type": "application/octet-stream"}),
                                        f.read(), timeout=3600)
                if st != 200:
                    raise RemoteError(f"Dropbox upload failed ({st})")
        self._after_write()

    def remove(self, path, trash=False):
        self._rpc("files/delete_v2", {"path": self._p(path)})
        self._after_write()

    def rename(self, path, new_path):
        self._rpc("files/move_v2", {"from_path": self._p(path), "to_path": self._p(new_path), "autorename": False})
        self._after_write()


# ================================================================ OneDrive
GRAPH = "https://graph.microsoft.com/v1.0"


class OneDriveFS(RemoteFS):
    scheme = "onedrive"

    def __init__(self, token, root="/"):
        super().__init__("onedrive:/", root)
        if not token:
            raise NeedCredentials("onedrive", "token", "A Microsoft Graph access token is required")
        self.token = token
        st, _, _ = http_request("GET", f"{GRAPH}/me/drive", self._h())
        if st == 401:
            raise NeedCredentials("onedrive", "token", "The OneDrive token was rejected or has expired")
        if st != 200:
            raise RemoteError(f"OneDrive error {st}")

    def _h(self, extra=None):
        h = {"Authorization": f"Bearer {self.token}", "User-Agent": "LavatiCompare"}
        h.update(extra or {})
        return h

    @staticmethod
    def _item(path):
        path = _norm(path)
        return f"{GRAPH}/me/drive/root" + ("" if path == "/" else ":" + urllib.parse.quote(path, safe="/~") + ":")

    def _json(self, method, url, body=None, ok=(200, 201, 204)):
        h = self._h({"Content-Type": "application/json"} if body is not None else None)
        st, _, data = http_request(method, url, h, json.dumps(body).encode() if body is not None else None)
        if st == 404:
            return None
        if st == 401:
            raise NeedCredentials("onedrive", "token", "The OneDrive token was rejected or has expired")
        if st not in ok:
            raise RemoteError(f"OneDrive error {st}: {data[:200].decode('utf-8', 'replace')}")
        return json.loads(data) if data else {}

    def _list(self, path):
        url = self._item(path) + ("/children" if _norm(path) == "/" else "/children") + "?$top=200"
        out = []
        while url:
            res = self._json("GET", url)
            if res is None:
                return out
            for e in res.get("value", []):
                isdir = "folder" in e
                out.append(Entry(e["name"], isdir, 0 if isdir else int(e.get("size", 0)),
                                 parse_iso((e.get("fileSystemInfo") or {}).get("lastModifiedDateTime") or e.get("lastModifiedDateTime", ""))))
            url = res.get("@odata.nextLink")
        return out

    def _download(self, path, local):
        st, _ = http_download(self._item(path) + "/content", self._h(), local)
        if st != 200:
            raise RemoteError(f"{path}: OneDrive download failed ({st})")

    def makedirs(self, path):
        cur = ""
        for part in [p for p in _norm(path).split("/") if p]:
            parent = cur or "/"
            cur += "/" + part
            if self._json("GET", self._item(cur)) is None:
                self._json("POST", self._item(parent) + "/children",
                           {"name": part, "folder": {}, "@microsoft.graph.conflictBehavior": "fail"})
        self._after_write()

    def put_file(self, local, path, mtime=None):
        path = _norm(path)
        self.makedirs(self.parent(path))
        size = os.path.getsize(local)
        fsinfo = {}
        if mtime:
            fsinfo = {"lastModifiedDateTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime))}
        if size <= 4 * 1024 * 1024:
            with open(local, "rb") as f:
                st, _, body = http_request("PUT", self._item(path) + "/content", self._h({"Content-Type": "application/octet-stream"}),
                                           f.read(), timeout=600)
            if st not in (200, 201):
                raise RemoteError(f"{path}: OneDrive upload failed ({st})")
        else:
            res = self._json("POST", self._item(path) + "/createUploadSession",
                             {"item": {"@microsoft.graph.conflictBehavior": "replace", **({"fileSystemInfo": fsinfo} if fsinfo else {})}})
            url = res["uploadUrl"]
            CH = 320 * 1024 * 32
            with open(local, "rb") as f:
                off = 0
                while off < size:
                    chunk = f.read(CH)
                    h = {"Content-Length": str(len(chunk)), "Content-Range": f"bytes {off}-{off + len(chunk) - 1}/{size}"}
                    st, _, body = http_request("PUT", url, h, chunk, timeout=3600)      # pre-authenticated URL: no bearer
                    if st not in (200, 201, 202):
                        raise RemoteError(f"{path}: OneDrive upload failed ({st})")
                    off += len(chunk)
            self._after_write()
            return
        if fsinfo:
            try:
                self._json("PATCH", self._item(path), {"fileSystemInfo": fsinfo})
            except RemoteError:
                pass
        self._after_write()

    def set_mtime(self, path, mtime):
        self._json("PATCH", self._item(path), {"fileSystemInfo": {"lastModifiedDateTime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime))}})
        self._after_write()

    def remove(self, path, trash=False):
        self._json("DELETE", self._item(path))
        self._after_write()

    def rename(self, path, new_path):
        self.makedirs(self.parent(new_path))
        parent = self._json("GET", self._item(self.parent(new_path)))
        self._json("PATCH", self._item(path), {"name": self.basename(new_path), "parentReference": {"id": parent["id"]}})
        self._after_write()


# ======================================================================= SVN
class SVNFS(RemoteFS):
    scheme = "svn"
    readonly = True

    def __init__(self, url, user=None, password=None, rev=None):
        self.url = url.rstrip("/")
        super().__init__(self.url + (f"@{rev}" if rev else ""), "/")
        self.user, self.password, self.rev = user, password, rev
        self.svn = shutil.which("svn")
        if not self.svn:
            raise RemoteError("Subversion support needs the 'svn' command line client on the PATH")
        self._svn(["info", "--xml", self._target("/")])

    def _target(self, path):
        p = _norm(path)
        return self.url + ("" if p == "/" else urllib.parse.quote(p, safe="/~")) + (f"@{self.rev}" if self.rev else "")

    def _svn(self, args, binary=False, out=None):
        cmd = [self.svn, "--non-interactive"] + args
        stdin = None
        if self.user:
            cmd += ["--username", self.user]
        if self.password:
            cmd += ["--password-from-stdin"]
            stdin = (self.password + "\n").encode()
        try:
            res = subprocess.run(cmd, input=stdin, capture_output=True, timeout=300)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise RemoteError(f"svn failed: {e}") from e
        if res.returncode != 0:
            msg = res.stderr.decode("utf-8", "replace").strip()
            if "authorization failed" in msg.lower() or "authentication" in msg.lower():
                raise NeedCredentials(cred_key("svn", self.user, urllib.parse.urlparse(self.url).netloc), "password", msg[:200])
            raise RemoteError(f"svn: {msg[:300]}")
        return res.stdout

    def _list(self, path):
        root = ET.fromstring(self._svn(["list", "--xml", self._target(path)]))
        out = []
        for e in root.iter("entry"):
            name = e.findtext("name")
            size = e.findtext("size")
            date = e.findtext("commit/date")
            out.append(Entry(name, e.get("kind") == "dir", int(size or 0), parse_iso(date) if date else 0.0))
        return out

    def _download(self, path, local):
        data = self._svn(["cat", self._target(path)])
        with open(local, "wb") as f:
            f.write(data)

    def makedirs(self, path):
        self._ro()


# ====================================================================== URLs
def _qs(u):
    return {k: v[0] for k, v in urllib.parse.parse_qs(u.query).items()}


def _creds_for(u, scheme, needs=True, password=None):
    user = urllib.parse.unquote(u.username) if u.username else None
    pw = urllib.parse.unquote(u.password) if u.password else password
    key = cred_key(scheme, user, u.hostname or "", u.port)
    if pw is None:
        pw = get_credential(key)
    return user, pw, key


def open_url(url, password=None):
    """Create the file system for a URL. Returns (fs, root_path)."""
    u = urllib.parse.urlparse(url)
    scheme = u.scheme.lower()
    path = urllib.parse.unquote(u.path) or "/"
    qs = _qs(u)
    if scheme in ("ftp", "ftps"):
        user, pw, key = _creds_for(u, scheme, password=password)
        if user and pw is None:
            raise NeedCredentials(key, "password", f"Password for {user}@{u.hostname}")
        return FTPFS(u.hostname, u.port or 21, user, pw, tls=(scheme == "ftps")), _norm(path)
    if scheme == "sftp":
        user, pw, key = _creds_for(u, scheme, password=password)
        keyfile = get_credential_extra(key).get("key_file") or qs.get("key")
        return SFTPFS(u.hostname, u.port or 22, user, pw, keyfile), _norm(path)
    if scheme in ("dav", "davs", "webdav", "webdavs"):
        user, pw, key = _creds_for(u, "dav", password=password)
        http = "https" if scheme.endswith("s") else "http"
        base = f"{http}://{u.netloc.split('@')[-1]}"
        fs = WebDAVFS(base, user, pw, label=f"{scheme}://{u.netloc.split('@')[-1]}")
        return fs, _norm(path)
    if scheme == "s3":
        bucket = u.hostname or u.netloc
        access = urllib.parse.unquote(u.username) if u.username else None
        secret = urllib.parse.unquote(u.password) if u.password else None
        key = f"s3://{bucket}"
        if not (access and secret):
            c = get_credential(key)
            if c:
                access, secret = c.split(":", 1) if ":" in c else (c, None)
        fs = S3FS(bucket, path.strip("/"), qs.get("region"), qs.get("endpoint"), access, secret, qs.get("token"),
                  pathstyle={"1": True, "0": False}.get(qs.get("pathstyle")))
        return fs, fs.root
    if scheme == "dropbox":
        token = get_credential("dropbox") or password or os.environ.get("DROPBOX_TOKEN")
        return DropboxFS(token), _norm(path)
    if scheme == "onedrive":
        token = get_credential("onedrive") or password or os.environ.get("ONEDRIVE_TOKEN")
        return OneDriveFS(token), _norm(path)
    if scheme.startswith("svn"):
        real = scheme[4:] if scheme.startswith("svn+") else "svn"
        if real in ("http", "https", "file"):
            base = f"{real}://{u.netloc.split('@')[-1]}"
        elif real == "svn" and scheme == "svn":
            base = f"svn://{u.netloc.split('@')[-1]}"
        else:
            base = f"{scheme}://{u.netloc.split('@')[-1]}"
        user, pw, key = _creds_for(u, "svn", password=password)
        rev = qs.get("rev")
        return SVNFS(base + path.rstrip("/"), user, pw, rev), "/"
    raise RemoteError(f"Unknown location type: {scheme}://")


def split_secrets(url):
    """Remove a password embedded in a URL so sessions can be stored safely. Returns (clean_url, password)."""
    u = urllib.parse.urlparse(url)
    if u.password is None:
        return url, None
    netloc = (urllib.parse.quote(urllib.parse.unquote(u.username or "")) + "@" if u.username else "") + (u.hostname or "")
    if u.port:
        netloc += f":{u.port}"
    return urllib.parse.urlunparse(u._replace(netloc=netloc)), urllib.parse.unquote(u.password)


def stash_url_secrets(url):
    """Keep a password embedded in a URL in memory only. Returns the URL without the password."""
    if not isinstance(url, str) or "://" not in url or (len(url) > 1 and url[1] == ":"):
        return url
    clean, pw = split_secrets(url)
    if pw is None:
        return url
    u = urllib.parse.urlparse(url)
    scheme = u.scheme.lower()
    user = urllib.parse.unquote(u.username) if u.username else None
    if scheme == "s3":
        set_credential(f"s3://{u.hostname}", f"{user}:{pw}" if user else pw)
        return clean
    family = {"davs": "dav", "webdav": "dav", "webdavs": "dav"}.get(scheme, scheme)
    if family.startswith("svn"):
        family = "svn"
    set_credential(cred_key(family, user, u.hostname or "", u.port), pw)
    return clean
