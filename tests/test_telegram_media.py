# Unit tests for Telegram direct media upload and pipeline processing
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

from megabot.plugins.agent import on_user_media
from megabot.core.pipeline import run_job


class TestTelegramMedia(unittest.IsolatedAsyncioTestCase):
    @patch("megabot.core.job_queue.job_queue.submit", new_callable=AsyncMock)
    @patch("megabot.core.database.db.create_job", new_callable=AsyncMock)
    @patch("megabot.core.database.db.active_jobs_for_user", new_callable=AsyncMock)
    @patch("megabot.core.database.db.is_user_banned", new_callable=AsyncMock)
    @patch("megabot.core.database.db.add_user", new_callable=AsyncMock)
    async def test_on_user_media_document_queuing(self, mock_add_user, mock_banned, mock_active, mock_create, mock_submit):
        mock_banned.return_value = False
        mock_active.return_value = 0
        mock_create.return_value = {"_id": "job_tg_123"}

        client = MagicMock()
        msg = MagicMock()
        msg.id = 555
        msg.from_user.id = 123456
        msg.from_user.username = "testuser"
        msg.from_user.is_bot = False
        msg.chat.id = 123456
        msg.caption = "unzip and extract"
        msg.video = None
        msg.audio = None
        msg.photo = None

        doc = MagicMock()
        doc.file_name = "archive.zip"
        doc.file_size = 10485760  # 10 MB
        msg.document = doc

        status_msg = MagicMock()
        status_msg.id = 999
        msg.reply_text = AsyncMock(return_value=status_msg)

        await on_user_media(client, msg)

        mock_create.assert_called_once()
        call_args = mock_create.call_args[1]
        self.assertEqual(call_args["user_id"], 123456)
        self.assertEqual(call_args["url"], "tg://media/555")
        self.assertEqual(call_args["prompt"], "unzip and extract")
        mock_submit.assert_called_once()

    @patch("megabot.core.pipeline._upload_files", new_callable=AsyncMock)
    @patch("megabot.core.pipeline._edit_status", new_callable=AsyncMock)
    @patch("megabot.core.database.db.set_job_status", new_callable=AsyncMock)
    async def test_run_job_with_telegram_media(self, mock_status, mock_edit, mock_upload):
        app = MagicMock()
        app.get_messages = AsyncMock()

        def fake_download(msg, file_name, progress):
            os.makedirs(os.path.dirname(file_name), exist_ok=True)
            with open(file_name, "w") as f:
                f.write("test content")
            return file_name

        app.download_media = AsyncMock(side_effect=fake_download)

        job = {
            "_id": "tg_job_001",
            "user_id": 123456,
            "chat_id": 123456,
            "message_id": 999,
            "url": "tg://media/555",
            "media_message_id": 555,
            "media_file_name": "document.zip",
            "media_file_size": 2048,
            "is_telegram_media": True,
            "prompt": "extract",
        }

        with patch("megabot.analyzers.classify.classify", return_value={"kind": "single", "name": "document.zip", "archives": [], "videos": [], "images": [], "others": ["fake_doc.zip"]}):
            with patch("megabot.ai.client.get_ai_config", return_value={"is_configured": False, "api_key": ""}):
                await run_job(app, job)

        app.download_media.assert_called_once()
        mock_upload.assert_called_once()

    @patch("megabot.core.pipeline._upload_files", new_callable=AsyncMock)
    @patch("megabot.core.pipeline._edit_status", new_callable=AsyncMock)
    @patch("megabot.core.database.db.set_job_status", new_callable=AsyncMock)
    @patch("megabot.core.database.db.get_user_setting", new_callable=AsyncMock)
    async def test_run_job_auto_extracts_telegram_zip_upload(self, mock_setting, mock_status, mock_edit, mock_upload):
        import zipfile
        mock_setting.return_value = "extract"  # default user setting

        app = MagicMock()
        msg_obj = MagicMock()
        msg_obj.id = 777

        def fake_download_zip(msg, file_name, progress):
            os.makedirs(os.path.dirname(file_name), exist_ok=True)
            with zipfile.ZipFile(file_name, "w") as zf:
                zf.writestr("extracted_file1.txt", "content file 1")
                zf.writestr("extracted_file2.txt", "content file 2")
            return file_name

        app.download_media = AsyncMock(side_effect=fake_download_zip)

        job = {
            "_id": "tg_job_auto_extract",
            "user_id": 123456,
            "chat_id": 123456,
            "message_id": 999,
            "url": "tg://media/777",
            "media_message_id": 777,
            "media_file_name": "my_archive.zip",
            "media_file_size": 2048,
            "is_telegram_media": True,
            "prompt": "",  # Direct upload without caption
            "_tg_message": msg_obj,
        }

        with patch("megabot.ai.client.get_ai_config", return_value={"is_configured": False, "api_key": ""}):
            await run_job(app, job)

        app.download_media.assert_called_once()
        mock_upload.assert_called_once()
        uploaded_files = mock_upload.call_args[0][3]
        file_basenames = [os.path.basename(f) for f in uploaded_files]
        self.assertIn("extracted_file1.txt", file_basenames)
        self.assertIn("extracted_file2.txt", file_basenames)
        self.assertNotIn("my_archive.zip", file_basenames)

    @patch("megabot.core.pipeline._upload_files", new_callable=AsyncMock)
    @patch("megabot.core.pipeline._edit_status", new_callable=AsyncMock)
    @patch("megabot.core.database.db.set_job_status", new_callable=AsyncMock)
    @patch("megabot.core.database.db.get_user_setting", new_callable=AsyncMock)
    async def test_run_job_respects_keep_archive_prompt(self, mock_setting, mock_status, mock_edit, mock_upload):
        import zipfile
        mock_setting.return_value = "extract"

        app = MagicMock()
        msg_obj = MagicMock()
        msg_obj.id = 888

        def fake_download_zip(msg, file_name, progress):
            os.makedirs(os.path.dirname(file_name), exist_ok=True)
            with zipfile.ZipFile(file_name, "w") as zf:
                zf.writestr("inner.txt", "content")
            return file_name

        app.download_media = AsyncMock(side_effect=fake_download_zip)

        job = {
            "_id": "tg_job_keep_archive",
            "user_id": 123456,
            "chat_id": 123456,
            "message_id": 999,
            "url": "tg://media/888",
            "media_message_id": 888,
            "media_file_name": "bundle.zip",
            "media_file_size": 2048,
            "is_telegram_media": True,
            "prompt": "keep archive as-is",
            "_tg_message": msg_obj,
        }

        with patch("megabot.ai.client.get_ai_config", return_value={"is_configured": False, "api_key": ""}):
            await run_job(app, job)

        mock_upload.assert_called_once()
        uploaded_files = mock_upload.call_args[0][3]
        file_basenames = [os.path.basename(f) for f in uploaded_files]
        self.assertIn("bundle.zip", file_basenames)
        self.assertNotIn("inner.txt", file_basenames)
