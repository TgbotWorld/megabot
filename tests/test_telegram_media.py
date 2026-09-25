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
