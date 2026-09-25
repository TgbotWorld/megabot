# OpenClaw-level conversation memory: proper message arrays + compaction.
import logging

from megabot.core.database import db

log = logging.getLogger(__name__)

MAX_HISTORY_TURNS = 8          # kept verbatim for the fast path
COMPACT_THRESHOLD = 24         # compact when history exceeds this
DEFAULT_KEEP_LAST = 6


def estimate_tokens(text: str) -> int:
    """Rough token estimate (chars/4) — avoids a tokenizer dependency."""
    return max(1, len(text or "") // 4)


async def get_memory_summary(user_id: int) -> str:
    """Return persisted memory summary, if any."""
    try:
        summary = await db.get_config(f"memory_summary_{int(user_id)}")
        return summary or ""
    except Exception:
        return ""


async def set_memory_summary(user_id: int, summary: str) -> None:
    try:
        await db.set_config(f"memory_summary_{int(user_id)}", (summary or "")[:2000])
    except Exception as e:
        log.debug("set_memory_summary failed: %s", e)


async def build_llm_messages(user_id: int, system_prompt: str, user_text: str,
                             extra_context: str = "",
                             limit: int = MAX_HISTORY_TURNS) -> list[dict]:
    """Build OpenAI-style messages: system (+summary) + history + user."""
    messages: list[dict] = [{"role": "system", "content": system_prompt}]
    summary = await get_memory_summary(user_id)
    if summary:
        messages.append({"role": "system",
                         "content": f"Earlier conversation summary: {summary}"})
    history = await db.get_conversation_history(user_id, limit=limit)
    for turn in history:
        role = turn.get("role", "user")
        if role not in ("user", "assistant", "system"):
            role = "user"
        content = str(turn.get("content", ""))[:1500]
        if content:
            messages.append({"role": role, "content": content})
    full_user = user_text + (f"\n{extra_context}" if extra_context else "")
    messages.append({"role": "user", "content": full_user[:4000]})
    return messages


async def build_legacy_prompt(user_id: int, user_text: str,
                              extra_context: str = "",
                              limit: int = MAX_HISTORY_TURNS) -> str:
    """Legacy single-string prompt (kept for provider fallback + tests)."""
    history_turns = await db.get_conversation_history(user_id, limit=limit)
    recent_history = ""
    if history_turns:
        h_lines = []
        for turn in history_turns:
            pfx = "User" if turn["role"] == "user" else "AI Agent"
            h_lines.append(f"{pfx}: {turn['content']}")
        recent_history = "\nRecent Conversation History:\n" + "\n".join(h_lines)
    summary = await get_memory_summary(user_id)
    prefix = f"Earlier summary: {summary}\n" if summary else ""
    return f"{prefix}User Request: {user_text}{extra_context}{recent_history}"


async def compact_memory(user_id: int, keep_last: int = DEFAULT_KEEP_LAST) -> dict:
    """Summarize older turns into a persisted summary; keep last N verbatim."""
    history = await db.get_conversation_history(user_id, limit=50)
    if len(history) <= keep_last + 2:
        return {"message": "Memory is small; no compaction needed.",
                "turns": len(history), "compacted": False}
    older = history[:len(history) - keep_last]
    recent = history[len(history) - keep_last:]
    blob = "\n".join(f"{t['role']}: {t['content']}" for t in older)[:6000]

    summary = ""
    try:
        from megabot.ai.client import call_openrouter_text
        summary = await call_openrouter_text(
            "Summarize this Telegram bot conversation in 3-5 terse bullet facts "
            "(user goals, ongoing jobs, preferences). No chit-chat.",
            blob) or ""
    except Exception as e:
        log.debug("LLM summarization failed, using heuristic: %s", e)
    if not summary:
        # Heuristic fallback: keep first lines of older turns
        summary = "; ".join(t["content"][:120] for t in older[:5])[:800]

    await set_memory_summary(user_id, summary)
    # Rewrite history: drop older turns, keep summary + recent
    await db.clear_conversation_history(user_id)
    await db.add_conversation_message(user_id, "system", f"Summary of earlier chat: {summary}")
    for t in recent:
        await db.add_conversation_message(user_id, t["role"], t["content"])
    return {"message": f"Compacted {len(older)} older turn(s) into a summary; kept last {len(recent)}.",
            "turns": len(recent) + 1, "compacted": True, "summary": summary[:500]}


async def maybe_compact_memory(user_id: int) -> None:
    """Auto-compact when history grows past threshold (fire-and-forget safe)."""
    try:
        history = await db.get_conversation_history(user_id, limit=COMPACT_THRESHOLD + 1)
        if len(history) > COMPACT_THRESHOLD:
            await compact_memory(user_id, keep_last=DEFAULT_KEEP_LAST)
    except Exception as e:
        log.debug("maybe_compact_memory skipped: %s", e)
