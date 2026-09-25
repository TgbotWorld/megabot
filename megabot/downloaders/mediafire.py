# MediaFire Downloader — Advanced implementation supporting web pages, folders, and direct CDN links
import hashlib
import json
import logging
import os
import re
import urllib.parse
from typing import Callable, Optional
import requests

from megabot.downloaders.base import BaseDownloader

log = logging.getLogger(__name__)

# Matches any MediaFire URL, including www, direct download subdomains (download*.mediafire.com), short links, and folders
MEDIAFIRE_URL_RE = re.compile(
    r"https?://(?:[a-zA-Z0-9\-._]+\.)?mediafire\.com/[^\s\"\'<>]+",
    re.I
)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.mediafire.com/",
    "Sec-Ch-Ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}


def extract_mediafire_links(text: str) -> list[str]:
    """Pull all MediaFire links out of arbitrary text, preserving order and deduplicating."""
    if not text:
        return []
    matches = MEDIAFIRE_URL_RE.findall(text)
    cleaned = []
    seen = set()
    for m in matches:
        clean = m.rstrip(".,;:!?)'\"")
        if clean not in seen:
            seen.add(clean)
            cleaned.append(clean)
    return cleaned


def is_mediafire_link(url: str) -> bool:
    """Return True if URL is a valid MediaFire link (page, folder, or direct CDN download)."""
    if not isinstance(url, str):
        return False
    return bool(MEDIAFIRE_URL_RE.search(url))


def is_mediafire_direct_link(url: str) -> bool:
    """Return True if URL is already a direct MediaFire download link rather than an HTML page."""
    if not isinstance(url, str):
        return False
    url_l = url.lower()
    return bool(
        re.search(r"https?://download\d*\.mediafire\.com/", url_l)
        or "dynamicdownload.php" in url_l
        or "/file_premium/" in url_l
    )


def extract_quickkey(url: str) -> str:
    """Extract MediaFire quickkey from URL if present."""
    if not isinstance(url, str):
        return ""
    m = re.search(r"/(?:file|download|view|folder)/([a-zA-Z0-9_-]+)", url)
    if m:
        return m.group(1)
    m_q = re.search(r"[?&](?:quickkey=)?([a-zA-Z0-9]{8,15})", url)
    if m_q:
        return m_q.group(1)
    return ""


def mediafire_link_key(url: str) -> str:
    """Stable identifier for a MediaFire link (for the dedup cache)."""
    if not isinstance(url, str):
        return "mf_unknown"
    qk = extract_quickkey(url)
    if qk:
        return f"mf_{qk}"

    # For direct download links: extract filename or hash of URL
    parsed = urllib.parse.urlparse(url)
    fname = os.path.basename(parsed.path)
    if fname:
        return f"mf_{fname}"
    h = hashlib.md5(url.encode()).hexdigest()[:10]
    return f"mf_{h}"


class MediaFireDownloader(BaseDownloader):
    """
    Downloads files and folders from MediaFire.
    Handles HTML file pages, direct CDN links (download*.mediafire.com),
    dynamic links, and folder listings with automatic fallbacks.
    """

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)

    def login(self) -> None:
        """MediaFire downloads are anonymous."""
        pass

    def probe(self, url: str) -> dict:
        """Inspect MediaFire URL to determine filename, filesize, and kind."""
        if "/folder/" in url:
            return self._probe_folder(url)
        return self._probe_file(url)

    def _probe_file(self, url: str) -> dict:
        info = self._resolve_file_info(url)
        return {
            "name": info["name"],
            "size": info["size"],
            "kind": "file",
            "direct_url": info.get("direct_url"),
        }

    def _probe_folder(self, url: str) -> dict:
        m = re.search(r"/folder/([a-zA-Z0-9_-]+)", url)
        folder_key = m.group(1) if m else ""
        folder_name = "MediaFire Folder"
        total_size = 0
        file_urls = []

        if folder_key:
            try:
                # Query official folder API
                api_url = (
                    f"https://www.mediafire.com/api/1.4/folder/get_content.php?"
                    f"folder_key={folder_key}&content_type=files&response_format=json"
                )
                resp = self.session.get(api_url, timeout=15)
                if resp.status_code == 200:
                    data = resp.json()
                    folder_content = data.get("response", {}).get("folder_content", {})
                    files_data = folder_content.get("files", [])
                    for f in files_data:
                        total_size += int(f.get("size", 0))
                        file_name = f.get("filename", "")
                        quickkey = f.get("quickkey", "")
                        if quickkey:
                            file_urls.append(f"https://www.mediafire.com/file/{quickkey}/{file_name}")
            except Exception as e:
                log.warning("MediaFire folder API query failed: %s", e)

        # Fallback to HTML if API did not return files
        if not file_urls:
            try:
                resp = self.session.get(url, timeout=15)
                html = resp.text
                title_match = re.search(r"<title>(.*?)(?: - MediaFire)?</title>", html, re.I)
                if title_match:
                    folder_name = title_match.group(1).strip()
                matches = re.findall(r'href=["\'](https?://(?:www\.)?mediafire\.com/file/[^"\']+)["\']', html)
                file_urls = list(dict.fromkeys(matches))
            except Exception as e:
                log.warning("MediaFire folder HTML probe error: %s", e)

        return {
            "name": folder_name,
            "size": total_size,
            "kind": "folder",
            "files": file_urls,
        }

    def _resolve_direct_link_info(self, direct_url: str) -> dict:
        """Handle links that are already direct download URLs (download*.mediafire.com)."""
        filename = None
        size = 0

        # Try HEAD request to query Content-Disposition and Content-Length
        try:
            head_headers = self.session.headers.copy()
            head_headers["Referer"] = "https://www.mediafire.com/"
            head_resp = self.session.head(direct_url, headers=head_headers, allow_redirects=True, timeout=15)
            if head_resp.status_code < 400:
                cd = head_resp.headers.get("content-disposition", "")
                filename = self._parse_content_disposition(cd)
                if "content-length" in head_resp.headers:
                    try:
                        size = int(head_resp.headers["content-length"])
                    except Exception:
                        pass
        except Exception as e:
            log.debug("MediaFire direct HEAD failed: %s", e)

        if not filename:
            parsed = urllib.parse.urlparse(direct_url)
            base = os.path.basename(urllib.parse.unquote(parsed.path))
            if base and "." in base:
                filename = base

        if not filename:
            filename = "mediafire_file"

        filename = re.sub(r'[\\/*?:"<>|]', "_", filename).strip()

        return {
            "name": filename,
            "size": size,
            "direct_url": direct_url,
        }

    def _resolve_file_info(self, url: str) -> dict:
        """Resolve direct download link, filename, and size from a MediaFire URL."""
        # 1. Check if the URL is ALREADY a direct download link
        if is_mediafire_direct_link(url):
            return self._resolve_direct_link_info(url)

        # 2. Fetch the MediaFire page (with stream=True to prevent accidental large downloads on redirect)
        resp = self.session.get(url, stream=True, allow_redirects=True, timeout=25)
        if resp.status_code != 200:
            raise RuntimeError(f"MediaFire page returned HTTP {resp.status_code}")

        # Check if MediaFire redirected directly to the CDN stream
        final_url = resp.url if isinstance(getattr(resp, "url", None), str) else url
        raw_headers = resp.headers if hasattr(resp, "headers") and hasattr(resp.headers, "get") else {}
        content_type = raw_headers.get("content-type", "")
        if isinstance(content_type, str):
            content_type = content_type.lower()
        else:
            content_type = ""

        if is_mediafire_direct_link(final_url) or ("text/html" not in content_type and "application" in content_type):
            cd = raw_headers.get("content-disposition", "")
            cd_str = cd if isinstance(cd, str) else ""
            fname = self._parse_content_disposition(cd_str) or os.path.basename(urllib.parse.unquote(urllib.parse.urlparse(final_url).path))
            try:
                size = int(raw_headers.get("content-length", 0))
            except (ValueError, TypeError):
                size = 0
            return {
                "name": re.sub(r'[\\/*?:"<>|]', "_", fname or "mediafire_file").strip(),
                "size": size,
                "direct_url": final_url,
            }

        # 3. Read HTML text (check resp.text first for mocks/normal responses, fallback to resp.raw.read)
        html = ""
        if hasattr(resp, "text") and isinstance(resp.text, str):
            html = resp.text
        elif hasattr(resp, "raw") and hasattr(resp.raw, "read"):
            try:
                content_bytes = resp.raw.read(1024 * 1024)
                if isinstance(content_bytes, bytes):
                    html = content_bytes.decode("utf-8", errors="ignore")
            except Exception:
                pass

        # 4. Search for direct download link in HTML with comprehensive patterns
        direct_url = None
        patterns = [
            # Standard button and link patterns
            r'id=["\']downloadButton["\']\s+href=["\']([^"\']+)["\']',
            r'href=["\']([^"\']+)["\']\s+id=["\']downloadButton["\']',
            r'aria-label=["\']Download file["\']\s+href=["\']([^"\']+)["\']',
            r'href=["\']([^"\']+)["\']\s+aria-label=["\']Download file["\']',
            r'class=["\'][^"\']*(?:opensdl|popsok|download_link)[^"\']*["\']\s+href=["\']([^"\']+)["\']',
            r'href=["\'](https?://download\d*\.mediafire\.com/[^"\']+)["\']',
            r'data-download-url=["\']([^"\']+)["\']',
            r'data-href=["\'](https?://download\d*\.mediafire\.com/[^"\']+)["\']',
            # Embedded JavaScript variables
            r'kNO\s*=\s*["\'](https?://[^"\']+)["\']',
            r'window\.location\.href\s*=\s*["\'](https?://download\d*\.mediafire\.com/[^"\']+)["\']',
            r'DLP_URL\s*=\s*["\'](https?://[^"\']+)["\']',
            r'downloadUrl\s*=\s*["\'](https?://[^"\']+)["\']',
            r'(https?://download\d*\.mediafire\.com/[^\s"\'<>]+)',
            r'(https?://[a-zA-Z0-9.\-_]*mediafire\.com/dynamicdownload\.php\?[^\s"\'<>]+)',
        ]
        for pat in patterns:
            match = re.search(pat, html, re.I)
            if match:
                candidate = match.group(1)
                if candidate.startswith("http") and ("download" in candidate or "dynamicdownload" in candidate):
                    direct_url = candidate
                    break

        # 5. Fallback: Query MediaFire Official Public API if scraper didn't locate direct link
        if not direct_url:
            qk = extract_quickkey(url)
            if qk:
                try:
                    api_link_url = f"https://www.mediafire.com/api/1.4/file/get_links.php?quick_key={qk}&link_type=direct_download&response_format=json"
                    api_resp = self.session.get(api_link_url, timeout=10)
                    if api_resp.status_code == 200:
                        j = api_resp.json()
                        links_data = j.get("response", {}).get("links", [])
                        if links_data and isinstance(links_data, list):
                            direct_url = links_data[0].get("direct_download")
                except Exception as e:
                    log.debug("MediaFire get_links API fallback failed: %s", e)

        if not direct_url:
            raise RuntimeError(
                "Could not find direct download link on MediaFire page. "
                "The file may have been deleted, blocked by copyright, or requires a password."
            )

        # 6. Extract Filename
        name = None
        name_patterns = [
            r'<div\s+class=["\']filename["\']>(.*?)</div>',
            r'<div\s+class=["\']dl-btn-label["\']\s+title=["\']([^"\']+)["\']',
            r'<meta\s+property=["\']og:title["\']\s+content=["\']([^"\']+)["\']',
            r'<span\s+class=["\']filename["\']>(.*?)</span>',
            r'<title>(.*?)(?: - MediaFire)?</title>',
        ]
        for np in name_patterns:
            nm = re.search(np, html, re.I | re.S)
            if nm:
                raw_name = nm.group(1).strip()
                cleaned = re.sub(r"<[^>]+>", "", raw_name).strip()
                if cleaned and cleaned != "MediaFire" and not cleaned.lower().startswith("download "):
                    name = cleaned
                    break

        if not name:
            parsed = urllib.parse.urlparse(direct_url)
            name = urllib.parse.unquote(os.path.basename(parsed.path))
        if not name:
            name = "mediafire_file"

        name = re.sub(r'[\\/*?:"<>|]', "_", name).strip()

        # 7. Extract File Size
        size = 0
        try:
            head_headers = self.session.headers.copy()
            head_headers["Referer"] = "https://www.mediafire.com/"
            head_resp = self.session.head(direct_url, headers=head_headers, allow_redirects=True, timeout=10)
            if "content-length" in head_resp.headers:
                size = int(head_resp.headers["content-length"])
        except Exception:
            pass

        if not size:
            size_match = re.search(r'\((\d+(?:\.\d+)?\s*(?:B|KB|MB|GB))\)', html, re.I)
            if size_match:
                size = self._parse_size(size_match.group(1))

        return {
            "name": name,
            "size": size,
            "direct_url": direct_url,
        }

    def _parse_content_disposition(self, header: str) -> Optional[str]:
        if not header:
            return None
        m_utf = re.search(r"filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)", header, re.I)
        if m_utf:
            return urllib.parse.unquote(m_utf.group(1).strip("\"' "))
        m = re.search(r'filename\s*=\s*"([^"]+)"', header, re.I)
        if m:
            return m.group(1).strip()
        m_bare = re.search(r'filename\s*=\s*([^\s;]+)', header, re.I)
        if m_bare:
            return m_bare.group(1).strip("\"' ")
        return None

    def _parse_size(self, size_str: str) -> int:
        """Convert '12.5 MB' into bytes."""
        m = re.match(r"(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)", size_str.strip(), re.I)
        if not m:
            return 0
        num = float(m.group(1))
        unit = m.group(2).upper()
        multipliers = {"B": 1, "KB": 1024, "MB": 1024 * 1024, "GB": 1024 * 1024 * 1024}
        return int(num * multipliers.get(unit, 1))

    def download(self, url: str, dest_dir: str,
                 progress_cb: Optional[Callable[[int, int], None]] = None) -> str:
        """Download file or folder from MediaFire into dest_dir."""
        os.makedirs(dest_dir, exist_ok=True)

        if "/folder/" in url:
            return self._download_folder(url, dest_dir, progress_cb)
        return self._download_file(url, dest_dir, progress_cb)

    def _download_file(self, url: str, dest_dir: str, progress_cb=None) -> str:
        info = self._resolve_file_info(url)
        direct_url = info["direct_url"]
        filename = info["name"]
        total_size = info["size"]

        target_path = os.path.join(dest_dir, filename)

        req_headers = self.session.headers.copy()
        req_headers["Referer"] = "https://www.mediafire.com/"
        req_headers["Accept-Encoding"] = "identity"

        with self.session.get(direct_url, headers=req_headers, stream=True, timeout=60, allow_redirects=True) as r:
            r.raise_for_status()
            if not total_size and "content-length" in r.headers:
                try:
                    total_size = int(r.headers["content-length"])
                except Exception:
                    pass

            done = 0
            chunk_size = 256 * 1024  # 256 KB
            with open(target_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        done += len(chunk)
                        if progress_cb and total_size:
                            progress_cb(done, total_size)

        log.info("MediaFire download complete: %s (%d bytes)", target_path, done)
        return target_path

    def _download_folder(self, url: str, dest_dir: str, progress_cb=None) -> str:
        folder_info = self._probe_folder(url)
        files = folder_info.get("files", [])
        if not files:
            raise RuntimeError("No downloadable files found in MediaFire folder.")

        folder_name = folder_info.get("name", "MediaFire_Folder")
        folder_path = os.path.join(dest_dir, re.sub(r'[\\/*?:"<>|]', "_", folder_name))
        os.makedirs(folder_path, exist_ok=True)

        for file_url in files:
            try:
                self._download_file(file_url, folder_path, progress_cb)
            except Exception as e:
                log.warning("Failed to download %s from MediaFire folder: %s", file_url, e)

        return folder_path
