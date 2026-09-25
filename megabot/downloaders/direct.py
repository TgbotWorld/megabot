# Direct / Generic HTTP & HTTPS Downloader
import hashlib
import logging
import os
import re
import urllib.parse
from typing import Callable, Optional
import requests

from megabot.downloaders.base import BaseDownloader

log = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}

DIRECT_URL_RE = re.compile(
    r"https?://(?:[a-zA-Z0-9\-._~%!$&'()*+,;=:@]+|\[[a-fA-F0-9:]+\])(?::\d+)?(?:/[^\s\"\'<>]*)?",
    re.I
)

# Common domains handled by specialized downloaders
SPECIALIZED_DOMAINS = (
    "mega.nz", "mega.io", "mega.co.nz",
    "mediafire.com",
    "mp4upload.com",
    "terabox.com", "terabox.app", "1024tera.com", "terafileshare.com",
    "freeterabox.com", "mirrobox.com", "nephobox.com", "tibibox.com", "4funbox.com",
)


def is_direct_link(url: str) -> bool:
    """Check if URL is a direct HTTP/HTTPS link not handled by specialized downloaders."""
    if not url:
        return False
    url_l = url.lower()
    if not (url_l.startswith("http://") or url_l.startswith("https://")):
        return False
    try:
        parsed = urllib.parse.urlparse(url)
        host = (parsed.hostname or "").lower()
        if not host:
            return False
        for d in SPECIALIZED_DOMAINS:
            if host == d or host.endswith("." + d):
                return False
        return True
    except Exception:
        return False


def extract_direct_links(text: str) -> list[str]:
    """Extract direct download URLs from text."""
    if not text:
        return []
    matches = DIRECT_URL_RE.findall(text)
    result = []
    seen = set()
    for m in matches:
        clean = m.rstrip(".,;:!?)'\"")
        if is_direct_link(clean) and clean not in seen:
            seen.add(clean)
            result.append(clean)
    return result


def direct_link_key(url: str) -> str:
    """Stable cache identifier for a direct link."""
    h = hashlib.sha256(url.encode()).hexdigest()[:12]
    return f"dir_{h}"


class DirectDownloader(BaseDownloader):
    """Downloads files directly from HTTP / HTTPS URLs."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def login(self) -> None:
        pass

    def probe(self, url: str) -> dict:
        """Inspect HTTP/HTTPS header to determine filename and size."""
        filename = None
        size = 0

        try:
            head = self.session.head(url, allow_redirects=True, timeout=15)
            if head.status_code < 400:
                cd = head.headers.get("content-disposition", "")
                filename = self._parse_content_disposition(cd)
                if "content-length" in head.headers:
                    try:
                        size = int(head.headers["content-length"])
                    except Exception:
                        pass
        except Exception as e:
            log.debug("Direct HEAD probe failed for %s: %s", url, e)

        # Fallback to GET stream if HEAD didn't give content-length or failed
        if not filename or size == 0:
            try:
                with self.session.get(url, stream=True, allow_redirects=True, timeout=15) as r:
                    if r.status_code < 400:
                        if not filename:
                            cd = r.headers.get("content-disposition", "")
                            filename = self._parse_content_disposition(cd)
                        if size == 0 and "content-length" in r.headers:
                            try:
                                size = int(r.headers["content-length"])
                            except Exception:
                                pass
            except Exception as e:
                log.debug("Direct GET stream probe failed for %s: %s", url, e)

        if not filename:
            parsed = urllib.parse.urlparse(url)
            path_name = os.path.basename(urllib.parse.unquote(parsed.path or ""))
            if path_name and "." in path_name:
                filename = path_name

        if not filename:
            url_hash = hashlib.md5(url.encode()).hexdigest()[:8]
            filename = f"download_{url_hash}"

        # Clean filename
        filename = re.sub(r'[\\/*?:"<>|]', "_", filename).strip()

        return {
            "name": filename,
            "size": size,
            "kind": "file",
            "direct_url": url,
        }

    def _parse_content_disposition(self, header: str) -> Optional[str]:
        if not header:
            return None
        # Try UTF-8 encoding: filename*=UTF-8''...
        m_utf = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", header, re.I)
        if m_utf:
            return urllib.parse.unquote(m_utf.group(1).strip("\"' "))
        # Try standard filename="example.ext"
        m = re.search(r'filename\s*=\s*"([^"]+)"', header, re.I)
        if m:
            return m.group(1).strip()
        m_bare = re.search(r'filename\s*=\s*([^\s;]+)', header, re.I)
        if m_bare:
            return m_bare.group(1).strip("\"' ")
        return None

    def download(self, url: str, dest_dir: str,
                 progress_cb: Optional[Callable[[int, int], None]] = None) -> str:
        """Download file directly into dest_dir with live progress."""
        os.makedirs(dest_dir, exist_ok=True)
        info = self.probe(url)
        filename = info.get("name") or "downloaded_file"
        dest_path = os.path.join(dest_dir, filename)

        with self.session.get(url, stream=True, allow_redirects=True, timeout=60) as r:
            r.raise_for_status()
            total_size = info.get("size") or int(r.headers.get("content-length", 0))

            done = 0
            chunk_size = 256 * 1024  # 256 KB
            with open(dest_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        done += len(chunk)
                        if progress_cb and total_size:
                            progress_cb(done, total_size)

        log.info("Direct download completed: %s (%d bytes)", dest_path, done)
        return dest_path
