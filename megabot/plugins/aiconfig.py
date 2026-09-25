# Telegram AI Configuration Plugin — Change AI Provider, Models, Keys, and Temperature
import logging

from pyrogram import Client, filters
from pyrogram.types import CallbackQuery, Message

from config import OWNER_ID
from megabot.ai.client import (
    get_ai_config,
    set_ai_config,
    reset_ai_config,
    test_ai_connection,
    PROVIDER_PRESETS,
)
from megabot.core.database import db
from megabot.ui.keyboards import (
    ai_config_kb,
    ai_models_kb,
    ai_providers_kb,
    ai_temperature_kb,
)

log = logging.getLogger(__name__)


def _is_authorized(user_id: int) -> bool:
    """Allow bot owner (or any user if OWNER_ID is not configured) to modify AI config."""
    if not OWNER_ID:
        return True
    return user_id == OWNER_ID


async def render_ai_dashboard(client: Client, chat_id: int, message_id: int = None,
                              user_id: int = None, alert_text: str = None) -> None:
    """Render interactive AI configuration card."""
    cfg = await get_ai_config(user_id)
    masked_key = (cfg["api_key"][:7] + "…" + cfg["api_key"][-4:]) if cfg["api_key"] else "Not configured ⚠️"

    status_icon = "Online & Ready ✅" if cfg["is_configured"] else "Inactive (Needs API Key) ⚠️"

    text = (
        "<blockquote>🤖 <b>MegaBot AI Configuration</b></blockquote>\n"
        f"• <b>Status:</b> {status_icon}\n"
        f"• <b>Provider:</b> <b>{cfg['provider_name']}</b>\n"
        f"• <b>Active Model:</b> <code>{cfg['model']}</code>\n"
        f"• <b>Base URL:</b> <code>{cfg['base_url']}</code>\n"
        f"• <b>API Key:</b> <code>{masked_key}</code>\n"
        f"• <b>Temperature:</b> <code>{cfg['temperature']}</code>\n\n"
        "<i>Tap buttons below to change models, switch providers, or update keys in real time without restarting:</i>"
    )

    kb = ai_config_kb(cfg)

    try:
        if message_id:
            await client.edit_message_text(chat_id, message_id, text, reply_markup=kb, disable_web_page_preview=True)
        else:
            await client.send_message(chat_id, text, reply_markup=kb, disable_web_page_preview=True)
    except Exception as e:
        log.debug("Dashboard render error: %s", e)


@Client.on_message(filters.command(["aiconfig", "aiconf"]) & filters.private & filters.incoming & ~filters.bot)
async def aiconfig_cmd(client: Client, message: Message):
    """Open interactive AI configuration dashboard."""
    user_id = message.from_user.id
    if not _is_authorized(user_id):
        await message.reply_text("🚫 Only the bot owner can configure global AI settings.")
        return

    await render_ai_dashboard(client, message.chat.id, user_id=user_id)


@Client.on_message(filters.command("setmodel") & filters.private & filters.incoming & ~filters.bot)
async def setmodel_cmd(client: Client, message: Message):
    """Set active AI model via command shortcut: /setmodel <model_name>"""
    user_id = message.from_user.id
    if not _is_authorized(user_id):
        await message.reply_text("🚫 Only the bot owner can change the AI model.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text(
            "<b>Usage:</b> <code>/setmodel &lt;model_id&gt;</code>\n\n"
            "<b>Examples:</b>\n"
            "• <code>/setmodel google/gemini-2.0-flash</code>\n"
            "• <code>/setmodel google/gemini-flash-1.5</code>\n"
            "• <code>/setmodel openai/gpt-4o-mini</code>\n"
            "• <code>/setmodel deepseek/deepseek-chat</code>\n"
            "• <code>/setmodel meta-llama/llama-3.3-70b-instruct:free</code>"
        )
        return

    new_model = parts[1].strip()
    await set_ai_config("model", new_model)
    await message.reply_text(
        f"✅ <b>AI Model Updated!</b>\n"
        f"Active model is now: <code>{new_model}</code>\n\n"
        f"Test it with /agent or open /aiconfig."
    )


@Client.on_message(filters.command("setkey") & filters.private & filters.incoming & ~filters.bot)
async def setkey_cmd(client: Client, message: Message):
    """Set AI API Key securely: /setkey <key> (auto-deletes your message for privacy)."""
    user_id = message.from_user.id
    if not _is_authorized(user_id):
        await message.reply_text("🚫 Only the bot owner can set the API key.")
        return

    # Delete message immediately to protect credentials
    try:
        await message.delete()
    except Exception:
        pass

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await client.send_message(
            message.chat.id,
            "<b>Usage:</b> <code>/setkey &lt;your-api-key&gt;</code>\n"
            "<i>(Your message will be automatically deleted for security.)</i>"
        )
        return

    new_key = parts[1].strip()
    await set_ai_config("api_key", new_key)

    masked = new_key[:7] + "…" + new_key[-4:]
    await client.send_message(
        message.chat.id,
        f"✅ <b>AI API Key Saved Securely!</b>\n"
        f"Key: <code>{masked}</code>\n"
        "Your original message was deleted to protect your privacy.\n\n"
        "Test connection with /aiconfig."
    )


@Client.on_message(filters.command(["setprovider", "seturl", "setendpoint"]) & filters.private & filters.incoming & ~filters.bot)
async def setprovider_cmd(client: Client, message: Message):
    """Switch AI provider or set custom OpenAI-compatible endpoint URL."""
    user_id = message.from_user.id
    if not _is_authorized(user_id):
        await message.reply_text("🚫 Only the bot owner can change the AI provider.")
        return

    cmd = message.command[0].lower()
    parts = message.text.split(maxsplit=2)
    if len(parts) < 2:
        prov_list = ", ".join(f"<code>{p}</code>" for p in PROVIDER_PRESETS.keys())
        await message.reply_text(
            f"<b>Usage:</b>\n"
            f"• <b>Switch to preset:</b> <code>/setprovider &lt;name&gt;</code> (e.g. {prov_list})\n"
            f"• <b>Set custom OpenAI URL:</b> <code>/seturl &lt;base_url&gt;</code> (e.g. <code>/seturl https://api.together.xyz/v1</code>)\n"
            f"• <b>Set Provider & URL:</b> <code>/setprovider &lt;name&gt; &lt;base_url&gt;</code>\n\n"
            f"<i>You can use ANY OpenAI-compatible API endpoint and model!</i>"
        )
        return

    # If called via /seturl or /setendpoint
    if cmd in ["seturl", "setendpoint"]:
        new_url = parts[1].strip()
        await set_ai_config("provider", "custom")
        await set_ai_config("base_url", new_url)
        await message.reply_text(
            f"✅ <b>Custom OpenAI Endpoint Set!</b>\n"
            f"• Base URL: <code>{new_url}</code>\n"
            f"• Provider: <b>Custom OpenAI-Compatible</b>\n\n"
            f"👉 Set your model: <code>/setmodel &lt;model_id&gt;</code>\n"
            f"👉 Set your API key: <code>/setkey &lt;key&gt;</code>\n"
            f"🧪 Test connection with: <code>/aiconfig</code>"
        )
        return

    first_arg = parts[1].strip()

    # Case 1: First argument is already a URL (e.g. /setprovider https://api.together.xyz/v1)
    if first_arg.startswith("http://") or first_arg.startswith("https://"):
        await set_ai_config("provider", "custom")
        await set_ai_config("base_url", first_arg)
        await message.reply_text(
            f"✅ <b>Custom OpenAI Endpoint Set!</b>\n"
            f"• Base URL: <code>{first_arg}</code>\n"
            f"• Provider: <b>Custom OpenAI-Compatible</b>\n\n"
            f"👉 Set your model: <code>/setmodel &lt;model_id&gt;</code>\n"
            f"👉 Set your API key: <code>/setkey &lt;key&gt;</code>"
        )
        return

    prov = first_arg.lower()

    # Case 2: Known preset without extra URL
    if prov in PROVIDER_PRESETS and len(parts) == 2:
        pinfo = PROVIDER_PRESETS[prov]
        await set_ai_config("provider", prov)
        await set_ai_config("base_url", pinfo["base_url"])
        await set_ai_config("model", pinfo["default_model"])
        await message.reply_text(
            f"✅ <b>AI Provider Switched to {pinfo['name']}!</b>\n"
            f"• Base URL: <code>{pinfo['base_url']}</code>\n"
            f"• Default Model: <code>{pinfo['default_model']}</code>\n\n"
            f"<i>Make sure you have set the appropriate API key for {pinfo['name']} with /setkey!</i>"
        )
        return

    # Case 3: Custom provider with base URL: /setprovider together https://api.together.xyz/v1
    if len(parts) >= 3:
        custom_name = parts[1].strip()
        custom_url = parts[2].strip()
        await set_ai_config("provider", custom_name)
        await set_ai_config("base_url", custom_url)
        await message.reply_text(
            f"✅ <b>Custom OpenAI-Compatible Provider Configured!</b>\n"
            f"• Provider: <b>{custom_name}</b>\n"
            f"• Base URL: <code>{custom_url}</code>\n\n"
            f"👉 Set your model: <code>/setmodel &lt;model_id&gt;</code>\n"
            f"👉 Set your API key: <code>/setkey &lt;key&gt;</code>"
        )
        return

    # Case 4: Known extended providers
    known_urls = {
        "together": "https://api.together.xyz/v1",
        "mistral": "https://api.mistral.ai/v1",
        "perplexity": "https://api.perplexity.ai",
        "ollama": "http://localhost:11434/v1",
        "lmstudio": "http://localhost:1234/v1",
    }
    if prov in known_urls:
        await set_ai_config("provider", prov)
        await set_ai_config("base_url", known_urls[prov])
        await message.reply_text(
            f"✅ <b>AI Provider Switched to {prov.title()}!</b>\n"
            f"• Base URL: <code>{known_urls[prov]}</code>\n\n"
            f"👉 Set your model: <code>/setmodel &lt;model_id&gt;</code>\n"
            f"👉 Set your API key: <code>/setkey &lt;key&gt;</code>"
        )
        return

    # Case 5: Custom provider name only (e.g. /setprovider custom)
    await set_ai_config("provider", prov)
    await message.reply_text(
        f"✅ Provider set to <b>{prov}</b>.\n\n"
        f"💡 Please configure the base URL with:\n"
        f"<code>/seturl &lt;base_url&gt;</code>\n"
        f"<i>(e.g. <code>/seturl https://api.together.xyz/v1</code>)</i>"
    )


@Client.on_message(filters.command("settemp") & filters.private & filters.incoming & ~filters.bot)
async def settemp_cmd(client: Client, message: Message):
    """Set AI temperature: /settemp <0.0 - 2.0>"""
    user_id = message.from_user.id
    if not _is_authorized(user_id):
        await message.reply_text("🚫 Only the bot owner can change AI temperature.")
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.reply_text("<b>Usage:</b> <code>/settemp &lt;0.0 to 2.0&gt;</code>")
        return

    try:
        temp = float(parts[1].strip())
        if not (0.0 <= temp <= 2.0):
            raise ValueError
    except ValueError:
        await message.reply_text("❌ Temperature must be a number between 0.0 and 2.0.")
        return

    await set_ai_config("temperature", temp)
    await message.reply_text(f"✅ AI Temperature set to <code>{temp}</code>.")


# ── Interactive Callback Query Handlers ─────────────────────────

@Client.on_callback_query(filters.regex(r"^aiconf:(.*)$"))
async def aiconfig_callbacks(client: Client, cq: CallbackQuery):
    user_id = cq.from_user.id
    if not _is_authorized(user_id):
        await cq.answer("🚫 Owner only.", show_alert=True)
        return

    action = cq.matches[0].group(1)

    if action == "main":
        await cq.answer()
        await render_ai_dashboard(client, cq.message.chat.id, cq.message.id, user_id=user_id)

    elif action == "models":
        await cq.answer()
        cfg = await get_ai_config(user_id)
        text = (
            "<blockquote>🧠 <b>Select AI Model</b></blockquote>\n"
            f"Current model: <code>{cfg['model']}</code>\n\n"
            "Choose a top-tier modern model below:"
        )
        await cq.message.edit_text(text, reply_markup=ai_models_kb(cfg["model"]), disable_web_page_preview=True)

    elif action.startswith("set_model:"):
        model_id = action.split(":", 1)[1]
        await set_ai_config("model", model_id)
        await cq.answer(f"✅ Model set to: {model_id}", show_alert=True)
        await render_ai_dashboard(client, cq.message.chat.id, cq.message.id, user_id=user_id)

    elif action == "custom_model":
        await cq.answer()
        text = (
            "<blockquote>✏️ <b>Custom Model Input</b></blockquote>\n"
            "To use any custom model from your provider, send the command:\n\n"
            "<code>/setmodel &lt;model_id&gt;</code>\n\n"
            "<b>Examples:</b>\n"
            "• <code>/setmodel google/gemini-2.0-flash</code>\n"
            "• <code>/setmodel anthropic/claude-3.5-sonnet</code>\n"
            "• <code>/setmodel mistralai/mistral-large</code>"
        )
        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Models", callback_data="aiconf:models")]])
        await cq.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)

    elif action == "providers":
        await cq.answer()
        cfg = await get_ai_config(user_id)
        text = (
            "<blockquote>🌐 <b>Select AI Provider</b></blockquote>\n"
            f"Current provider: <b>{cfg['provider_name']}</b>\n\n"
            "Select a preset provider (automatically configures default endpoint and model):"
        )
        await cq.message.edit_text(text, reply_markup=ai_providers_kb(cfg["provider"]), disable_web_page_preview=True)

    elif action == "custom_prov":
        await cq.answer()
        text = (
            "<blockquote>🌐 <b>Custom OpenAI-Compatible Provider</b></blockquote>\n"
            "You can use <b>ANY</b> OpenAI-compatible endpoint or model (Together, Ollama, vLLM, Mistral, Perplexity, or custom server).\n\n"
            "<b>Setup commands:</b>\n"
            "• <code>/seturl &lt;base_url&gt;</code> — Set endpoint URL (e.g. <code>https://api.together.xyz/v1</code>)\n"
            "• <code>/setmodel &lt;model_id&gt;</code> — Set any model name\n"
            "• <code>/setkey &lt;api_key&gt;</code> — Save your API key\n\n"
            "<i>Or one-line setup:</i>\n"
            "<code>/setprovider &lt;name&gt; &lt;base_url&gt;</code>"
        )
        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Providers", callback_data="aiconf:providers")]])
        await cq.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)

    elif action.startswith("set_prov:"):
        prov_id = action.split(":", 1)[1]
        if prov_id in PROVIDER_PRESETS:
            pinfo = PROVIDER_PRESETS[prov_id]
            await set_ai_config("provider", prov_id)
            await set_ai_config("base_url", pinfo["base_url"])
            await set_ai_config("model", pinfo["default_model"])
            await cq.answer(f"✅ Switched to {pinfo['name']}!", show_alert=True)
        await render_ai_dashboard(client, cq.message.chat.id, cq.message.id, user_id=user_id)

    elif action == "temp":
        await cq.answer()
        cfg = await get_ai_config(user_id)
        text = (
            "<blockquote>🌡 <b>Adjust Temperature</b></blockquote>\n"
            f"Current temperature: <code>{cfg['temperature']}</code>\n\n"
            "• <b>0.0 - 0.2:</b> Strict, precise, deterministic (Recommended for tools & jobs)\n"
            "• <b>0.5 - 0.7:</b> Balanced conversational\n"
            "• <b>1.0:</b> Highly creative\n\n"
            "Select new temperature:"
        )
        await cq.message.edit_text(text, reply_markup=ai_temperature_kb(cfg["temperature"]), disable_web_page_preview=True)

    elif action.startswith("set_temp:"):
        val = float(action.split(":", 1)[1])
        await set_ai_config("temperature", val)
        await cq.answer(f"✅ Temperature set to {val}", show_alert=True)
        await render_ai_dashboard(client, cq.message.chat.id, cq.message.id, user_id=user_id)

    elif action == "key":
        await cq.answer()
        text = (
            "<blockquote>🔑 <b>Set AI API Key</b></blockquote>\n"
            "To set or update your API key safely, send:\n\n"
            "<code>/setkey &lt;your-api-key&gt;</code>\n\n"
            "🛡️ <i>Your message will be automatically deleted immediately to prevent exposing keys in chat history.</i>\n"
            "The key is securely stored in your bot's database."
        )
        from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back to Config", callback_data="aiconf:main")]])
        await cq.message.edit_text(text, reply_markup=kb, disable_web_page_preview=True)

    elif action == "test":
        await cq.answer("Testing connection…")
        status_msg = await cq.message.reply_text("🧪 <i>Testing live AI connection…</i>")
        res = await test_ai_connection()
        await status_msg.delete()

        if res.get("success"):
            alert_msg = (
                f"✅ Connected to {res.get('provider')}!\n"
                f"• Model: {res.get('model')}\n"
                f"• Latency: {res.get('latency_ms')} ms\n"
                f"• Response: \"{res.get('reply')}\""
            )
        else:
            alert_msg = (
                f"⚠️ Connection Failed!\n"
                f"• Provider: {res.get('provider')}\n"
                f"• Error: {res.get('error')}"
            )
        await cq.answer(alert_msg, show_alert=True)

    elif action == "reset":
        await reset_ai_config()
        await cq.answer("🔄 AI settings reset to .env defaults!", show_alert=True)
        await render_ai_dashboard(client, cq.message.chat.id, cq.message.id, user_id=user_id)

    elif action == "close":
        await cq.answer()
        try:
            await cq.message.delete()
        except Exception:
            pass
