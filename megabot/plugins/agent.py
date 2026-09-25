# Autonomous AI Agent Plugin — Natural Language Brain & Tool Execution Engine
import asyncio
from collections import defaultdict
import json
import logging
import time
import uuid

from pyrogram import Client, filters
from pyrogram.enums import ChatAction
from pyrogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton

from config import OWNER_ID, MAX_JOBS_PER_USER, MAX_FILE_SIZE_MB
from megabot.ai.client import (
    call_openrouter_text,
    call_openrouter_json,
    get_ai_config,
    test_ai_connection,
)
from megabot.ai.tools import TOOL_DEFINITIONS, execute_tool
from megabot.core.database import db
from megabot.core.job_queue import job_queue
from megabot.downloaders import extract_supported_links, is_supported_link
from megabot.processors.uploader import human_size
from megabot.ui import texts

log = logging.getLogger(__name__)

# Sliding conversation memory: user_id -> list of {"role": "user"|"assistant", "content": str}
_conversation_memory = db._mem_conversations
MAX_HISTORY_TURNS = 8

AGENT_SYSTEM_PROMPT = f"""You are the autonomous MegaBot AI Agent on Telegram.
You are directly connected to MegaBot's backend server with FULL AUTHORITY and DIRECT SYSTEM ACCESS to execute real tools.

CRITICAL ANTI-REFUSAL DIRECTIVES:
1. YOU ARE NOT A TEXT-ONLY ASSISTANT. You have REAL backend tools executing on this server.
2. NEVER REFUSE ANY ACTION claiming you lack filesystem access, device access, process controls, memory management, or physical capabilities. That is completely FALSE for MegaBot.
   - User asks to stop/cancel background jobs? You HAVE the `cancel_job` tool!
   - User asks to clean disk/free up storage/free up desk? You HAVE the `clean_disk` tool!
   - User asks to unzip/extract archives? You HAVE the `unzip_files` tool!
   - User asks to delete files? You HAVE the `delete_job_files` tool!
   - User asks about jobs or status? You HAVE the `list_jobs` tool!
   - User asks to download links? You HAVE the `start_download` tool!
   - User asks to configure AI/keys/models? You HAVE the `update_ai_config` tool!
   - User asks to clear memory/forget conversation/wipe history? You HAVE the `clear_conversation_memory` tool!
3. YOU MUST ALWAYS USE YOUR TOOLS. Whenever the user asks to perform an action, return a JSON tool call (`"action": "call_tool"`). NEVER answer with "I cannot perform this action" or "I am a text-based AI without system controls".

AVAILABLE TOOLS:
{json.dumps(TOOL_DEFINITIONS, indent=2)}

CAPABILITIES & RULES:
1. TOOL DISPATCH:
   - When the user sends download link(s) (MEGA, MediaFire, MP4Upload, TeraBox, or direct HTTP/HTTPS web links), or asks to download a URL:
     Call tool `start_download` with the URLs and any instructions.
   - When the user asks to change or check AI settings (model, API key, provider, temperature, base_url):
     Call tool `update_ai_config` or `get_ai_config`.
   - When the user asks to clear memory, forget conversation, or reset chat history:
     Call tool `clear_conversation_memory`.
   - When the user asks to unzip, decompress, or extract archives (ZIP, RAR, 7Z, TAR, GZ):
     Call tool `unzip_files`.
   - When the user asks to delete job files from server disk:
     Call tool `delete_job_files`.
   - When the user asks to clean disk, free storage, or clean server space:
     Call tool `clean_disk`.
   - When the user asks to cancel, stop, or abort background jobs:
     Call tool `cancel_job`.
   - When the user asks about jobs, queue, or history:
     Call tool `list_jobs` or `get_job_details`.
   - When the user asks what files are inside a downloaded job directory:
     Call tool `list_job_files`.
   - When the user asks about disk space or server statistics:
     Call tool `get_system_stats`.
   - When the user asks to change user settings (archive mode, PDF merging, video thumbs):
     Call tool `update_user_setting`.
   - When the user asks to set or update TeraBox session cookie:
     Call tool `set_terabox_cookie`.
   - When the user asks to clear cache:
     Call tool `clear_cache`.

2. MULTI-STEP REASONING:
   - If a request requires multiple steps (e.g. check jobs then unzip, or cancel then delete), call the first tool.
   - You will receive the tool output and can call the next tool or give the final reply.

3. CONVERSATIONAL BEHAVIOR:
   - If the user greets, chats, or asks what you can do:
     Respond warmly and clearly in Telegram HTML format (<b>, <i>, <code>).
     State that you have direct autonomous tools to download links, extract archives, clean storage, manage background jobs, and adjust settings!

4. RESPONSE FORMAT (Respond with JSON only):
   To execute a tool:
   {{
     "action": "call_tool",
     "tool": "<tool_name>",
     "parameters": {{ ... }},
     "thought": "<brief reason>"
   }}

   To reply directly to the user:
   {{
     "action": "reply",
     "response": "<friendly response formatted in Telegram HTML (use <b>, <i>, <code>)>"
   }}
"""

FALLBACK_CONVERSATIONAL_PROMPT = """You are the autonomous MegaBot AI Agent on Telegram.
MegaBot is a powerful cloud downloader and file processor:
- Downloads MEGA, MediaFire, MP4Upload, TeraBox, and direct web links at high speed.
- Receives uploaded Telegram files (documents, videos, audios, photos) and auto-extracts archives.
- Unzips ZIP, RAR, 7Z, TAR archives, merges images to PDF, generates video thumbnails.
- Manages background download jobs, cleans disk space, deletes files, and adjusts AI settings (/aiconfig).

Answer the user helpfully and concisely in Telegram HTML (<b>, <i>, <code>).
NEVER claim you lack system access, device access, or process control. MegaBot is fully equipped with backend tools."""


def detect_user_intents(text: str) -> list[tuple[str, dict]]:
    """Detect tool execution intents directly from user message text."""
    text_l = text.lower()
    intents = []

    # Cancel / Stop background jobs
    if any(k in text_l for k in [
        "stop background", "cancel background", "stop job", "cancel job",
        "stop download", "cancel download", "abort job", "stop active", "cancel active"
    ]):
        words = text.split()
        job_id = ""
        for w in words:
            if len(w) in [8, 10] and w.isalnum() and not w.isalpha():
                job_id = w
                break
        intents.append(("cancel_job", {"job_id": job_id} if job_id else {}))

    # Clean disk / Free storage / free desk (typo for disk)
    if any(k in text_l for k in [
        "clean disk", "free disk", "free storage", "clear storage", "free up storage",
        "free up disk", "free up your disk", "free up my disk", "free up desk",
        "free your desk", "free up your desk", "clear disk", "clean server",
        "free space", "free up space", "clean storage"
    ]):
        intents.append(("clean_disk", {}))

    # Unzip / Extract archives
    if any(k in text_l for k in [
        "unzip", "extract", "decompress", "unrar", "untar", "extract archive",
        "extract files", "extract file", "extract archives"
    ]):
        intents.append(("unzip_files", {}))

    # Delete job files
    if any(k in text_l for k in [
        "delete file", "delete files", "remove file", "remove files", "delete job files", "delete my files"
    ]):
        intents.append(("delete_job_files", {}))

    # System / Disk stats
    if any(k in text_l for k in [
        "system stats", "server stats", "disk space", "storage stats", "how much disk"
    ]):
        intents.append(("get_system_stats", {}))

    # List jobs
    if any(k in text_l for k in [
        "show jobs", "list jobs", "my jobs", "active jobs", "queue status", "job status"
    ]):
        intents.append(("list_jobs", {}))

    # Clear conversation memory
    if any(k in text_l for k in [
        "clear memory", "reset memory", "forget chat", "forget conversation",
        "clear chat", "clear conversation", "wipe memory", "delete chat history",
        "forget everything", "reset chat", "clear our conversation",
        "forget what we talked about", "forget our conversation"
    ]):
        intents.append(("clear_conversation_memory", {}))

    return intents


def is_ai_refusal(text: str) -> bool:
    """Detect if an LLM generated an out-of-context refusal claiming lack of system/file access."""
    if not text:
        return False
    tl = text.lower()
    refusal_phrases = [
        "without system-level controls",
        "text-based ai assistant",
        "without access to your device",
        "without access to your computer",
        "physical environment",
        "i don't have access to your computer",
        "i cannot access your filesystem",
        "without access to the filesystem",
        "cannot extract files from archives directly",
        "i don't have the ability to extract files",
        "i cannot stop background jobs",
        "cannot stop background jobs",
        "as an ai, i cannot",
        "as an ai language model, i do not have access",
        "don't have access to your computer's processes",
    ]
    return any(p in tl for p in refusal_phrases)


async def _add_memory(user_id: int, role: str, content: str):
    """Store recent turn in persistent conversation memory."""
    if not content:
        return
    await db.add_conversation_message(user_id, role, content)


@Client.on_message(filters.command(["clearmemory", "resetmemory", "forget"]) & filters.private & filters.incoming & ~filters.bot)
async def clear_memory_command(client: Client, message: Message):
    """Clear AI agent conversation history for the user."""
    if not message.from_user or message.from_user.is_bot:
        return
    user_id = message.from_user.id
    count = await db.clear_conversation_history(user_id)
    await message.reply_text(
        "<blockquote>🧠 <b>AI Memory Cleared</b></blockquote>\n"
        f"Erased <b>{count}</b> message(s) from conversation memory.\n"
        "Starting with a fresh context!"
    )


@Client.on_message(filters.command(["agent", "ai"]) & filters.private & filters.incoming & ~filters.bot)
async def agent_command(client: Client, message: Message):
    """Show interactive AI Agent dashboard or run inline query."""
    if not message.from_user or message.from_user.is_bot:
        return

    # Check if user provided an inline query: /agent <query>
    parts = message.text.split(maxsplit=1)
    if len(parts) > 1:
        query = parts[1].strip()
        await _run_agent_turn(client, message, query)
        return

    # If no query, show interactive agent dashboard
    status_msg = await message.reply_text("🤖 <i>Connecting to AI Agent…</i>")
    user_id = message.from_user.id
    cfg = await get_ai_config(user_id)

    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("⚙️ Configure AI Settings", callback_data="aiconf:main")],
        [InlineKeyboardButton("🧪 Test Connection", callback_data="aiconf:test")],
        [InlineKeyboardButton("🧠 Clear Memory", callback_data="aiconf:clearmem")],
    ])

    if not cfg["is_configured"]:
        text = (
            "<blockquote>🤖 <b>MegaBot Autonomous AI Agent</b></blockquote>\n"
            "• <b>Status:</b> Inactive (API Key needed) ⚠️\n"
            f"• <b>Provider:</b> {cfg['provider_name']}\n"
            f"• <b>Configured Model:</b> <code>{cfg['model']}</code>\n\n"
            "💡 <i>To activate conversational AI and smart dispatch:</i>\n"
            "• Tap <b>Configure AI Settings</b> below or send <code>/setkey &lt;your-key&gt;</code>\n"
            "• Downloads, unzipping, and Telegram file processing still run automatically!"
        )
        await status_msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)
        return

    # Test live connection
    conn = await test_ai_connection(cfg)
    latency = conn.get("latency_ms", 0)

    if conn.get("success"):
        text = (
            "<blockquote>🤖 <b>MegaBot Autonomous AI Agent</b></blockquote>\n"
            "• <b>Status:</b> Online & Ready ✅\n"
            f"• <b>Provider:</b> {cfg['provider_name']}\n"
            f"• <b>Model:</b> <code>{cfg['model']}</code>\n"
            f"• <b>Latency:</b> <code>{latency} ms</code>\n"
            "• <b>Privacy:</b> 🛡️ <i>Zero file content inspection</i>\n"
            "• <b>Sandbox:</b> 🔒 <i>Enforced job directory jail</i>\n\n"
            "🛠 <b>Autonomous Tools & Capabilities:</b>\n"
            "• 📥 <b>Downloads:</b> MEGA, MediaFire, MP4Upload, TeraBox & Direct Web URLs\n"
            "• 📁 <b>Telegram Files:</b> Direct file/document uploads & automatic processing\n"
            "• 📦 <b>Unzip:</b> Extract ZIP, RAR, 7Z, TAR archives automatically\n"
            "• 🖼️ <b>PDF:</b> Merge image sets into single ordered PDFs\n"
            "• 🗑️ <b>Files:</b> Delete job files & auto-clean server disk\n"
            "• 📋 <b>Jobs:</b> List, inspect, track, and cancel active jobs\n"
            "• ⚙️ <b>AI Config:</b> Switch models, keys, and providers right from Telegram!\n\n"
            "💬 <b>Chat with me directly:</b>\n"
            "Send any message, paste links, send files, or ask me to perform tasks!"
        )
    else:
        text = (
            "<blockquote>⚠️ <b>AI Agent: Connection Warning</b></blockquote>\n"
            f"Could not contact AI provider: <code>{conn.get('error')}</code>\n"
            f"• <b>Provider:</b> {cfg['provider_name']}\n"
            f"• <b>Model:</b> <code>{cfg['model']}</code>\n\n"
            "<i>Tap Configure AI Settings to change provider, model, or API key.</i>"
        )

    await status_msg.edit_text(text, reply_markup=kb, disable_web_page_preview=True)


@Client.on_message(filters.command("cancel") & filters.private & filters.incoming & ~filters.bot)
async def cancel_command(client: Client, message: Message):
    """Direct shortcut command to cancel active jobs."""
    user_id = message.from_user.id
    parts = message.text.split(maxsplit=1)
    target_id = parts[1].strip() if len(parts) > 1 else ""

    context = {
        "user_id": user_id,
        "is_owner": (user_id == OWNER_ID),
        "client": client,
        "chat_id": message.chat.id,
    }

    res = await execute_tool("cancel_job", {"job_id": target_id}, context)
    if res.get("status") == "success":
        await message.reply_text(f"<blockquote>❌ <b>Job Cancelled</b></blockquote>\n{res.get('message')}")
    else:
        await message.reply_text(f"<blockquote>⚠️ <b>Cancel Failed</b></blockquote>\n{res.get('message')}")


# ── Incoming Telegram Files & Media Handler ─────────────────────

@Client.on_message(
    filters.private
    & filters.incoming
    & ~filters.bot
    & ~filters.service
    & (filters.document | filters.video | filters.audio | filters.photo)
)
async def on_user_media(client: Client, message: Message):
    """
    Handle direct Telegram file uploads (documents, archives, videos, audios, photos).
    Enqueues the media for downloading and pipeline processing (unzip, PDF merge, thumbs, etc.).
    """
    if not message.from_user or message.from_user.is_bot:
        return

    user_id = message.from_user.id
    await db.add_user(user_id, message.from_user.username)

    if await db.is_user_banned(user_id):
        await message.reply_text(texts.BANNED)
        return

    # Check active jobs limit
    active = await db.active_jobs_for_user(user_id)
    if active >= MAX_JOBS_PER_USER:
        await message.reply_text(
            f"⏳ You already have {active} active job(s). Maximum allowed is {MAX_JOBS_PER_USER}. "
            "Please wait for your current job to finish."
        )
        return

    # Extract file attributes
    file_name = "telegram_file"
    file_size = 0

    if message.document:
        file_name = message.document.file_name or "document"
        file_size = message.document.file_size or 0
    elif message.video:
        file_name = message.video.file_name or f"video_{message.id}.mp4"
        file_size = message.video.file_size or 0
    elif message.audio:
        file_name = message.audio.file_name or f"audio_{message.id}.mp3"
        file_size = message.audio.file_size or 0
    elif message.photo:
        file_name = f"photo_{message.id}.jpg"
        file_size = message.photo.file_size or 0

    # Check file size limit
    if file_size and file_size > MAX_FILE_SIZE_MB * 1024 * 1024:
        await message.reply_text(texts.error_too_large(file_name, file_size))
        return

    instruction = (message.caption or "").strip()
    job_id = uuid.uuid4().hex[:10]

    # Create live status card
    status_card = await message.reply_text(
        texts.status_queued(f"Telegram file: {file_name}"),
        disable_web_page_preview=True
    )

    # Register job in MongoDB
    job = await db.create_job(
        job_id,
        user_id=user_id,
        chat_id=message.chat.id,
        url=f"tg://media/{message.id}",
        message_id=status_card.id,
        prompt=instruction,
    )
    job["media_message_id"] = message.id
    job["media_file_name"] = file_name
    job["media_file_size"] = file_size
    job["is_telegram_media"] = True

    await job_queue.submit(job)

    log.info("Queued Telegram media download job %s for %s (%s)", job_id, file_name, human_size(file_size))


@Client.on_message(
    filters.private
    & filters.incoming
    & ~filters.bot
    & ~filters.service
    & filters.text
    & ~filters.command([
        "start", "help", "settings", "stats", "ban", "unban",
        "broadcast", "login", "logout", "cancel", "agent", "ai",
        "terabox", "cookie", "aiconfig", "aiconf", "setmodel",
        "setkey", "setprovider", "settemp", "seturl", "clearmemory",
        "resetmemory", "forget"
    ])
)
async def on_user_message(client: Client, message: Message):
    """Primary entry point for ALL user text messages (links, questions, requests)."""
    if not message.from_user or message.from_user.is_bot or message.from_user.is_self:
        return

    user_id = message.from_user.id

    # Allow the login wizard to intercept credentials if active
    from megabot.plugins.auth import _login_state
    if user_id in _login_state:
        return

    await db.add_user(user_id, message.from_user.username)

    if await db.is_user_banned(user_id):
        await message.reply_text(texts.BANNED)
        return

    # Dispatch directly to the AI Agent reasoning engine
    await _run_agent_turn(client, message, message.text.strip())


async def _run_agent_turn(client: Client, message: Message, user_text: str):
    """Autonomous agent reasoning loop with tool execution and multi-turn context."""
    user_id = message.from_user.id
    is_owner = (user_id == OWNER_ID)
    chat_id = message.chat.id

    detected_links = extract_supported_links(user_text)

    context = {
        "user_id": user_id,
        "is_owner": is_owner,
        "client": client,
        "chat_id": chat_id,
    }

    ai_cfg = await get_ai_config(user_id)

    # If AI API key is not configured:
    if not ai_cfg["is_configured"]:
        if detected_links:
            res = await execute_tool("start_download", {"urls": detected_links, "instruction": user_text}, context)
            if res.get("status") == "success":
                await message.reply_text(
                    f"🚀 <b>Download Queued!</b>\nJob ID: <code>{res.get('job_id')}</code>\n"
                    "<i>(Set an AI API key using /aiconfig or /setkey to activate full conversational brain.)</i>"
                )
            else:
                await message.reply_text(f"❌ {res.get('message')}")
            return
        else:
            kb = InlineKeyboardMarkup([[
                InlineKeyboardButton("⚙️ Configure AI Key", callback_data="aiconf:main")
            ]])
            await message.reply_text(
                "<blockquote>🤖 <b>MegaBot AI Agent</b></blockquote>\n"
                "To chat with the AI Agent and use autonomous tools, set your API key using <code>/setkey &lt;key&gt;</code> or open <code>/aiconfig</code>.\n\n"
                "You can still download MEGA, MediaFire, MP4Upload, TeraBox, or direct web links by pasting them here, or send files directly to extract them!",
                reply_markup=kb,
                disable_web_page_preview=True,
            )
            return

    await client.send_chat_action(chat_id, ChatAction.TYPING)
    status_msg = await message.reply_text("🤖 <i>Agent reasoning…</i>")

    # Build context with conversation history and detected links
    recent_history = ""
    history_turns = await db.get_conversation_history(user_id, limit=MAX_HISTORY_TURNS)
    if history_turns:
        h_lines = []
        for turn in history_turns:
            pfx = "User" if turn["role"] == "user" else "AI Agent"
            h_lines.append(f"{pfx}: {turn['content']}")
        recent_history = "\nRecent Conversation History:\n" + "\n".join(h_lines)

    extra_info = ""
    if detected_links:
        extra_info += f"\nDetected Download Links: {json.dumps(detected_links)}\n"

    current_prompt = f"User Request: {user_text}{extra_info}{recent_history}"

    max_steps = 3
    final_reply_text = None
    executed_tools = set()

    intents = detect_user_intents(user_text)

    for step in range(max_steps):
        try:
            plan = await call_openrouter_json(AGENT_SYSTEM_PROMPT, current_prompt)
        except Exception as e:
            log.warning("AI call failed in step %d: %s", step, e)
            plan = None

        if not plan or not isinstance(plan, dict):
            # Fallback 1: if links are detected on step 0, ensure download starts!
            if step == 0 and detected_links:
                executed_tools.add("start_download")
                res = await execute_tool("start_download", {"urls": detected_links, "instruction": user_text}, context)
                if res.get("status") == "success":
                    final_reply_text = (
                        f"<blockquote>🚀 <b>Download Queued</b></blockquote>\n"
                        f"• <b>Job ID:</b> <code>{res.get('job_id')}</code>\n"
                        f"• <b>Links:</b> {len(detected_links)} link(s)\n"
                        f"• <b>Extraction:</b> <i>Auto-extract archives enabled</i>\n\n"
                        f"<i>Live progress status is running above!</i>"
                    )
                else:
                    final_reply_text = f"❌ {res.get('message')}"
                break

            # Fallback 2: if direct action intents were detected (e.g. stop jobs, clean disk, unzip)
            if intents:
                log.info("Executing detected intents fallback for: %s", user_text)
                results = []
                for t_name, t_params in intents:
                    executed_tools.add(t_name)
                    try:
                        res = await execute_tool(t_name, t_params, context)
                        results.append(res.get("message", f"{t_name} completed."))
                    except Exception as te:
                        results.append(f"{t_name}: {te}")
                final_reply_text = (
                    "<blockquote>🛠 <b>Autonomous Action Executed</b></blockquote>\n"
                    + "\n".join(f"• {r}" for r in results)
                )
                break

            # Fallback 3: conversational response with tool-aware prompt
            fb_text = await call_openrouter_text(
                FALLBACK_CONVERSATIONAL_PROMPT,
                user_text
            )
            if is_ai_refusal(fb_text):
                fb_text = (
                    "<blockquote>🤖 <b>MegaBot Autonomous AI Agent</b></blockquote>\n"
                    "I am directly connected to the server and have full tools to process files, extract archives, clean storage, and manage background jobs!\n\n"
                    "Paste any link (MEGA, MediaFire, MP4Upload, TeraBox), upload a file, or ask me: <i>'clean disk'</i> or <i>'unzip files'</i>."
                )
            final_reply_text = fb_text or "⚠️ I'm temporarily unable to reach the AI engine. Please try again shortly."
            break

        action = plan.get("action", "reply")

        if action == "call_tool":
            tool_name = plan.get("tool")
            params = plan.get("parameters", {})
            if not isinstance(params, dict):
                params = {}

            executed_tools.add(tool_name)

            # If user sent links and model called start_download without passing URLs, auto-fill
            if tool_name == "start_download" and not params.get("urls") and detected_links:
                params["urls"] = detected_links

            try:
                await status_msg.edit_text(f"⚙️ <i>Using tool:</i> <code>{tool_name}</code>…")
            except Exception:
                pass

            # Execute tool
            tool_res = await execute_tool(tool_name, params, context)

            # If tool is start_download and successful, we can finalize immediately
            if tool_name == "start_download" and tool_res.get("status") == "success":
                jid = tool_res.get("job_id")
                n_urls = len(tool_res.get("urls", []))
                inst = tool_res.get("instruction", "")
                inst_str = f"\n• <b>Instructions:</b> <i>{inst}</i>" if inst else ""
                final_reply_text = (
                    f"<blockquote>🚀 <b>Download Started</b></blockquote>\n"
                    f"• <b>Job ID:</b> <code>{jid}</code>\n"
                    f"• <b>Links:</b> {n_urls} link(s)\n"
                    f"• <b>Mode:</b> Auto-extract archives active{inst_str}\n\n"
                    f"<i>Watch the live progress card above!</i>"
                )
                break

            # Otherwise, feed tool result back for next iteration
            current_prompt = (
                f"Original User Request: {user_text}\n"
                f"You executed tool '{tool_name}' with parameters {json.dumps(params)}.\n"
                f"Tool Result:\n{json.dumps(tool_res, indent=2)}\n\n"
                "If the task is complete, respond with action 'reply' and provide a clear, helpful response in Telegram HTML. "
                "If another tool is needed, respond with action 'call_tool'."
            )

        else:
            # Action is reply
            reply_cand = plan.get("response") or plan.get("summary") or ""
            if is_ai_refusal(reply_cand) and intents:
                log.info("Detected AI refusal in plan response for action request, executing intents directly...")
                results = []
                for t_name, t_params in intents:
                    executed_tools.add(t_name)
                    try:
                        res = await execute_tool(t_name, t_params, context)
                        results.append(res.get("message", f"{t_name} completed."))
                    except Exception as te:
                        results.append(f"{t_name}: {te}")
                final_reply_text = (
                    "<blockquote>🛠 <b>Autonomous Action Executed</b></blockquote>\n"
                    + "\n".join(f"• {r}" for r in results)
                )
                break
            elif is_ai_refusal(reply_cand):
                final_reply_text = (
                    "<blockquote>🤖 <b>MegaBot Autonomous AI Agent</b></blockquote>\n"
                    "I am directly connected to the server and have full tools to process files, extract archives, clean storage, and manage background jobs!\n\n"
                    "💡 <i>Try commands like /cancel, /settings, /aiconfig, or send links or files directly.</i>"
                )
                break
            else:
                final_reply_text = reply_cand
                break

    if not final_reply_text:
        final_reply_text = "✅ Done."

    try:
        await status_msg.edit_text(final_reply_text, disable_web_page_preview=True)
    except Exception as e:
        log.warning("Could not edit status message: %s", e)
        try:
            await message.reply_text(final_reply_text, disable_web_page_preview=True)
        except Exception:
            pass

    # Save to memory (skip if memory was explicitly cleared during this turn)
    if "clear_conversation_memory" not in executed_tools:
        await _add_memory(user_id, "user", user_text)
        await _add_memory(user_id, "assistant", final_reply_text)
