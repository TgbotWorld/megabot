# Unit tests for DirectDownloader
import unittest
from unittest.mock import patch, MagicMock

from megabot.downloaders.direct import (
    DirectDownloader,
    is_direct_link,
    extract_direct_links,
    direct_link_key,
)


class TestDirectDownloader(unittest.TestCase):
    def test_is_direct_link(self):
        self.assertTrue(is_direct_link("https://example.com/file.zip"))
        self.assertTrue(is_direct_link("http://cdn.mysite.org/video.mp4?token=123"))
        # Must return False for specialized services
        self.assertFalse(is_direct_link("https://mega.nz/file/ABC#123"))
        self.assertFalse(is_direct_link("https://mediafire.com/file/123"))
        self.assertFalse(is_direct_link("https://www.mp4upload.com/abc"))
        self.assertFalse(is_direct_link("https://terabox.app/s/123"))

    def test_extract_direct_links(self):
        text = "Download here: https://files.com/doc.pdf and skip https://mega.nz/file/xyz#123"
        links = extract_direct_links(text)
        self.assertEqual(links, ["https://files.com/doc.pdf"])

    def test_direct_link_key(self):
        url = "https://files.com/test.zip"
        key = direct_link_key(url)
        self.assertTrue(key.startswith("dir_"))

    @patch("requests.Session.head")
    def test_direct_downloader_probe(self, mock_head):
        resp = MagicMock()
        resp.status_code = 200
        resp.headers = {
            "content-disposition": 'attachment; filename="custom_archive.zip"',
            "content-length": "10485760",
        }
        mock_head.return_value = resp

        dl = DirectDownloader()
        info = dl.probe("https://example.com/download?id=99")
        self.assertEqual(info["name"], "custom_archive.zip")
        self.assertEqual(info["size"], 10485760)
        self.assertEqual(info["kind"], "file")
