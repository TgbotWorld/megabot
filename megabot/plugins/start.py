# /start & /help
import logging
from pyrogram import Client, filters
from pyrogram.types import Message

from megabot.core.database import db
from megabot.ui import texts

log = logging.getLogger(__name__)
_commands_initialized = False


async def ensure_commands(client: Client):
    global _commands_initialized
    if not _commands_initialized:
        try:
            from main import set_bot_commands
            await set_bot_commands(client)
            _commands_initialized = True
        except Exception as e:
            log.debug("ensure_commands check: %s", e)


@Client.on_message(filters.command("start") & filters.private & filters.incoming & ~filters.bot)
async def start_cmd(client: Client, message: Message):
    if not message.from_user or message.from_user.is_bot:
        return
    await ensure_commands(client)
    await db.add_user(message.from_user.id, message.from_user.username)
    if await db.is_user_banned(message.from_user.id):
        await message.reply_text(texts.BANNED)
        return
    await message.reply_text(texts.WELCOME, disable_web_page_preview=True)


@Client.on_message(filters.command("help") & filters.private & filters.incoming & ~filters.bot)
async def help_cmd(client: Client, message: Message):
    if not message.from_user or message.from_user.is_bot:
        return
    await ensure_commands(client)
    await message.reply_text(texts.HELP, disable_web_page_preview=True)