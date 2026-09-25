# Owner-only commands: /stats, /ban, /unban, /broadcast
import logging
import shutil

import psutil
from pyrogram import Client, filters
from pyrogram.types import Message

from config import OWNER_ID, DOWNLOAD_DIR
from megabot.core.database import db
from megabot.processors.uploader import human_size

log = logging.getLogger(__name__)


def owner_only(func):
    async def wrapper(client: Client, message: Message):
        if message.from_user.id != OWNER_ID:
            await message.reply_text("🚫 Owner only.")
            return
        await func(client, message)
    return wrapper


@Client.on_message(filters.command("stats"))
@owner_only
async def stats_cmd(client: Client, message: Message):
    total_users = await db.total_users_count()
    total_jobs = await db.count_jobs()
    done_jobs = await db.count_jobs("done")
    failed_jobs = await db.count_jobs("failed")
    success_rate = f"{done_jobs * 100 // max(total_jobs, 1)}%"

    disk = shutil.disk_usage(DOWNLOAD_DIR)
    db_stats = await db.get_db_stats()

    text = (
        "<blockquote>📊 <b>MegaBot Stats</b></blockquote>\n"
        f"👥 Users: <b>{total_users}</b>\n"
        f"📦 Jobs: <b>{total_jobs}</b> (✅ {done_jobs} • ❌ {failed_jobs})\n"
        f"🎯 Success rate: <b>{success_rate}</b>\n"
        f"💾 Disk: <b>{human_size(disk.used)}</b> / {human_size(disk.total)} "
        f"(free {human_size(disk.free)})\n"
        f"🗄️ DB size: <b>{human_size((db_stats or {}).get('storage_size', 0))}</b>\n"
        f"🖥️ RAM: <b>{psutil.virtual_memory().percent}%</b> • "
        f"CPU: <b>{psutil.cpu_percent()}%</b>"
    )
    await message.reply_text(text, disable_web_page_preview=True)


@Client.on_message(filters.command("ban"))
@owner_only
async def ban_cmd(client: Client, message: Message):
    try:
        target = int(message.command[1])
    except (IndexError, ValueError):
        await message.reply_text("Usage: <code>/ban &lt;user_id&gt;</code>")
        return
    await db.set_ban(target, True, "manual ban")
    await message.reply_text(f"🚫 Banned <code>{target}</code>.")


@Client.on_message(filters.command("unban"))
@owner_only
async def unban_cmd(client: Client, message: Message):
    try:
        target = int(message.command[1])
    except (IndexError, ValueError):
        await message.reply_text("Usage: <code>/unban &lt;user_id&gt;</code>")
        return
    await db.set_ban(target, False)
    await message.reply_text(f"✅ Unbanned <code>{target}</code>.")


@Client.on_message(filters.command("broadcast"))
@owner_only
async def broadcast_cmd(client: Client, message: Message):
    content = message.text.split(maxsplit=1)
    if len(content) < 2:
        await message.reply_text("Usage: <code>/broadcast &lt;text&gt;</code>")
        return
    users = await db.get_all_users()
    sent = failed = 0
    for user in users:
        try:
            await client.send_message(user["_id"], f"📢 {content[1]}")
            sent += 1
        except Exception:
            failed += 1
    await message.reply_text(f"📢 Broadcast done: ✅ {sent} sent, ❌ {failed} failed.")


@Client.on_message(filters.command(["setcommands", "sync", "reloadcommands"]))
@owner_only
async def setcommands_cmd(client: Client, message: Message):
    """Force re-register bot commands with Telegram servers in both Default and Private scopes."""
    from main import set_bot_commands
    status_msg = await message.reply_text("🔄 Syncing bot commands with Telegram servers...")
    ok = await set_bot_commands(client)
    if ok:
        await status_msg.edit_text(
            "✅ <b>Bot commands successfully synced with Telegram!</b>\n\n"
            "Registered in both <b>Default</b> and <b>Private Chats</b> scopes.\n\n"
            "💡 <i>Tip: Telegram apps cache the command menu. If you do not see them immediately in your '/' menu, completely close and reopen your Telegram app.</i>"
        )
    else:
        await status_msg.edit_text("❌ Failed to sync bot commands. Check server console logs for details.")