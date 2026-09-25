# Unit tests for AI Client and Telegram AI Configuration System
import asyncio
import unittest
from unittest.mock import AsyncMock, patch, MagicMock

from megabot.ai.client import (
    get_ai_config,
    set_ai_config,
    reset_ai_config,
    test_ai_connection as live_test_ai_conn,
    PROVIDER_PRESETS,
    POPULAR_MODELS,
)
from megabot.core.database import db
from megabot.plugins.aiconfig import (
    setmodel_cmd,
    setkey_cmd,
    setprovider_cmd,
    settemp_cmd,
    aiconfig_callbacks,
)


class TestAIConfig(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await reset_ai_config()

    async def asyncTearDown(self):
        await reset_ai_config()

    async def test_default_ai_config(self):
        cfg = await get_ai_config()
        self.assertIn("model", cfg)
        self.assertIn("provider", cfg)
        self.assertIn("base_url", cfg)
        self.assertIn("temperature", cfg)

    async def test_dynamic_set_and_get_config(self):
        await set_ai_config("model", "google/gemini-2.0-flash")
        await set_ai_config("api_key", "sk-test-secret-key-123456")
        await set_ai_config("temperature", 0.5)

        cfg = await get_ai_config()
        self.assertEqual(cfg["model"], "google/gemini-2.0-flash")
        self.assertEqual(cfg["api_key"], "sk-test-secret-key-123456")
        self.assertEqual(cfg["temperature"], 0.5)
        self.assertTrue(cfg["is_configured"])

    async def test_switch_provider_presets(self):
        pinfo = PROVIDER_PRESETS["gemini"]
        await set_ai_config("provider", "gemini")
        await set_ai_config("base_url", pinfo["base_url"])
        await set_ai_config("model", pinfo["default_model"])

        cfg = await get_ai_config()
        self.assertEqual(cfg["provider"], "gemini")
        self.assertEqual(cfg["base_url"], pinfo["base_url"])
        self.assertEqual(cfg["model"], pinfo["default_model"])

    async def test_reset_ai_config(self):
        await set_ai_config("model", "custom-model")
        await reset_ai_config()
        cfg = await get_ai_config()
        self.assertNotEqual(cfg["model"], "custom-model")

    @patch("megabot.ai.client.aiohttp.ClientSession.post")
    async def test_test_ai_connection(self, mock_post):
        # Mock successful response
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = {
            "choices": [{"message": {"content": "pong"}}]
        }
        mock_post.return_value.__aenter__.return_value = mock_resp

        res = await live_test_ai_conn({"api_key": "dummy_key", "base_url": "https://dummy.api", "model": "test-model"})
        self.assertTrue(res["success"])
        self.assertEqual(res["reply"], "pong")

    async def test_setmodel_command(self):
        msg = MagicMock()
        msg.from_user.id = 0
        msg.text = "/setmodel openai/gpt-4o-mini"
        msg.reply_text = AsyncMock()

        with patch("megabot.plugins.aiconfig._is_authorized", return_value=True):
            await setmodel_cmd(MagicMock(), msg)

        cfg = await get_ai_config()
        self.assertEqual(cfg["model"], "openai/gpt-4o-mini")
        msg.reply_text.assert_called_once()
        self.assertIn("AI Model Updated", msg.reply_text.call_args[0][0])

    async def test_setkey_command(self):
        client = MagicMock()
        client.send_message = AsyncMock()
        msg = MagicMock()
        msg.from_user.id = 0
        msg.chat.id = 12345
        msg.text = "/setkey sk-test-new-api-key-9999"
        msg.delete = AsyncMock()

        with patch("megabot.plugins.aiconfig._is_authorized", return_value=True):
            await setkey_cmd(client, msg)

        # Message must be deleted for security
        msg.delete.assert_called_once()

        cfg = await get_ai_config()
        self.assertEqual(cfg["api_key"], "sk-test-new-api-key-9999")
        client.send_message.assert_called_once()
        self.assertIn("AI API Key Saved Securely", client.send_message.call_args[0][1])

    async def test_settemp_command(self):
        msg = MagicMock()
        msg.from_user.id = 0
        msg.text = "/settemp 0.7"
        msg.reply_text = AsyncMock()

        with patch("megabot.plugins.aiconfig._is_authorized", return_value=True):
            await settemp_cmd(MagicMock(), msg)

        cfg = await get_ai_config()
        self.assertEqual(cfg["temperature"], 0.7)

    async def test_setprovider_custom_url(self):
        msg = MagicMock()
        msg.from_user.id = 0
        msg.command = ["setprovider"]
        msg.text = "/setprovider https://api.together.xyz/v1"
        msg.reply_text = AsyncMock()

        with patch("megabot.plugins.aiconfig._is_authorized", return_value=True):
            await setprovider_cmd(MagicMock(), msg)

        cfg = await get_ai_config()
        self.assertEqual(cfg["base_url"], "https://api.together.xyz/v1")
        self.assertEqual(cfg["provider"], "custom")

    async def test_seturl_command(self):
        msg = MagicMock()
        msg.from_user.id = 0
        msg.command = ["seturl"]
        msg.text = "/seturl https://my-llm.local/v1"
        msg.reply_text = AsyncMock()

        with patch("megabot.plugins.aiconfig._is_authorized", return_value=True):
            await setprovider_cmd(MagicMock(), msg)

        cfg = await get_ai_config()
        self.assertEqual(cfg["base_url"], "https://my-llm.local/v1")
        self.assertEqual(cfg["provider"], "custom")

    async def test_setprovider_custom_name_and_url(self):
        msg = MagicMock()
        msg.from_user.id = 0
        msg.command = ["setprovider"]
        msg.text = "/setprovider together https://api.together.xyz/v1"
        msg.reply_text = AsyncMock()

        with patch("megabot.plugins.aiconfig._is_authorized", return_value=True):
            await setprovider_cmd(MagicMock(), msg)

        cfg = await get_ai_config()
        self.assertEqual(cfg["provider"], "together")
        self.assertEqual(cfg["base_url"], "https://api.together.xyz/v1")

    def test_format_chat_url(self):
        from megabot.ai.client import format_chat_url
        self.assertEqual(format_chat_url("https://api.openai.com/v1"), "https://api.openai.com/v1/chat/completions")
        self.assertEqual(format_chat_url("https://api.openai.com/v1/"), "https://api.openai.com/v1/chat/completions")
        self.assertEqual(format_chat_url("https://api.openai.com/v1/chat/completions"), "https://api.openai.com/v1/chat/completions")
        self.assertEqual(format_chat_url("https://api.openai.com/v1/chat/completions/"), "https://api.openai.com/v1/chat/completions")
