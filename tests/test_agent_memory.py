# Unit tests for AI Agent Persistent Memory & Multi-Turn History
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from megabot.ai.tools import TOOL_DEFINITIONS, execute_tool
from megabot.core.database import Database, db
from megabot.plugins.agent import (
    _add_memory,
    clear_memory_command,
    detect_user_intents,
    _run_agent_turn,
)
from megabot.plugins.aiconfig import aiconfig_callbacks


class TestAgentMemory(unittest.TestCase):
    def setUp(self):
        # Reset memory for test user
        self.user_id = 88888
        self.context = {
            "user_id": self.user_id,
            "is_owner": False,
            "client": MagicMock(),
            "chat_id": 123456,
        }
        asyncio.run(db.clear_conversation_history(self.user_id))

    def tearDown(self):
        asyncio.run(db.clear_conversation_history(self.user_id))

    def test_database_in_memory_conversation_history(self):
        """Database fallback stores and retrieves conversation history chronologically."""
        test_db = Database(uri=None)
        uid = 55555

        # Empty initially
        history = asyncio.run(test_db.get_conversation_history(uid))
        self.assertEqual(history, [])

        # Add user and assistant turns
        asyncio.run(test_db.add_conversation_message(uid, "user", "Hello agent!"))
        asyncio.run(test_db.add_conversation_message(uid, "assistant", "Hello! How can I help?"))
        asyncio.run(test_db.add_conversation_message(uid, "user", "What can you do?"))

        history = asyncio.run(test_db.get_conversation_history(uid, limit=10))
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0], {"role": "user", "content": "Hello agent!"})
        self.assertEqual(history[1], {"role": "assistant", "content": "Hello! How can I help?"})
        self.assertEqual(history[2], {"role": "user", "content": "What can you do?"})

        # Test limit
        history_limited = asyncio.run(test_db.get_conversation_history(uid, limit=2))
        self.assertEqual(len(history_limited), 2)
        self.assertEqual(history_limited[0]["content"], "Hello! How can I help?")
        self.assertEqual(history_limited[1]["content"], "What can you do?")

        # Clear history
        deleted = asyncio.run(test_db.clear_conversation_history(uid))
        self.assertEqual(deleted, 3)
        self.assertEqual(asyncio.run(test_db.get_conversation_history(uid)), [])

    def test_database_mongo_conversation_methods(self):
        """Database delegates to MongoDB when collection is present."""
        test_db = Database(uri=None)
        mock_collection = MagicMock()
        mock_collection.insert_one = AsyncMock()

        # Mock find cursor for sort().limit().to_list()
        mock_cursor = MagicMock()
        mock_cursor.sort.return_value = mock_cursor
        mock_cursor.limit.return_value = mock_cursor
        mock_cursor.to_list = AsyncMock(return_value=[
            {"role": "assistant", "content": "Sure thing!"},
            {"role": "user", "content": "Can you help?"},
        ])
        mock_collection.find.return_value = mock_cursor

        mock_delete_res = MagicMock()
        mock_delete_res.deleted_count = 5
        mock_collection.delete_many = AsyncMock(return_value=mock_delete_res)

        test_db.conversations = mock_collection
        uid = 77777

        # Add message
        asyncio.run(test_db.add_conversation_message(uid, "user", "Mongo test"))
        mock_collection.insert_one.assert_called_once()
        saved_doc = mock_collection.insert_one.call_args[0][0]
        self.assertEqual(saved_doc["user_id"], uid)
        self.assertEqual(saved_doc["content"], "Mongo test")

        # Get history (reversed to chronological)
        history = asyncio.run(test_db.get_conversation_history(uid, limit=5))
        mock_collection.find.assert_called_with({"user_id": uid})
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]["content"], "Can you help?")
        self.assertEqual(history[1]["content"], "Sure thing!")

        # Clear history
        deleted = asyncio.run(test_db.clear_conversation_history(uid))
        mock_collection.delete_many.assert_called_with({"user_id": uid})
        self.assertEqual(deleted, 5)

    def test_tool_definition_exists(self):
        """clear_conversation_memory tool is in TOOL_DEFINITIONS."""
        tool_names = {t["name"] for t in TOOL_DEFINITIONS}
        self.assertIn("clear_conversation_memory", tool_names)

    def test_execute_clear_memory_tool(self):
        """execute_tool with clear_conversation_memory wipes history."""
        # Add messages first
        asyncio.run(db.add_conversation_message(self.user_id, "user", "turn 1"))
        asyncio.run(db.add_conversation_message(self.user_id, "assistant", "turn 2"))

        res = asyncio.run(execute_tool("clear_conversation_memory", {}, self.context))
        self.assertEqual(res.get("status"), "success")
        self.assertIn("Successfully cleared conversation memory", res.get("message"))
        self.assertEqual(res.get("count"), 2)

        # Confirm database is now empty for user
        history = asyncio.run(db.get_conversation_history(self.user_id))
        self.assertEqual(history, [])

    def test_execute_clear_memory_without_user_id(self):
        """execute_tool returns error if user_id is missing from context."""
        res = asyncio.run(execute_tool("clear_conversation_memory", {}, {}))
        self.assertEqual(res.get("status"), "error")

    def test_detect_user_intents_clear_memory(self):
        """detect_user_intents identifies clear memory phrases."""
        phrases = [
            "please clear memory",
            "reset memory now",
            "forget our conversation",
            "clear chat history",
            "forget everything we talked about",
            "wipe memory",
        ]
        for phrase in phrases:
            intents = detect_user_intents(phrase)
            intent_names = [name for name, _ in intents]
            self.assertIn("clear_conversation_memory", intent_names, f"Failed for phrase: '{phrase}'")

    def test_clear_memory_command(self):
        """clear_memory_command clears user history and responds."""
        asyncio.run(db.add_conversation_message(self.user_id, "user", "Message to clear"))

        mock_msg = MagicMock()
        mock_msg.from_user.id = self.user_id
        mock_msg.from_user.is_bot = False
        mock_msg.reply_text = AsyncMock()

        client = MagicMock()
        asyncio.run(clear_memory_command(client, mock_msg))

        mock_msg.reply_text.assert_called_once()
        reply_call_text = mock_msg.reply_text.call_args[0][0]
        self.assertIn("AI Memory Cleared", reply_call_text)
        self.assertIn("1", reply_call_text)

        # Confirm db is now empty
        history = asyncio.run(db.get_conversation_history(self.user_id))
        self.assertEqual(history, [])

    def test_aiconfig_callback_clearmem(self):
        """aiconfig callback with clearmem wipes memory for any user."""
        asyncio.run(db.add_conversation_message(self.user_id, "user", "Old context"))

        mock_cq = MagicMock()
        mock_cq.from_user.id = self.user_id
        mock_match = MagicMock()
        mock_match.group.return_value = "clearmem"
        mock_cq.matches = [mock_match]
        mock_cq.answer = AsyncMock()

        client = MagicMock()
        asyncio.run(aiconfig_callbacks(client, mock_cq))

        mock_cq.answer.assert_called_once()
        ans_text = mock_cq.answer.call_args[0][0]
        self.assertIn("AI Memory cleared", ans_text)

        history = asyncio.run(db.get_conversation_history(self.user_id))
        self.assertEqual(history, [])

    @patch("megabot.plugins.agent.call_openrouter_json", new_callable=AsyncMock)
    @patch("megabot.plugins.agent.get_ai_config", new_callable=AsyncMock)
    def test_run_agent_turn_loads_and_saves_memory(self, mock_cfg, mock_llm):
        """_run_agent_turn loads prior turns into prompt and saves new turns to DB."""
        mock_cfg.return_value = {
            "is_configured": True,
            "provider_name": "OpenRouter",
            "model": "google/gemini-2.0-flash",
        }
        mock_llm.return_value = {
            "action": "reply",
            "response": "<b>I remember your name!</b>",
        }

        # Seed initial conversation in DB
        asyncio.run(db.add_conversation_message(self.user_id, "user", "My name is Alice."))
        asyncio.run(db.add_conversation_message(self.user_id, "assistant", "Nice to meet you, Alice!"))

        mock_msg = MagicMock()
        mock_msg.from_user.id = self.user_id
        mock_msg.chat.id = 123456
        mock_status = MagicMock()
        mock_status.edit_text = AsyncMock()
        mock_msg.reply_text = AsyncMock(return_value=mock_status)

        client = MagicMock()
        client.send_chat_action = AsyncMock()

        asyncio.run(_run_agent_turn(client, mock_msg, "What is my name?"))

        # Verify prompt passed to LLM included recent conversation history
        prompt_passed = mock_llm.call_args[0][1]
        self.assertIn("Recent Conversation History:", prompt_passed)
        self.assertIn("User: My name is Alice.", prompt_passed)
        self.assertIn("AI Agent: Nice to meet you, Alice!", prompt_passed)
        self.assertIn("User Request: What is my name?", prompt_passed)

        # Verify new turns were saved to DB
        updated_history = asyncio.run(db.get_conversation_history(self.user_id, limit=10))
        self.assertEqual(len(updated_history), 4)
        self.assertEqual(updated_history[2]["content"], "What is my name?")
        self.assertEqual(updated_history[3]["content"], "<b>I remember your name!</b>")


if __name__ == "__main__":
    unittest.main()
