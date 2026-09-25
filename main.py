# MegaBot — MEGA downloader & Telegram uploader
import asyncio
import types

# Python 3.12 compatibility shim:
# asyncio.coroutine was removed in Python 3.11+, but legacy libraries
# (like tenacity < 6 used by mega.py) still reference it.
if not hasattr(asyncio, "coroutine"):
    asyncio.coroutine = types.coroutine

import os
import sys
import logging

from aiohttp import web
from pyrogram import Client, idle
from pyrogram.enums import ParseMode

from config import API_ID, API_HASH, BOT_TOKEN, PORT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
)
logging.getLogger("pyrogram").setLevel(logging.WARNING)

if not all([API_ID, API_HASH, BOT_TOKEN]):
    logging.critical("❌ Missing credentials: API_ID, API_HASH, or BOT_TOKEN not set in .env!")
    sys.exit(1)

SESSION_DIR = os.environ.get("SESSION_DIR", ".")
os.makedirs(SESSION_DIR, exist_ok=True)

app = Client(
    "megabot",
    workdir=SESSION_DIR,
    api_id=API_ID,
    api_hash=API_HASH,
    bot_token=BOT_TOKEN,
    plugins=dict(root="megabot.plugins"),
    workers=16,
    parse_mode=ParseMode.HTML,
)


async def set_bot_commands(client):
    try:
        from pyrogram.types import (
            BotCommand,
            BotCommandScopeDefault,
            BotCommandScopeAllPrivateChats,
        )
        commands = [
            BotCommand("start", "Start the bot & view features"),
            BotCommand("agent", "Check AI Agent status & dashboard"),
            BotCommand("clearmemory", "Clear AI conversation memory"),
            BotCommand("aiconfig", "AI Configuration menu (models, keys, providers)"),
            BotCommand("setmodel", "Change active AI model"),
            BotCommand("setprovider", "Change AI provider"),
            BotCommand("seturl", "Set custom OpenAI base URL"),
            BotCommand("setkey", "Set AI API key (auto-deleted for privacy)"),
            BotCommand("settemp", "Set AI temperature (0.0 - 2.0)"),
            BotCommand("settings", "Preferences & conversion options"),
            BotCommand("cancel", "Cancel current active job"),
            BotCommand("terabox", "Set TeraBox ndus cookie"),
            BotCommand("login", "Connect MEGA account"),
            BotCommand("logout", "Disconnect MEGA account"),
            BotCommand("help", "Help & command guide"),
            BotCommand("stats", "Bot statistics (owner)"),
        ]
        await client.set_bot_commands(commands, scope=BotCommandScopeDefault())
        try:
            await client.set_bot_commands(commands, scope=BotCommandScopeAllPrivateChats())
        except Exception:
            pass
        logging.info("✅ Successfully registered %d bot commands with Telegram", len(commands))
        return True
    except Exception as e:
        logging.warning("Setting bot commands skipped: %s", e)
        return False


async def web_server():
    """Keep-alive endpoint for Docker / Railway style hosts."""
    try:
        web_app = web.Application()
        web_app.router.add_get("/", lambda _: web.Response(text="MegaBot is running"))
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", PORT)
        await site.start()
        logging.info("Web keep-alive server on port %s", PORT)
    except Exception as e:
        logging.warning("Web server failed to bind on port %s: %s (non-fatal, bot continues)", PORT, e)


async def check_database():
    """Verify MongoDB Atlas connection with friendly error messaging."""
    from config import MONGO_URL
    from megabot.core.database import db
    if not MONGO_URL:
        logging.warning("⚠️ MONGO_URL not set — bot will run without database persistence!")
        return
    try:
        logging.info("Checking MongoDB connection...")
        await db.client.admin.command("ping")
        logging.info("✅ MongoDB Atlas connected successfully!")
    except Exception as e:
        logging.error("❌ MongoDB Atlas connection FAILED: %s", e)
        logging.error(
            "↳ Tips for MongoDB Atlas:\n"
            "  1. Network Access: Ensure IP 0.0.0.0/0 (Allow access from anywhere) is enabled in Atlas.\n"
            "  2. Database Access: Ensure the username and password in MONGO_URL are correct.\n"
            "  3. Special characters in password must be URL-encoded."
        )


async def check_ai():
    """Log AI Agent status on startup."""
    from megabot.ai.client import get_ai_config
    cfg = await get_ai_config()
    if not cfg["is_configured"]:
        logging.warning("🤖 AI Agent: Inactive (Set OPENROUTER_API_KEY in .env or via /setkey or /aiconfig in Telegram)")
        return
    logging.info("🤖 AI Agent: Active (Provider: %s, Model: %s, Privacy & Sandbox Enforced)", cfg["provider_name"], cfg["model"])


async def main():
    logging.info("Starting Telegram MTProto connection...")
    await app.start()
    me = await app.get_me()
    logging.info("✅ Connected to Telegram as @%s (ID: %s) 🚀", me.username, me.id)

    await check_database()
    await check_ai()
    await web_server()
    await set_bot_commands(app)

    # start the background job queue
    from megabot.core.job_queue import job_queue
    await job_queue.start(app)

    logging.info("⚡ MegaBot is fully operational and listening for messages!")
    await idle()
    await job_queue.stop()
    await app.stop()


if __name__ == "__main__":
    import asyncio
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("MegaBot stopped")
    except Exception as e:
        logging.critical("Fatal crash in main: %s", e, exc_info=True)