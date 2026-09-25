# Unit tests for AI Agent Tools
import asyncio
import os
import shutil
import tempfile
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from megabot.ai.tools import execute_tool, TOOL_DEFINITIONS


class TestAITools(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.context = {
            "user_id": 99999,
            "is_owner": True,
            "client": MagicMock(),
            "chat_id": 123456,
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_tool_definitions_valid(self):
        """Tool definitions must have name, description, and parameters."""
        self.assertTrue(len(TOOL_DEFINITIONS) >= 12)
        tool_names = {t["name"] for t in TOOL_DEFINITIONS}
        expected = {
            "start_download",
            "list_jobs",
            "get_job_details",
            "list_job_files",
            "unzip_files",
            "delete_job_files",
            "cancel_job",
            "clean_disk",
            "get_system_stats",
            "get_user_settings",
            "update_user_setting",
            "clear_cache",
            "get_account_info",
            "logout_mega_account",
        }
        self.assertTrue(expected.issubset(tool_names))
        for t in TOOL_DEFINITIONS:
            self.assertIn("name", t)
            self.assertIn("description", t)
            self.assertIn("parameters", t)

    @patch("megabot.core.database.db.count_jobs", new_callable=AsyncMock)
    def test_get_system_stats(self, mock_count):
        """get_system_stats returns disk, queue, and jobs info."""
        mock_count.return_value = 0
        res = asyncio.run(execute_tool("get_system_stats", {}, self.context))
        self.assertEqual(res.get("status"), "success")
        self.assertIn("disk", res)
        self.assertIn("queue", res)

    def test_clean_disk_executes_safely(self):
        """clean_disk runs without error and returns summary."""
        res = asyncio.run(execute_tool("clean_disk", {}, self.context))
        self.assertEqual(res.get("status"), "success")
        self.assertIn("cleaned_folders", res)
        self.assertIn("freed_mb", res)

    def test_unknown_tool_handled_gracefully(self):
        """Unknown tool name returns error dictionary instead of raising."""
        res = asyncio.run(execute_tool("non_existent_tool", {}, self.context))
        self.assertEqual(res.get("status"), "error")
        self.assertIn("Unknown tool", res.get("message", ""))

    def test_start_download_validation(self):
        """start_download rejects empty or non-supported links."""
        res_empty = asyncio.run(execute_tool("start_download", {}, self.context))
        self.assertEqual(res_empty.get("status"), "error")

        res_invalid = asyncio.run(
            execute_tool("start_download", {"urls": "https://example.com/file.zip"}, self.context)
        )
        self.assertEqual(res_invalid.get("status"), "error")

    @patch("megabot.core.database.db.cache_link", new_callable=AsyncMock)
    @patch("megabot.core.job_queue.job_queue.submit", new_callable=AsyncMock)
    @patch("megabot.core.database.db.create_job", new_callable=AsyncMock)
    @patch("megabot.core.database.db.get_cached_link", new_callable=AsyncMock)
    @patch("megabot.core.database.db.active_jobs_for_user", new_callable=AsyncMock)
    def test_start_download_success(self, mock_active, mock_cache, mock_create_job, mock_submit, mock_cache_link):
        """start_download accepts valid MediaFire or MEGA URLs and queues a job."""
        mock_active.return_value = 0
        mock_cache.return_value = None
        mock_create_job.return_value = {"_id": "testjob123"}
        self.context["client"].send_message = AsyncMock(return_value=MagicMock(id=999))

        res = asyncio.run(
            execute_tool(
                "start_download",
                {
                    "urls": "https://www.mediafire.com/file/abc123xyz/sample.zip/file",
                    "instruction": "unzip and keep only documents"
                },
                self.context,
            )
        )
        self.assertEqual(res.get("status"), "success")
        self.assertIn("job_id", res)
        self.assertEqual(len(res.get("urls")), 1)
        self.assertEqual(res.get("instruction"), "unzip and keep only documents")

    @patch("megabot.core.database.db.get_job", new_callable=AsyncMock)
    def test_list_job_files(self, mock_get_job):
        """list_job_files inspects job workspace safely."""
        mock_get_job.return_value = {"_id": "test_inspect_job", "user_id": 99999}
        with patch("megabot.ai.tools.DOWNLOAD_DIR", self.temp_dir):
            job_id = "test_inspect_job"
            job_folder = os.path.join(self.temp_dir, job_id)
            os.makedirs(job_folder, exist_ok=True)
            with open(os.path.join(job_folder, "test.txt"), "w") as f:
                f.write("test content")

            res = asyncio.run(execute_tool("list_job_files", {"job_id": job_id}, self.context))
            self.assertEqual(res.get("status"), "success")
            self.assertEqual(res.get("file_count"), 1)
            self.assertEqual(res["files"][0]["filename"], "test.txt")

    def test_detect_user_intents(self):
        from megabot.plugins.agent import detect_user_intents
        intents1 = detect_user_intents("stop background jobs and free up your desk")
        self.assertTrue(any(t == "cancel_job" for t, _ in intents1))
        self.assertTrue(any(t == "clean_disk" for t, _ in intents1))

        intents2 = detect_user_intents("please extract files from archives directly")
        self.assertTrue(any(t == "unzip_files" for t, _ in intents2))

    def test_is_ai_refusal(self):
        from megabot.plugins.agent import is_ai_refusal
        refusal_sample = (
            "I can't stop background jobs or free up your desk - I'm a text-based AI assistant "
            "without system-level controls. I don't have access to your computer's processes or physical environment. "
            "and m an AI assistant without access to your device’s filesystem or the ability to extract files from archives directly."
        )
        self.assertTrue(is_ai_refusal(refusal_sample))
        self.assertFalse(is_ai_refusal("✅ I have cleaned the disk and unzipped your archives."))


    @patch("megabot.core.job_queue.job_queue.submit", new_callable=AsyncMock)
    @patch("megabot.core.database.db.create_job", new_callable=AsyncMock)
    @patch("megabot.core.database.db.set_user_setting", new_callable=AsyncMock)
    def test_unzip_files_with_replied_media(self, mock_setting, mock_create, mock_submit):
        """unzip_files extracts media from replied-to message."""
        mock_create.return_value = {"_id": "job_unzip_reply"}
        self.context["client"].send_message = AsyncMock(return_value=MagicMock(id=888))

        msg = MagicMock()
        replied = MagicMock()
        replied.id = 456
        replied.video = None
        replied.audio = None
        replied.photo = None
        doc = MagicMock()
        doc.file_name = "archive.zip"
        doc.file_size = 5000
        replied.document = doc
        msg.reply_to_message = replied
        self.context["message"] = msg

        res = asyncio.run(execute_tool("unzip_files", {}, self.context))
        self.assertEqual(res.get("status"), "success")
        self.assertIn("job_id", res)
        self.assertIn("archive.zip", res.get("message"))
        mock_submit.assert_called_once()
        mock_setting.assert_called_once_with(99999, "archive_mode", "extract")

    @patch("megabot.core.job_queue.job_queue.submit", new_callable=AsyncMock)
    @patch("megabot.core.database.db.create_job", new_callable=AsyncMock)
    @patch("megabot.core.database.db.list_jobs", new_callable=AsyncMock)
    @patch("megabot.core.database.db.set_user_setting", new_callable=AsyncMock)
    def test_unzip_files_with_recent_telegram_upload(self, mock_setting, mock_list, mock_create, mock_submit):
        """unzip_files re-queues recent telegram media upload if no reply message."""
        mock_list.return_value = [{
            "_id": "past_job_1",
            "url": "tg://media/333",
            "media_message_id": 333,
            "media_file_name": "past_upload.zip",
            "media_file_size": 4096,
            "is_telegram_media": True,
        }]
        mock_create.return_value = {"_id": "job_unzip_recent"}
        self.context["client"].send_message = AsyncMock(return_value=MagicMock(id=999))
        self.context["message"] = MagicMock(reply_to_message=None)

        res = asyncio.run(execute_tool("unzip_files", {}, self.context))
        self.assertEqual(res.get("status"), "success")
        self.assertIn("job_id", res)
        self.assertIn("past_upload.zip", res.get("message"))
        mock_submit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
