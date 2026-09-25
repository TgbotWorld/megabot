# Unified Downloader Factory and Link Dispatcher
from typing import Optional

from megabot.downloaders.base import BaseDownloader
from megabot.downloaders.mega import (
    MegaDownloader,
    extract_mega_links,
    is_mega_direct_link,
    link_key as mega_link_key,
)
from megabot.downloaders.mediafire import (
    MediaFireDownloader,
    extract_mediafire_links,
    is_mediafire_link,
    mediafire_link_key,
)
from megabot.downloaders.mp4upload import (
    MP4UploadDownloader,
    extract_mp4upload_links,
    is_mp4upload_link,
    mp4upload_link_key,
)
from megabot.downloaders.terabox import (
    TeraBoxDownloader,
    extract_terabox_links,
    is_terabox_link,
    terabox_link_key,
)
from megabot.downloaders.direct import (
    DirectDownloader,
    extract_direct_links,
    is_direct_link,
    direct_link_key,
)


def extract_supported_links(text: str) -> list[str]:
    """
    Extract all supported download links (MEGA, MediaFire, MP4Upload, TeraBox) from text.
    Preserves order and deduplicates.
    """
    if not text:
        return []

    mega_links = extract_mega_links(text)
    mf_links = extract_mediafire_links(text)
    mp4u_links = extract_mp4upload_links(text)
    tb_links = extract_terabox_links(text)

    # Combine and deduplicate
    combined = []
    seen = set()
    for link in mega_links + mf_links + mp4u_links + tb_links:
        if link not in seen:
            seen.add(link)
            combined.append(link)

    return combined


def get_link_key(url: str) -> str:
    """Return a unique, stable cache identifier for any supported link."""
    if is_terabox_link(url):
        return terabox_link_key(url)
    if is_mp4upload_link(url):
        return mp4upload_link_key(url)
    if is_mediafire_link(url):
        return mediafire_link_key(url)
    if is_direct_link(url):
        return direct_link_key(url)
    return mega_link_key(url)


def is_supported_link(url: str) -> bool:
    """Check if a URL is handled by any supported downloader."""
    if not url:
        return False
    return (
        is_terabox_link(url)
        or is_mp4upload_link(url)
        or is_mediafire_link(url)
        or "mega." in url.lower()
        or is_mega_direct_link(url)
    )


async def get_downloader(url: str, user_id: Optional[int] = None) -> BaseDownloader:
    """
    Instantiate and return the appropriate downloader instance for the URL.
    """
    if is_terabox_link(url):
        from config import TERABOX_COOKIE
        from megabot.core.database import db

        cookie = None
        if user_id:
            cookie = await db.get_user_setting(user_id, "terabox_cookie")
        if not cookie:
            cookie = await db.get_config("terabox_cookie")
        if not cookie:
            cookie = TERABOX_COOKIE

        return TeraBoxDownloader(cookie=cookie)

    if is_mp4upload_link(url):
        return MP4UploadDownloader()

    if is_mediafire_link(url):
        return MediaFireDownloader()

    if is_direct_link(url):
        return DirectDownloader()

    # Default to MEGA with user session credentials
    from megabot.core.database import db
    account = await db.get_mega_account(user_id) if user_id else None
    session = await db.get_mega_session(user_id) if user_id else None

    return MegaDownloader(
        email=account["email"] if account else None,
        password=account["password"] if account else None,
        saved_session=session,
    )
