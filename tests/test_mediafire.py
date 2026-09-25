# Unit tests for MediaFire downloader and link extraction
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from megabot.downloaders import (
    extract_supported_links,
    get_downloader,
    get_link_key,
    is_supported_link,
)
from megabot.downloaders.mediafire import (
    MediaFireDownloader,
    extract_mediafire_links,
    is_mediafire_link,
    mediafire_link_key,
)


class TestMediaFireDownloader(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_extract_mediafire_links(self):
        text = (
            "Check these files: "
            "https://www.mediafire.com/file/abc123xyz/sample_archive.zip/file and "
            "http://mediafire.com/download/def456/another.rar "
            "and folder https://www.mediafire.com/folder/fold789/my_collection "
            "and duplicate https://www.mediafire.com/file/abc123xyz/sample_archive.zip/file"
        )
        links = extract_mediafire_links(text)
        self.assertEqual(len(links), 3)
        self.assertIn("https://www.mediafire.com/file/abc123xyz/sample_archive.zip/file", links)
        self.assertIn("http://mediafire.com/download/def456/another.rar", links)
        self.assertIn("https://www.mediafire.com/folder/fold789/my_collection", links)

    def test_extract_supported_links_mixed(self):
        text = (
            "MEGA link: https://mega.nz/file/vHQgXZbA#8U8273hjsd82 \n"
            "MediaFire: https://www.mediafire.com/file/abc123xyz/sample.pdf/file"
        )
        links = extract_supported_links(text)
        self.assertEqual(len(links), 2)
        self.assertTrue(any("mega.nz" in l for l in links))
        self.assertTrue(any("mediafire.com" in l for l in links))

    def test_is_mediafire_link(self):
        self.assertTrue(is_mediafire_link("https://www.mediafire.com/file/123/test.zip"))
        self.assertTrue(is_mediafire_link("http://mediafire.com/download/abc/test.pdf"))
        self.assertTrue(is_mediafire_link("https://mediafire.com/folder/xyz/docs"))
        self.assertFalse(is_mediafire_link("https://mega.nz/file/123#abc"))
        self.assertFalse(is_mediafire_link("https://google.com"))

    def test_link_key(self):
        mf_url = "https://www.mediafire.com/file/abc123xyz/test_file.zip/file"
        self.assertEqual(mediafire_link_key(mf_url), "mf_abc123xyz")
        self.assertEqual(get_link_key(mf_url), "mf_abc123xyz")

    def test_is_supported_link(self):
        self.assertTrue(is_supported_link("https://www.mediafire.com/file/123/file.zip"))
        self.assertTrue(is_supported_link("https://mega.nz/file/123#abc"))
        self.assertFalse(is_supported_link("https://example.com/file.zip"))

    @patch("requests.Session.get")
    @patch("requests.Session.head")
    def test_probe_file(self, mock_head, mock_get):
        downloader = MediaFireDownloader()

        sample_html = """
        <html>
            <head><title>Test Archive.zip</title></head>
            <body>
                <div class="filename">Test_Archive.zip</div>
                <a id="downloadButton" href="https://download123.mediafire.com/fakekey/Test_Archive.zip">Download</a>
            </body>
        </html>
        """
        mock_resp_get = MagicMock()
        mock_resp_get.status_code = 200
        mock_resp_get.text = sample_html
        mock_get.return_value = mock_resp_get

        mock_resp_head = MagicMock()
        mock_resp_head.headers = {"content-length": "1048576"}
        mock_head.return_value = mock_resp_head

        info = downloader.probe("https://www.mediafire.com/file/fakekey/Test_Archive.zip")
        self.assertEqual(info["name"], "Test_Archive.zip")
        self.assertEqual(info["size"], 1048576)
        self.assertEqual(info["kind"], "file")
        self.assertEqual(info["direct_url"], "https://download123.mediafire.com/fakekey/Test_Archive.zip")

    @patch("requests.Session.get")
    @patch("requests.Session.head")
    def test_download_file(self, mock_head, mock_get):
        downloader = MediaFireDownloader()

        sample_html = """
        <html>
            <body>
                <div class="filename">document.pdf</div>
                <a id="downloadButton" href="https://download456.mediafire.com/key/document.pdf">Download</a>
            </body>
        </html>
        """
        page_resp = MagicMock()
        page_resp.status_code = 200
        page_resp.text = sample_html

        content_data = b"%PDF-1.4 test mediafire stream download data"
        stream_resp = MagicMock()
        stream_resp.status_code = 200
        stream_resp.headers = {"content-length": str(len(content_data))}
        stream_resp.__enter__.return_value = stream_resp
        stream_resp.__exit__.return_value = False
        stream_resp.iter_content.return_value = [content_data]

        # First call is HTML page get, second call is stream get
        mock_get.side_effect = [page_resp, stream_resp]

        mock_resp_head = MagicMock()
        mock_resp_head.headers = {"content-length": str(len(content_data))}
        mock_head.return_value = mock_resp_head

        progress_calls = []
        def progress_cb(done, total):
            progress_calls.append((done, total))

        target_file = downloader.download(
            "https://www.mediafire.com/file/key/document.pdf",
            self.temp_dir,
            progress_cb=progress_cb,
        )

        self.assertTrue(os.path.exists(target_file))
        self.assertEqual(os.path.basename(target_file), "document.pdf")
        with open(target_file, "rb") as f:
            self.assertEqual(f.read(), content_data)
        self.assertTrue(len(progress_calls) > 0)

    def test_direct_mediafire_link_detection(self):
        direct_url = "https://download1500.mediafire.com/xyz123/sample_archive.zip"
        self.assertTrue(is_mediafire_link(direct_url))
        self.assertTrue(is_supported_link(direct_url))
        extracted = extract_mediafire_links(f"Download here: {direct_url}")
        self.assertIn(direct_url, extracted)

    @patch("requests.Session.head")
    def test_probe_direct_mediafire_file(self, mock_head):
        downloader = MediaFireDownloader()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "content-disposition": 'attachment; filename="archive.zip"',
            "content-length": "2097152",
        }
        mock_head.return_value = mock_resp

        direct_url = "https://download1234.mediafire.com/token/archive.zip"
        info = downloader.probe(direct_url)
        self.assertEqual(info["name"], "archive.zip")
        self.assertEqual(info["size"], 2097152)
        self.assertEqual(info["kind"], "file")
        self.assertEqual(info["direct_url"], direct_url)

    @patch("requests.Session.head")
    @patch("requests.Session.get")
    def test_download_direct_mediafire_file(self, mock_get, mock_head):
        downloader = MediaFireDownloader()
        mock_resp_head = MagicMock()
        mock_resp_head.status_code = 200
        mock_resp_head.headers = {
            "content-disposition": 'attachment; filename="direct_file.bin"',
            "content-length": "10",
        }
        mock_head.return_value = mock_resp_head

        content_data = b"0123456789"
        stream_resp = MagicMock()
        stream_resp.status_code = 200
        stream_resp.headers = {"content-length": "10"}
        stream_resp.__enter__.return_value = stream_resp
        stream_resp.__exit__.return_value = False
        stream_resp.iter_content.return_value = [content_data]
        mock_get.return_value = stream_resp

        direct_url = "https://download999.mediafire.com/tok/direct_file.bin"
        out = downloader.download(direct_url, self.temp_dir)
        self.assertTrue(os.path.exists(out))
        self.assertEqual(os.path.basename(out), "direct_file.bin")
        with open(out, "rb") as f:
            self.assertEqual(f.read(), content_data)


if __name__ == "__main__":
    unittest.main()
