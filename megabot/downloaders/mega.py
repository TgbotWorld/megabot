# MEGA downloader — BaseDownloader facade over the raw API client (mega_raw.py)
#   file links   → mega.py's download_url (+ dir-size watcher for progress)
#   folder links → raw 'f'/'g' requests with the nk key
# Sessions are cached in MongoDB so each user logs in once, ever.
import logging
import os
import re

from mega.errors import RequestError

from config import MEGA_EMAIL, MEGA_PASSWORD
from megabot.downloaders.base import BaseDownloader
from megabot.downloaders.mega_raw import RawMega

log = logging.getLogger(__name__)

MEGA_URL_RE = re.compile(
    r"https?://(?:[a-zA-Z0-9\-._]+\.)?mega\.(?:nz|io|co\.nz)/(?:(?:file|folder)/[\w\-]+#[\w\-]+|#(?:F!)?[\w\-]+![\w\-]+|[^\s\"\'<>]+)|https?://[a-zA-Z0-9\-._]+\.usercontent\.mega\.co\.nz/[^\s\"\'<>]+",
    re.I
)


def is_mega_direct_link(url: str) -> bool:
    """Check if URL is a direct MEGA storage/download link rather than an encrypted handle link."""
    if not url:
        return False
    url_l = url.lower()
    return bool(
        "usercontent.mega.co.nz" in url_l
        or "usercontent.mega.nz" in url_l
        or re.search(r"https?://gfs\d*\.mega\.(?:nz|io|co\.nz)/", url_l)
        or ("/direct/" in url_l and "mega." in url_l)
    )


def extract_mega_links(text: str) -> list[str]:
    """Pull all MEGA file/folder links out of arbitrary text."""
    if not text:
        return []
    matches = MEGA_URL_RE.findall(text)
    cleaned = [m.rstrip(".,;:!?)'\"") for m in matches]
    return list(dict.fromkeys(cleaned))


def link_key(url: str) -> str:
    """Stable identifier for a MEGA link (for the dedup cache)."""
    m = re.search(r"/(?:file/|folder/|#F!|#!)([\w\-]+)", url)
    if m:
        return m.group(1)
    import hashlib
    h = hashlib.sha256(url.encode()).hexdigest()[:12]
    return f"mega_dir_{h}"


class MegaDownloader(BaseDownloader):
    def __init__(self, email: str = None, password: str = None,
                 saved_session: dict = None):
        self._raw = RawMega(email or MEGA_EMAIL, password or MEGA_PASSWORD)
        self._saved_session = saved_session
        self.session_fresh = False      # True after a real (fresh) login
        self._folder_cache: dict[str, dict] = {}
        from megabot.downloaders.direct import DirectDownloader
        self._direct = DirectDownloader()

    def login(self) -> None:
        self.session_fresh = self._raw.login_with_session(self._saved_session)
        self._saved_session = None      # cached session only tried once

    def session_state(self) -> dict:
        return self._raw.session_state()

    def probe(self, url: str) -> dict:
        if is_mega_direct_link(url):
            return self._direct.probe(url)
        if RawMega.parse_folder_url(url):
            listing = self._listing(url)
            return {"name": listing["name"], "size": listing["size"],
                    "kind": "folder", "files": listing["files"]}
        return self._raw.probe_file(url)

    def download(self, url: str, dest_dir: str, progress_cb=None) -> str:
        os.makedirs(dest_dir, exist_ok=True)
        if is_mega_direct_link(url):
            return self._direct.download(url, dest_dir, progress_cb)
        if RawMega.parse_folder_url(url):
            return self._raw.download_folder(url, dest_dir, progress_cb,
                                             listing=self._listing(url))
        return self._download_file_link(url, dest_dir, progress_cb)

    # ── internals ────────────────────────────────────────────

    def _listing(self, url: str) -> dict:
        """Folder listing cached so probe + download share one 'f' request."""
        if url not in self._folder_cache:
            self._folder_cache[url] = self._raw.list_folder(url)
        return self._folder_cache[url]

    def _download_file_link(self, url: str, dest_dir: str, progress_cb) -> str:
        # raw streaming download with real per-chunk progress — mega.py's own
        # download_url writes to a temp file and only moves it at the end,
        # which froze the progress bar at 0%
        self._raw.download_file(url, dest_dir, progress_cb)
        return dest_dir

    def rescue_siblings(self, lone_path: str, dest_dir: str, progress_cb=None) -> bool:
        """A middle volume of a split archive arrived alone: search the
        user's own MEGA account for the missing volumes and download them
        next to it. True when volume 1 is present afterwards."""
        from megabot.analyzers.classify import volume_base, _volume_number
        lone_name = os.path.basename(lone_path)
        base = volume_base(lone_name)
        if not self._raw.logged_in:
            return False
        try:
            candidates = [f for f in self._raw.account_files()
                          if volume_base(f["name"]) == base
                          and f["name"] != lone_name]
        except Exception as e:
            log.warning("sibling search failed: %s", e)
            return False
        existing = set(os.listdir(dest_dir))
        todo = [c for c in candidates if c["name"] not in existing]
        if not todo:
            return False
        log.info("rescuing %d sibling volume(s) for %s", len(todo), lone_name)
        for c in todo:
            def _cb(d, t, label=c["name"]):
                if progress_cb:
                    progress_cb(d, t, label)
            self._raw.download_account_file(c, dest_dir, _cb)
        # extraction can only start from volume 1
        vols = [_volume_number(n) for n in os.listdir(dest_dir)
                if volume_base(n) == base]
        vols = [v for v in vols if v is not None]
        return bool(vols) and min(vols) == 1