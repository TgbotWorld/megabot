# Unit tests for MEGA link extraction, parsing, and probing
import unittest
from unittest.mock import patch, MagicMock

from megabot.downloaders import is_supported_link
from megabot.downloaders.mega import extract_mega_links, link_key, MegaDownloader, is_mega_direct_link
from megabot.downloaders.mega_raw import RawMega


class TestMegaDownload(unittest.TestCase):
    def test_extract_mega_links_both_versions(self):
        text = """
        Here are multiple mega links:
        1. V2 file: https://mega.nz/file/TESTHANDLE#TESTKEY12345
        2. V2 folder: https://mega.nz/folder/TESTFOLDER#FOLDERKEY123
        3. V1 file: https://mega.nz/#!LEGACYHANDLE!LEGACYKEY123
        4. V1 folder: https://mega.nz/#F!LEGACYFOLDER!LEGFOLDERKEY
        """
        links = extract_mega_links(text)
        self.assertEqual(len(links), 4)
        self.assertIn("https://mega.nz/file/TESTHANDLE#TESTKEY12345", links)
        self.assertIn("https://mega.nz/folder/TESTFOLDER#FOLDERKEY123", links)
        self.assertIn("https://mega.nz/#!LEGACYHANDLE!LEGACYKEY123", links)
        self.assertIn("https://mega.nz/#F!LEGACYFOLDER!LEGFOLDERKEY", links)

    def test_link_key_generation(self):
        v2_file = "https://mega.nz/file/ABC123#KEY456"
        v2_folder = "https://mega.nz/folder/FOL123#KEY456"
        v1_file = "https://mega.nz/#!LEG123!KEY456"
        v1_folder = "https://mega.nz/#F!LEGFOL123!KEY456"

        self.assertEqual(link_key(v2_file), "ABC123")
        self.assertEqual(link_key(v2_folder), "FOL123")
        self.assertEqual(link_key(v1_file), "LEG123")
        self.assertEqual(link_key(v1_folder), "LEGFOL123")

    def test_raw_mega_parse_urls(self):
        parsed_v2_file = RawMega.parse_file_url("https://mega.nz/file/ABC123#KEY456")
        self.assertEqual(parsed_v2_file, ("ABC123", "KEY456"))

        parsed_v1_file = RawMega.parse_file_url("https://mega.nz/#!ABC123!KEY456")
        self.assertEqual(parsed_v1_file, ("ABC123", "KEY456"))

        parsed_v2_folder = RawMega.parse_folder_url("https://mega.nz/folder/FOL123#KEY456")
        self.assertEqual(parsed_v2_folder, ("FOL123", "KEY456"))

        parsed_v1_folder = RawMega.parse_folder_url("https://mega.nz/#F!FOL123!KEY456")
        self.assertEqual(parsed_v1_folder, ("FOL123", "KEY456"))

    @patch("megabot.downloaders.mega_raw.RawMega._api")
    def test_probe_file_without_broken_mega_py_parser(self, mock_api):
        mock_api.return_value = {
            "s": 1048576,
            "at": "test_attribute_data",
        }
        raw = RawMega()
        # RawMega probe_file parses the URL handle directly and queries MEGA raw API
        info = raw.probe_file("https://mega.nz/file/FILEHANDLE#MDEyMzQ1Njc4OTAxMjM0NQ==")
        self.assertEqual(info["size"], 1048576)
        self.assertEqual(info["kind"], "file")
        self.assertTrue(info["name"])

    def test_mega_direct_link_detection(self):
        direct_url = "https://gfs302n212.usercontent.mega.co.nz/0/dl/abc12345/archive.zip"
        self.assertTrue(is_mega_direct_link(direct_url))
        self.assertTrue(is_supported_link(direct_url))
        links = extract_mega_links(f"Download: {direct_url}")
        self.assertIn(direct_url, links)

    @patch("megabot.downloaders.direct.DirectDownloader.probe")
    @patch("megabot.downloaders.direct.DirectDownloader.download")
    def test_mega_direct_link_probe_and_download(self, mock_download, mock_probe):
        mock_probe.return_value = {
            "name": "archive.zip",
            "size": 5242880,
            "kind": "file",
            "direct_url": "https://gfs302n212.usercontent.mega.co.nz/0/dl/abc12345/archive.zip",
        }
        mock_download.return_value = "/tmp/archive.zip"

        dl = MegaDownloader()
        direct_url = "https://gfs302n212.usercontent.mega.co.nz/0/dl/abc12345/archive.zip"

        info = dl.probe(direct_url)
        self.assertEqual(info["name"], "archive.zip")
        self.assertEqual(info["size"], 5242880)
        mock_probe.assert_called_once_with(direct_url)

        res = dl.download(direct_url, "/tmp")
        self.assertEqual(res, "/tmp/archive.zip")
        mock_download.assert_called_once_with(direct_url, "/tmp", None)
