import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock

from megabot.ai.executor import _list_all_files, execute_plan
from megabot.analyzers.classify import classify
from megabot.processors.archives import safe_extract, safe_zip
from megabot.processors.uploader import UploadProgress


class TestUploaderAndExtraction(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.job_dir = os.path.join(self.temp_dir, "job_test")
        os.makedirs(self.job_dir, exist_ok=True)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_upload_progress_rejects_missing_file(self):
        app_mock = MagicMock()
        job = {"_id": "test1", "chat_id": 123, "message_id": 456}
        uploader = UploadProgress(app_mock, job, ["missing.mp4"])
        loop = asyncio.new_event_loop()
        try:
            ok = loop.run_until_complete(uploader.send(os.path.join(self.job_dir, "missing.mp4")))
            self.assertFalse(ok)
            self.assertIn("File not found", uploader.last_error)
        finally:
            loop.close()

    def test_upload_progress_rejects_zero_byte_file(self):
        zero_file = os.path.join(self.job_dir, "empty.mp4")
        with open(zero_file, "wb") as f:
            pass
        self.assertEqual(os.path.getsize(zero_file), 0)

        app_mock = MagicMock()
        job = {"_id": "test2", "chat_id": 123, "message_id": 456}
        uploader = UploadProgress(app_mock, job, [zero_file])
        loop = asyncio.new_event_loop()
        try:
            ok = loop.run_until_complete(uploader.send(zero_file))
            self.assertFalse(ok)
            self.assertIn("File is empty (0 bytes)", uploader.last_error)
        finally:
            loop.close()

    def test_upload_progress_records_exception(self):
        non_empty = os.path.join(self.job_dir, "data.pdf")
        with open(non_empty, "wb") as f:
            f.write(b"%PDF-1.4 test")

        app_mock = MagicMock()
        app_mock.send_document = AsyncMock(side_effect=RuntimeError("MTProto connection closed"))
        app_mock.edit_message_text = AsyncMock()

        job = {"_id": "test3", "chat_id": 123, "message_id": 456}
        uploader = UploadProgress(app_mock, job, [non_empty])
        loop = asyncio.new_event_loop()
        try:
            ok = loop.run_until_complete(uploader.send(non_empty))
            self.assertFalse(ok)
            self.assertIn("RuntimeError", uploader.last_error)
            self.assertIn("MTProto connection closed", uploader.last_error)
        finally:
            loop.close()

    def test_classify_ignores_zero_byte_files(self):
        # Create a 0-byte video stub
        stub = os.path.join(self.job_dir, "corrupted.mp4")
        with open(stub, "wb") as f:
            pass

        # Create a valid text file
        valid = os.path.join(self.job_dir, "readme.txt")
        with open(valid, "w") as f:
            f.write("Valid content")

        analysis = classify(self.job_dir)
        # Should NOT classify as video_set because corrupted.mp4 has 0 bytes
        self.assertEqual(analysis["videos"], [])
        self.assertIn(valid, analysis["others"])

    def test_list_all_files_ignores_zero_byte_files(self):
        stub = os.path.join(self.job_dir, "empty.mkv")
        with open(stub, "wb") as f:
            pass

        valid = os.path.join(self.job_dir, "video.mkv")
        with open(valid, "wb") as f:
            f.write(b"content")

        files = _list_all_files(self.job_dir)
        self.assertIn(valid, files)
        self.assertNotIn(stub, files)

    def test_execute_plan_preserves_archive_when_extraction_yields_no_files(self):
        # Create dummy archive that is actually not a valid archive
        fake_archive = os.path.join(self.job_dir, "broken.zip")
        with open(fake_archive, "wb") as f:
            f.write(b"not a real zip file but non empty")

        plan = {
            "summary": "Try extracting broken archive",
            "actions": [
                {"action": "extract_archive", "file": "broken.zip"}
            ]
        }
        app_mock = MagicMock()
        edit_mock = AsyncMock()
        job = {"_id": "test_job_broken"}

        loop = asyncio.new_event_loop()
        try:
            results = loop.run_until_complete(
                execute_plan(app_mock, job, self.job_dir, plan, edit_mock)
            )
            # The archive should be preserved and returned as the file to send!
            self.assertTrue(os.path.exists(fake_archive))
            self.assertIn(fake_archive, results)
        finally:
            loop.close()

    def test_safe_extract_handles_windows_slashes_and_leading_slashes(self):
        import zipfile
        zip_path = os.path.join(self.job_dir, "test_slashes.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("/leading_slash.txt", "content leading")
            zf.writestr(r"windows\nested\deep.txt", "content windows")
            zf.writestr("normal.txt", "content normal")

        out_dir = os.path.join(self.job_dir, "extracted")
        safe_extract(zip_path, out_dir)

        self.assertTrue(os.path.isfile(os.path.join(out_dir, "leading_slash.txt")))
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "windows", "nested", "deep.txt")))
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "normal.txt")))

    def test_detect_archive_type_and_is_archive_file_by_magic_bytes(self):
        import zipfile
        from megabot.processors.archives import detect_archive_type
        from megabot.analyzers.classify import is_archive_file

        no_ext_path = os.path.join(self.job_dir, "unnamed_archive")
        with zipfile.ZipFile(no_ext_path, "w") as zf:
            zf.writestr("inner.txt", "magic byte test")

        self.assertEqual(detect_archive_type(no_ext_path), ".zip")
        self.assertTrue(is_archive_file(no_ext_path))

        out_dir = os.path.join(self.job_dir, "magic_extracted")
        safe_extract(no_ext_path, out_dir)
        self.assertTrue(os.path.isfile(os.path.join(out_dir, "inner.txt")))

    def test_safe_extract_blocks_zip_slip(self):
        import zipfile
        from megabot.processors.archives import UnsafeArchiveError

        slip_zip = os.path.join(self.job_dir, "zip_slip.zip")
        with zipfile.ZipFile(slip_zip, "w") as zf:
            zf.writestr("../../../../../etc/passwd", "malicious payload")

        out_dir = os.path.join(self.job_dir, "slip_extracted")
        with self.assertRaises(UnsafeArchiveError):
            safe_extract(slip_zip, out_dir)


if __name__ == "__main__":
    unittest.main()
