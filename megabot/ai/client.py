# Advanced Multi-Provider AI Client with Dynamic Runtime Configuration
# OpenClaw-level upgrade: native function-calling, retries with backoff,
# unified chat completion, and normalized tool-call extraction.
import ast
import asyncio
import json
import logging
import re
import time
import uuid
from typing import Any, Optional
import aiohttp

from config import OPENROUTER_API_KEY, OPENROUTER_MODEL, OPENROUTER_BASE_URL
from megabot.core.database import db

log = logging.getLogger(__name__)

# ── OpenClaw-level transport tuning ─────────────────────────────────
MAX_RETRIES = 3
BASE_BACKOFF_S = 1.0
CHAT_TIMEOUT_S = 45


def _auth_headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/megabot",
        "X-Title": "MegaBot",
    }


async def _post_chat(url: str, headers: dict, payload: dict,
                     timeout_s: int = CHAT_TIMEOUT_S,
                     retries: int = MAX_RETRIES):
    """POST with exponential backoff. Returns (status, json_data|None, text)."""
    last_err = ""
    for attempt in range(1, retries + 1):
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout_s)) as session:
                async with session.post(url, headers=headers, json=payload) as resp:
                    if resp.status in (429, 500, 502, 503, 504) and attempt < retries:
                        wait = BASE_BACKOFF_S * (2 ** (attempt - 1)) + 0.2 * attempt
                        log.info("AI provider HTTP %s, retry %d/%d in %.1fs",
                                 resp.status, attempt, retries, wait)
                        await asyncio.sleep(wait)
                        last_err = f"HTTP {resp.status}"
                        continue
                    try:
                        data = await resp.json()
                    except Exception:
                        data = None
                    text = "" if data is not None else await resp.text()
                    return resp.status, data, text
        except (aiohttp.ClientError, asyncio.TimeoutError) as e:
            last_err = str(e)
            if attempt < retries:
                wait = BASE_BACKOFF_S * (2 ** (attempt - 1))
                log.info("AI network error (%s), retry %d/%d in %.1fs", e, attempt, retries, wait)
                await asyncio.sleep(wait)
                continue
            log.warning("Network error calling AI provider after %d attempts: %s", retries, e)
            return 0, None, last_err
        except Exception as e:
            log.warning("Unexpected error during AI call: %s", e)
            return 0, None, str(e)
    return 0, None, last_err

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
    Now backed by exponential-backoff transport.
    """
    cfg = await get_ai_config()
    if not cfg["is_configured"]:
        log.info("AI API key not configured; AI agent inactive.")
        return None

    url = format_chat_url(cfg["base_url"])
    headers = _auth_headers(cfg["api_key"])
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

    status, data, text = await _post_chat(url, headers, payload)
    if status == 400 and ("response_format" in (text or "").lower()
                          or "json_object" in (text or "").lower()):
        # Some models (e.g. certain Llama / Gemini / free models) don't support response_format
        log.info("Model %s does not support response_format, retrying without it...", cfg["model"])
        payload.pop("response_format", None)
        status, data, text = await _post_chat(url, headers, payload)
    if status != 200 or not data:
        log.warning("AI API returned HTTP %s: %s", status, (text or "")[:300])
        return None

    choices = (data or {}).get("choices", [])
    if not choices:
        return None
    content = choices[0].get("message", {}).get("content", "")
    if not content:
        # Native tool_calls payload may carry the JSON instead
        tool_calls = choices[0].get("message", {}).get("tool_calls", [])
        if tool_calls:
            try:
                args = tool_calls[0].get("function", {}).get("arguments", "{}")
                return json.loads(args) if isinstance(args, str) else dict(args)
            except Exception:
                return None
        return None
    return _parse_json_content(content)


def _parse_json_content(raw: str) -> dict | None:
    """Robust extractor that handles think tags, markdown codeblocks, AST, or substring dicts."""
    clean = re.sub(r"<think>[\s\S]*?</think>", "", raw or "", flags=re.I).strip()
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
    headers = _auth_headers(cfg["api_key"])
    temp = temperature if temperature is not None else cfg["temperature"]
    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": temp,
    }

    status, data, text = await _post_chat(url, headers, payload)
    if status != 200 or not data:
        log.warning("AI provider returned HTTP %s: %s", status, (text or "")[:300])
        return None
    content = (data.get("choices", [{}])[0].get("message", {}).get("content"))
    return content.strip() if content else None


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
    status, data, text = await _post_chat(url, headers, payload, timeout_s=20, retries=2)
    latency = int((time.time() - start) * 1000)
    if status == 200 and data:
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
    return {
        "success": False,
        "latency_ms": latency,
        "model": cfg.get("model", ""),
        "provider": cfg.get("provider_name", cfg.get("provider", "AI")),
        "error": f"HTTP {status}: {(text or '')[:180]}" if status else (text or "connection failed")[:180],
    }


# ══════════════════════════════════════════════════════════════════
#  OpenClaw-level: native function-calling + normalized ReAct output
# ══════════════════════════════════════════════════════════════════

def build_openai_tools() -> list[dict]:
    """Return OpenAI function-calling tool schemas derived from the registry."""
    try:
        from megabot.ai.tools import to_openai_tools
        return to_openai_tools()
    except Exception:
        return []


def _normalize_tool_calls(message: dict) -> list[dict]:
    """Normalize native `tool_calls` into [{id, name, arguments}]."""
    out: list[dict] = []
    for tc in message.get("tool_calls", []) or []:
        fn = tc.get("function", {}) or {}
        name = fn.get("name", "")
        raw_args = fn.get("arguments", "{}")
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except Exception:
                parsed = _parse_json_content(raw_args)
                args = parsed if isinstance(parsed, dict) else {}
        elif isinstance(raw_args, dict):
            args = raw_args
        else:
            args = {}
        if name:
            out.append({
                "id": tc.get("id") or f"call_{uuid.uuid4().hex[:8]}",
                "name": name,
                "arguments": args,
            })
    return out


def _extract_legacy_tool_calls(content: str) -> list[dict]:
    """Fallback: parse legacy JSON `{"action":"call_tool",...}` from text."""
    parsed = _parse_json_content(content or "")
    if not isinstance(parsed, dict):
        return []
    # Direct tool-call shape
    if parsed.get("action") == "call_tool" and parsed.get("tool"):
        params = parsed.get("parameters", {})
        return [{
            "id": f"call_{uuid.uuid4().hex[:8]}",
            "name": parsed["tool"],
            "arguments": params if isinstance(params, dict) else {},
            "thought": parsed.get("thought", ""),
        }]
    # Plural shape: {"tool_calls": [{"tool":..., "parameters":...}]}
    if isinstance(parsed.get("tool_calls"), list):
        out = []
        for tc in parsed["tool_calls"]:
            if isinstance(tc, dict) and (tc.get("tool") or tc.get("name")):
                out.append({
                    "id": f"call_{uuid.uuid4().hex[:8]}",
                    "name": tc.get("tool") or tc.get("name"),
                    "arguments": tc.get("parameters", tc.get("arguments", {})) or {},
                })
        return out
    return []


async def chat_completion(messages: list[dict],
                          temperature: Optional[float] = None,
                          max_tokens: Optional[int] = None,
                          tools: Optional[list[dict]] = None,
                          tool_choice: str = "auto",
                          json_mode: bool = False,
                          timeout_s: int = CHAT_TIMEOUT_S) -> dict | None:
    """Unified chat completion with optional native tools and usage stats."""
    cfg = await get_ai_config()
    if not cfg["is_configured"]:
        return None
    url = format_chat_url(cfg["base_url"])
    headers = _auth_headers(cfg["api_key"])
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
        "temperature": cfg["temperature"] if temperature is None else temperature,
    }
    if max_tokens:
        payload["max_tokens"] = max_tokens
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice
    elif json_mode:
        payload["response_format"] = {"type": "json_object"}

    status, data, text = await _post_chat(url, headers, payload, timeout_s=timeout_s)
    if status == 400 and tools and ("tool" in (text or "").lower()):
        # Provider doesn't support native tools → retry without them (legacy JSON)
        log.info("Provider rejected native tools, retrying in legacy JSON mode")
        payload.pop("tools", None)
        payload.pop("tool_choice", None)
        payload["response_format"] = {"type": "json_object"}
        status, data, text = await _post_chat(url, headers, payload, timeout_s=timeout_s)
    if status != 200 or not data:
        log.warning("chat_completion HTTP %s: %s", status, (text or "")[:300])
        return None
    choices = data.get("choices", [])
    if not choices:
        return None
    msg = choices[0].get("message", {}) or {}
    usage = data.get("usage", {}) or {}
    return {
        "content": msg.get("content") or "",
        "tool_calls": _normalize_tool_calls(msg),
        "raw_message": msg,
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
            "total_tokens": usage.get("total_tokens", 0),
        },
        "model": cfg["model"],
    }


async def call_agent_with_tools(messages: list[dict],
                                temperature: Optional[float] = None,
                                max_tokens: Optional[int] = None) -> dict | None:
    """OpenClaw-style single reasoning step: native tools first, legacy JSON fallback."""
    tools = build_openai_tools()
    res = await chat_completion(messages, temperature=temperature,
                                max_tokens=max_tokens, tools=tools or None)
    if res is None:
        return None
    if res["tool_calls"]:
        return {"action": "call_tools", "tool_calls": res["tool_calls"],
                "content": res["content"], "usage": res["usage"]}
    legacy = _extract_legacy_tool_calls(res["content"])
    if legacy:
        return {"action": "call_tools", "tool_calls": legacy,
                "content": res["content"], "usage": res["usage"]}
    parsed = _parse_json_content(res["content"])
    if isinstance(parsed, dict) and parsed.get("action") == "reply":
        return {"action": "reply", "response": parsed.get("response", res["content"]),
                "usage": res["usage"]}
    return {"action": "reply", "response": res["content"], "usage": res["usage"]}
