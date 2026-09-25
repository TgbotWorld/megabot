# Inline keyboard builders — callback data format:  action:job_id[:extra]
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from megabot.ai.client import POPULAR_MODELS, PROVIDER_PRESETS


def cancel_kb(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel:{job_id}")
    ]])


def archive_choice_kb(job_id: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📦 Upload archive", callback_data=f"arch:{job_id}:archive"),
            InlineKeyboardButton("📂 Decompress", callback_data=f"arch:{job_id}:extract"),
        ],
        [
            InlineKeyboardButton("🛑 Cancel", callback_data=f"cancel:{job_id}"),
        ],
    ])


def settings_kb(current: dict) -> InlineKeyboardMarkup:
    def toggle(emoji_on, emoji_off, key: str, label: str, value):
        icon = emoji_on if value else emoji_off
        return InlineKeyboardButton(f"{icon} {label}", callback_data=f"set:{key}")

    archive_labels = {"ask": "Ask me", "archive": "Archive as-is", "extract": "Always decompress"}
    return InlineKeyboardMarkup([
        [toggle("📑", "🗂️", "image_pdf", "Images → PDF", current.get("image_pdf", True))],
        [toggle("🎞️", "🎬", "video_thumbs", "Video thumbnails", current.get("video_thumbs", True))],
        [InlineKeyboardButton(f"📦 Archive mode: {archive_labels.get(current.get('archive_mode', 'ask'), 'Ask me')}",
                              callback_data="set:archive_mode")],
        [InlineKeyboardButton("🤖 AI Agent Configuration", callback_data="aiconf:main")],
    ])


def ai_config_kb(cfg: dict) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🧠 Switch Model", callback_data="aiconf:models"),
            InlineKeyboardButton("🌐 Switch Provider", callback_data="aiconf:providers"),
        ],
        [
            InlineKeyboardButton("🔑 Set API Key", callback_data="aiconf:key"),
            InlineKeyboardButton(f"🌡 Temp: {cfg.get('temperature', 0.2)}", callback_data="aiconf:temp"),
        ],
        [
            InlineKeyboardButton("🧪 Test Connection", callback_data="aiconf:test"),
            InlineKeyboardButton("🔄 Reset to Default", callback_data="aiconf:reset"),
        ],
        [
            InlineKeyboardButton("❌ Close", callback_data="aiconf:close"),
        ],
    ])


def ai_models_kb(current_model: str) -> InlineKeyboardMarkup:
    rows = []
    for m in POPULAR_MODELS:
        mid = m["id"]
        is_active = (mid == current_model)
        label = f"✅ {m['name']}" if is_active else m["name"]
        rows.append([InlineKeyboardButton(label, callback_data=f"aiconf:set_model:{mid}")])

    rows.append([
        InlineKeyboardButton("✏️ Custom Model Input", callback_data="aiconf:custom_model"),
        InlineKeyboardButton("🔙 Back", callback_data="aiconf:main"),
    ])
    return InlineKeyboardMarkup(rows)


def ai_providers_kb(current_provider: str) -> InlineKeyboardMarkup:
    rows = []
    for pid, pdata in PROVIDER_PRESETS.items():
        is_active = (pid == current_provider)
        label = f"✅ {pdata['name']}" if is_active else pdata["name"]
        rows.append([InlineKeyboardButton(label, callback_data=f"aiconf:set_prov:{pid}")])

    rows.append([
        InlineKeyboardButton("🔙 Back", callback_data="aiconf:main"),
    ])
    return InlineKeyboardMarkup(rows)


def ai_temperature_kb(current_temp: float) -> InlineKeyboardMarkup:
    temps = [0.0, 0.2, 0.5, 0.7, 1.0]
    buttons = []
    for t in temps:
        is_active = (abs(t - current_temp) < 0.05)
        label = f"[{t}]" if is_active else str(t)
        buttons.append(InlineKeyboardButton(label, callback_data=f"aiconf:set_temp:{t}"))

    return InlineKeyboardMarkup([
        buttons,
        [InlineKeyboardButton("🔙 Back", callback_data="aiconf:main")],
    ])