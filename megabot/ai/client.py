# Advanced Multi-Provider AI Client with Dynamic Runtime Configuration
import ast
import json
import logging
import re
import time
from typing import Any, Optional
import aiohttp

from config import OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_BASE_URL
from megabot.core.database import db

log = logging.getLogger(__name__)

# Known AI Provider Presets
PROVIDER_PRESETS = {
    "openrouter": {
        "name": "OpenRouter",
        "base_url": "https://openrouter.ai/api/v1",
        "default_model": "google/gemini-2.0-flash",
    },
    "gemini": {
        "name": "Google Gemini",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_model": "gemini-2.0-flash",
    },
    "openai": {
        "name": "OpenAI",
        "base_url": "https://api.openai.com/v1",
        "default_model": "gpt-4o-mini",
    },
    "groq": {
        "name": "Groq",
        "base_url": "https://api.groq.com/openai/v1",
        "default_model": "llama-3.3-70b-versatile",
    },
    "deepseek": {
        "name": "DeepSeek",
        "base_url": "https://api.deepseek.com/v1",
        "default_model": "deepseek-chat",
    },
}

# Curated Top-Tier Modern AI Models
POPULAR_MODELS = [
    {"id": "google/gemini-2.0-flash", "name": "⚡ Gemini 2.0 Flash (Fast & Smart)", "provider": "openrouter"},
    {"id": "google/gemini-flash-1.5", "name": "🌟 Gemini 1.5 Flash (Balanced)", "provider": "openrouter"},
    {"id": "openai/gpt-4o-mini", "name": "🧠 GPT-4o Mini (OpenAI)", "provider": "openrouter"},
    {"id": "deepseek/deepseek-chat", "name": "🚀 DeepSeek V3 (Reasoning)", "provider": "openrouter"},
    {"id": "meta-llama/llama-3.3-70b-instruct:free", "name": "🦙 Llama 3.3 70B (Free OpenRouter)", "provider": "openrouter"},
    {"id": "nvidia/nemotron-3.5-lightning:free", "name": "⚡ Nemotron 3.5 (Free OpenRouter)", "provider": "openrouter"},
    {"id": "openai/gpt-4o", "name": "👑 GPT-4o Flagship", "provider": "openrouter"},
]


async def get_ai_config(user_id: Optional[int] = None) -> dict:
    """
    Resolve active AI configuration from database with fallback to .env defaults.
    Allows changing provider, model, API key, and temperature at runtime from Telegram.
    """
    # 1. API key: db global config -> .env
    api_key = await db.get_config("ai_api_key")
    if not api_key:
        api_key = OPENROUTER_API_KEY

    # 2. Provider: db global config -> detected from base_url -> "openrouter"
    provider = await db.get_config("ai_provider")
    if not provider:
        b_url_env = OPENROUTER_BASE_URL.lower()
        if "generativelanguage.googleapis" in b_url_env:
            provider = "gemini"
        elif "api.openai.com" in b_url_env:
            provider = "openai"
        elif "groq.com" in b_url_env:
            provider = "groq"
        elif "deepseek.com" in b_url_env:
            provider = "deepseek"
        else:
            provider = "openrouter"

    # 3. Base URL: db global config -> provider default -> .env
    base_url = await db.get_config("ai_base_url")
    if not base_url:
        if provider in PROVIDER_PRESETS:
            base_url = PROVIDER_PRESETS[provider]["base_url"]
        else:
            base_url = OPENROUTER_BASE_URL or "https://openrouter.ai/api/v1"

    # 4. Model: db global config -> .env -> provider default
    model = await db.get_config("ai_model")
    if not model:
        if OPENROUTER_MODEL and OPENROUTER_MODEL != "nvidia/nemotron-3.5-lightning:free":
            model = OPENROUTER_MODEL
        elif provider in PROVIDER_PRESETS:
            model = PROVIDER_PRESETS[provider]["default_model"]
        else:
            model = "google/gemini-2.0-flash"

    # 5. Temperature
    raw_temp = await db.get_config("ai_temperature")
    try:
        temperature = float(raw_temp) if raw_temp is not None else 0.2
    except (ValueError, TypeError):
        temperature = 0.2

    provider_name = PROVIDER_PRESETS.get(provider, {}).get("name", provider.title())
    if provider == "custom":
        provider_name = "Custom OpenAI-Compatible"

    return {
        "api_key": api_key.strip() if api_key else "",
        "provider": provider,
        "provider_name": provider_name,
        "model": model.strip(),
        "base_url": base_url.strip().rstrip("/"),
        "temperature": temperature,
        "is_configured": bool(api_key),
    }


def format_chat_url(base_url: str) -> str:
    """Format base URL to ensure proper /chat/completions endpoint without duplicate paths."""
    clean = (base_url or "https://openrouter.ai/api/v1").strip().rstrip("/")
    if clean.endswith("/chat/completions"):
        return clean
    return f"{clean}/chat/completions"


async def set_ai_config(key: str, value: Any) -> None:
    """Save an AI setting to MongoDB bot_config for instant persistence."""
    await db.set_config(f"ai_{key}", value)


async def reset_ai_config() -> None:
    """Delete database config overrides and revert to .env values."""
    for k in ["api_key", "provider", "model", "base_url", "temperature"]:
        await db.delete_config(f"ai_{k}")


async def call_openrouter_json(system_prompt: str, user_prompt: str,
                              temperature: Optional[float] = None) -> dict | None:
    """
    Send prompt to AI provider and safely extract structured JSON response.
    Includes auto-retry without response_format if provider doesn't support json mode.
    """
    cfg = await get_ai_config()
    if not cfg["is_configured"]:
        log.info("AI API key not configured; AI agent inactive.")
        return None

    url = format_chat_url(cfg["base_url"])
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/megabot",
        "X-Title": "MegaBot",
    }

    temp = temperature if temperature is not None else cfg["temperature"]

    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temp,
        "response_format": {"type": "json_object"},
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as session:
            # First attempt with json_object response format
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status == 400:
                    err_text = await resp.text()
                    # Some models (e.g. certain Llama / Gemini / free models) don't support response_format
                    if "response_format" in err_text.lower() or "json_object" in err_text.lower():
                        log.info("Model %s does not support response_format, retrying without it...", cfg["model"])
                        payload.pop("response_format", None)
                        async with session.post(url, headers=headers, json=payload) as retry_resp:
                            if retry_resp.status != 200:
                                log.warning("AI provider retry failed HTTP %s: %s", retry_resp.status, (await retry_resp.text())[:300])
                                return None
                            data = await retry_resp.json()
                    else:
                        log.warning("AI API returned HTTP 400: %s", err_text[:300])
                        return None
                elif resp.status != 200:
                    err_body = await resp.text()
                    log.warning("AI API returned HTTP %s: %s", resp.status, err_body[:300])
                    return None
                else:
                    data = await resp.json()

            choices = data.get("choices", [])
            if not choices:
                return None
            content = choices[0].get("message", {}).get("content", "")
            if not content:
                return None

            return _parse_json_content(content)

    except aiohttp.ClientError as e:
        log.warning("Network error calling AI provider: %s", e)
        return None
    except Exception as e:
        log.warning("Unexpected error during AI JSON call: %s", e)
        return None


def _parse_json_content(raw: str) -> dict | None:
    """Robust extractor that handles markdown codeblocks, AST, or substring dicts."""
    clean = raw.strip()
    if clean.startswith("```json"):
        clean = clean[7:]
    elif clean.startswith("```"):
        clean = clean[3:]
    if clean.endswith("```"):
        clean = clean[:-3]
    clean = clean.strip()

    try:
        return json.loads(clean)
    except Exception:
        pass

    try:
        res = ast.literal_eval(clean)
        if isinstance(res, dict):
            return res
    except Exception:
        pass

    match = re.search(r"(\{[\s\S]*\})", clean)
    if match:
        candidate = match.group(1)
        try:
            return json.loads(candidate)
        except Exception:
            try:
                res = ast.literal_eval(candidate)
                if isinstance(res, dict):
                    return res
            except Exception:
                pass

    return None


async def call_openrouter_text(system_prompt: str, user_prompt: str,
                              temperature: Optional[float] = None) -> str | None:
    """Send conversational prompt to active AI provider and return plain text response."""
    cfg = await get_ai_config()
    if not cfg["is_configured"]:
        log.info("AI API key not configured; AI agent inactive.")
        return None

    url = format_chat_url(cfg["base_url"])
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/megabot",
        "X-Title": "MegaBot",
    }

    temp = temperature if temperature is not None else cfg["temperature"]

    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temp,
    }

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=45)) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                if resp.status != 200:
                    err_body = await resp.text()
                    log.warning("AI provider returned HTTP %s: %s", resp.status, err_body[:300])
                    return None

                data = await resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content")
                return content.strip() if content else None

    except Exception as e:
        log.warning("AI text call error: %s", e)
        return None


async def test_ai_connection(config_override: Optional[dict] = None) -> dict:
    """
    Test live connectivity to the AI provider and return latency and status.
    Returns: {"success": bool, "latency_ms": int, "model": str, "provider": str, "error": str}
    """
    cfg = config_override or await get_ai_config()
    if not cfg.get("api_key"):
        return {
            "success": False,
            "latency_ms": 0,
            "model": cfg.get("model", ""),
            "provider": cfg.get("provider_name", "None"),
            "error": "API Key is not configured.",
        }

    url = format_chat_url(cfg["base_url"])
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/megabot",
        "X-Title": "MegaBot",
    }

    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "user", "content": "Ping. Respond with 'pong' in one word."},
        ],
        "temperature": 0.1,
        "max_tokens": 10,
    }

    start = time.time()
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20)) as session:
            async with session.post(url, headers=headers, json=payload) as resp:
                latency = int((time.time() - start) * 1000)
                if resp.status == 200:
                    data = await resp.json()
                    choices = data.get("choices", [])
                    reply = choices[0].get("message", {}).get("content", "").strip() if choices else "OK"
                    return {
                        "success": True,
                        "latency_ms": latency,
                        "model": cfg["model"],
                        "provider": cfg.get("provider_name", cfg.get("provider", "AI")),
                        "reply": reply,
                        "error": "",
                    }
                else:
                    err_txt = await resp.text()
                    return {
                        "success": False,
                        "latency_ms": latency,
                        "model": cfg["model"],
                        "provider": cfg.get("provider_name", cfg.get("provider", "AI")),
                        "error": f"HTTP {resp.status}: {err_txt[:180]}",
                    }
    except Exception as e:
        latency = int((time.time() - start) * 1000)
        return {
            "success": False,
            "latency_ms": latency,
            "model": cfg.get("model", ""),
            "provider": cfg.get("provider_name", cfg.get("provider", "AI")),
            "error": str(e),
        }
