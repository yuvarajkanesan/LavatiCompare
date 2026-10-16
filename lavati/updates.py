"""Look for a newer release on GitHub. Only a plain request for the public 'latest release' page is made; nothing about the
user or the files is sent. No Qt imports."""
import json
import re

from . import __version__, remote

REPO = "yuvarajkanesan/LavatiCompare"
API = f"https://api.github.com/repos/{REPO}/releases/latest"


class UpdateError(Exception):
    pass


def parse_version(text):
    """'v2.1.3-beta' -> (2, 1, 3). Unparseable text gives ()."""
    m = re.match(r"\s*v?(\d+(?:\.\d+)*)", text or "")
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def is_newer(latest, current=__version__):
    a, b = parse_version(latest), parse_version(current)
    n = max(len(a), len(b))
    return bool(a) and a + (0,) * (n - len(a)) > b + (0,) * (n - len(b))


def check(current=__version__, timeout=10):
    """Returns {'status': 'newer'|'current'|'none', 'version', 'url', 'notes'}. Raises UpdateError when offline or rate-limited."""
    try:
        status, _hdrs, body = remote.http_request("GET", API, {"User-Agent": "LavatiCompare", "Accept": "application/vnd.github+json"},
                                                  timeout=timeout, retries=0)
    except remote.RemoteError as e:
        raise UpdateError(str(e)) from e
    if status == 404:
        return {"status": "none", "version": "", "url": f"https://github.com/{REPO}/releases", "notes": ""}
    if status != 200:
        raise UpdateError(f"GitHub answered {status}")
    try:
        rel = json.loads(body.decode("utf-8", "replace"))
        tag = rel.get("tag_name") or ""
    except ValueError as e:
        raise UpdateError("Unreadable answer from GitHub") from e
    url = rel.get("html_url") or f"https://github.com/{REPO}/releases"
    if not url.startswith(f"https://github.com/{REPO}"):
        url = f"https://github.com/{REPO}/releases"                  # never send the user anywhere else
    return {"status": "newer" if is_newer(tag, current) else "current", "version": tag.lstrip("vV"), "url": url,
            "notes": (rel.get("body") or "")[:1500]}
