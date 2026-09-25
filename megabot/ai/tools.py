# AI Agent Tool Registry & Execution Engine
import asyncio
import logging
import os
import shutil
import uuid
from typing import Optional

from config import DOWNLOAD_DIR, OWNER_ID, MAX_JOBS_PER_USER
from megabot.core.database import db
from megabot.core.job_queue import job_queue
from megabot.downloaders import extract_supported_links, get_link_key, is_supported_link
from megabot.ui import texts

log = logging.getLogger(__name__)

# Available tools exposed to the AI Agent
TOOL_DEFINITIONS = [
    {
        "name": "start_download",
        "description": "Download one or more files/folders from MEGA, MediaFire, MP4Upload, TeraBox, or direct HTTP/HTTPS web links. Automatically queues and tracks the download with optional custom instructions (e.g., unzip archives, merge images into PDF, filter files, keep archive).",
        "parameters": {
            "urls": "A list of URLs or single URL (MEGA, MediaFire, MP4Upload, TeraBox, or direct HTTP/HTTPS link) (required).",
            "instruction": "Optional instructions for what to do with the files (e.g. 'unzip archive', 'extract only videos', 'convert images to pdf', 'delete samples')."
        }
    },
    {
        "name": "get_ai_config",
        "description": "Check current AI configuration: provider, active model, base URL, temperature, and status.",
        "parameters": {}
    },
    {
        "name": "update_ai_config",
        "description": "Update AI configuration settings directly from Telegram. Can update model, api_key, provider, base_url, or temperature.",
        "parameters": {
            "key": "The setting to change: 'model', 'api_key', 'provider', 'base_url', or 'temperature' (required).",
            "value": "The new value for the setting (e.g., 'google/gemini-2.0-flash', 'openai/gpt-4o-mini', 'deepseek/deepseek-chat', '0.2', or API key string) (required)."
        }
    },
    {
        "name": "test_ai_connection",
        "description": "Test live connection to the configured AI provider, measuring response latency and health.",
        "parameters": {}
    },
    {
        "name": "list_jobs",
        "description": "List active, queued, or recently finished download jobs. Users see their own jobs; bot owner sees all.",
        "parameters": {
            "status": "Optional filter: 'all', 'queued', 'downloading', 'processing', 'uploading', 'done', 'failed', 'cancelled'. Default 'all'.",
            "limit": "Maximum number of jobs to return (default 5, max 10)."
        }
    },
    {
        "name": "get_job_details",
        "description": "Get detailed status, URLs, error logs, and current progress for a specific job ID.",
        "parameters": {
            "job_id": "The job ID to inspect (required)."
        }
    },
    {
        "name": "list_job_files",
        "description": "Inspect and list all downloaded files currently inside a job's sandboxed directory (filenames, sizes, types).",
        "parameters": {
            "job_id": "The job ID whose downloaded files to inspect (required)."
        }
    },
    {
        "name": "unzip_files",
        "description": "Unzip or extract archive files (ZIP, RAR, 7Z, TAR, GZ). Can immediately extract a job's downloaded archive or enable automatic unzipping for future downloads.",
        "parameters": {
            "job_id": "Optional job ID to unzip immediately. If omitted, targets user's latest job.",
            "enable_auto_unzip": "Optional boolean: set true to enable automatic archive extraction for all future downloads."
        }
    },
    {
        "name": "delete_job_files",
        "description": "Delete downloaded files for a specific job ID from the server disk to save space.",
        "parameters": {
            "job_id": "The job ID whose files should be deleted. If omitted, targets user's latest finished/failed job."
        }
    },
    {
        "name": "cancel_job",
        "description": "Cancel a running or queued job and delete its temporary files.",
        "parameters": {
            "job_id": "The job ID to cancel. If omitted, attempts to cancel user's active running or queued job."
        }
    },
    {
        "name": "clean_disk",
        "description": "Scan downloads directory and delete stale/orphaned folders to free up disk storage space.",
        "parameters": {}
    },
    {
        "name": "get_system_stats",
        "description": "Get current server disk usage, active workers, queue size, total jobs, and database stats.",
        "parameters": {}
    },
    {
        "name": "get_user_settings",
        "description": "View current user preferences (archive_mode, image_pdf, video_thumbs).",
        "parameters": {}
    },
    {
        "name": "update_user_setting",
        "description": "Change a user preference setting.",
        "parameters": {
            "key": "Setting name: 'archive_mode', 'image_pdf', 'video_thumbs', or 'terabox_cookie'.",
            "value": "New value. For archive_mode: 'extract', 'archive', or 'ask'. For image_pdf/video_thumbs: true or false. For terabox_cookie: string ndus cookie value."
        }
    },
    {
        "name": "clear_cache",
        "description": "Clear duplicate link cache so any previously downloaded MEGA, MediaFire, MP4Upload, or TeraBox link can be processed again immediately.",
        "parameters": {}
    },
    {
        "name": "get_account_info",
        "description": "Check if user has a custom MEGA account logged in.",
        "parameters": {}
    },
    {
        "name": "logout_mega_account",
        "description": "Log out user from their custom MEGA account and remove saved session credentials.",
        "parameters": {}
    },
    {
        "name": "set_terabox_cookie",
        "description": "Set or update the TeraBox session cookie (ndus) directly for high-speed downloads without editing files on the server. Validates with TeraBox servers and saves to MongoDB. Bot owner can set it globally for all users.",
        "parameters": {
            "cookie": "The TeraBox ndus cookie value (required).",
            "scope": "Optional scope: 'global' (bot-wide for all users, owner only) or 'personal' (current user only). Default is 'global' for owner, 'personal' for users."
        }
    },
    {
        "name": "clear_conversation_memory",
        "description": "Clear and wipe the saved conversation history / memory for the current user. Use this when the user asks to forget previous chats, clear memory, or start a fresh conversation.",
        "parameters": {}
    },
    {
        "name": "summarize_memory",
        "description": "Compact long conversation history into a short summary to save context tokens while preserving key facts. Use when memory grows large or before a long task.",
        "parameters": {
            "keep_last": "Number of recent turns to keep verbatim (default 6, max 20)."
        }
    },
    {
        "name": "get_agent_state",
        "description": "Inspect the AI agent's runtime state: active model, provider, memory size, queued/running jobs. Use for self-diagnostics and status answers.",
        "parameters": {}
    }
]


# ── OpenClaw-level tool metadata: risk tiers + JSON schemas ──────────
# risk: safe (read-only) | caution (writes/queues) | destructive (deletes/cancels)
TOOL_METADATA: dict[str, dict] = {
    "start_download": {"risk": "caution", "owner_only": False, "requires_confirmation": False},
    "get_ai_config": {"risk": "safe", "owner_only": False, "requires_confirmation": False},
    "update_ai_config": {"risk": "caution", "owner_only": True, "requires_confirmation": False},
    "test_ai_connection": {"risk": "safe", "owner_only": False, "requires_confirmation": False},
    "list_jobs": {"risk": "safe", "owner_only": False, "requires_confirmation": False},
    "get_job_details": {"risk": "safe", "owner_only": False, "requires_confirmation": False},
    "list_job_files": {"risk": "safe", "owner_only": False, "requires_confirmation": False},
    "unzip_files": {"risk": "caution", "owner_only": False, "requires_confirmation": False},
    "delete_job_files": {"risk": "destructive", "owner_only": False, "requires_confirmation": True},
    "cancel_job": {"risk": "destructive", "owner_only": False, "requires_confirmation": True},
    "clean_disk": {"risk": "destructive", "owner_only": False, "requires_confirmation": True},
    "get_system_stats": {"risk": "safe", "owner_only": False, "requires_confirmation": False},
    "get_user_settings": {"risk": "safe", "owner_only": False, "requires_confirmation": False},
    "update_user_setting": {"risk": "caution", "owner_only": False, "requires_confirmation": False},
    "clear_cache": {"risk": "caution", "owner_only": False, "requires_confirmation": False},
    "get_account_info": {"risk": "safe", "owner_only": False, "requires_confirmation": False},
    "logout_mega_account": {"risk": "caution", "owner_only": False, "requires_confirmation": False},
    "set_terabox_cookie": {"risk": "caution", "owner_only": False, "requires_confirmation": False},
    "clear_conversation_memory": {"risk": "caution", "owner_only": False, "requires_confirmation": False},
    "summarize_memory": {"risk": "safe", "owner_only": False, "requires_confirmation": False},
    "get_agent_state": {"risk": "safe", "owner_only": False, "requires_confirmation": False},
}

# JSON-Schema input specs for native function-calling (OpenAI `tools` parameter).
# Kept separate from legacy `parameters` dicts so existing tests stay green.
TOOL_SCHEMAS: dict[str, dict] = {
    "start_download": {
        "type": "object",
        "properties": {
            "urls": {"type": "string", "description": "One URL or free text containing URLs"},
            "instruction": {"type": "string", "description": "Processing instructions"},
        },
        "required": ["urls"],
    },
    "get_ai_config": {"type": "object", "properties": {}},
    "update_ai_config": {
        "type": "object",
        "properties": {
            "key": {"type": "string", "enum": ["model", "api_key", "provider", "base_url", "temperature"]},
            "value": {"type": "string"},
        },
        "required": ["key", "value"],
    },
    "test_ai_connection": {"type": "object", "properties": {}},
    "list_jobs": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "default": "all"},
            "limit": {"type": "integer", "default": 5, "minimum": 1, "maximum": 10},
        },
    },
    "get_job_details": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    "list_job_files": {"type": "object", "properties": {"job_id": {"type": "string"}}, "required": ["job_id"]},
    "unzip_files": {
        "type": "object",
        "properties": {
            "job_id": {"type": "string"},
            "enable_auto_unzip": {"type": "boolean"},
        },
    },
    "delete_job_files": {"type": "object", "properties": {"job_id": {"type": "string"}}},
    "cancel_job": {"type": "object", "properties": {"job_id": {"type": "string"}}},
    "clean_disk": {"type": "object", "properties": {}},
    "get_system_stats": {"type": "object", "properties": {}},
    "get_user_settings": {"type": "object", "properties": {}},
    "update_user_setting": {
        "type": "object",
        "properties": {"key": {"type": "string"}, "value": {}},
        "required": ["key", "value"],
    },
    "clear_cache": {"type": "object", "properties": {}},
    "get_account_info": {"type": "object", "properties": {}},
    "logout_mega_account": {"type": "object", "properties": {}},
    "set_terabox_cookie": {
        "type": "object",
        "properties": {"cookie": {"type": "string"}, "scope": {"type": "string"}},
        "required": ["cookie"],
    },
    "clear_conversation_memory": {"type": "object", "properties": {}},
    "summarize_memory": {
        "type": "object",
        "properties": {"keep_last": {"type": "integer", "default": 6, "minimum": 2, "maximum": 20}},
    },
    "get_agent_state": {"type": "object", "properties": {}},
}


def get_tool_def(name: str) -> dict | None:
    for t in TOOL_DEFINITIONS:
        if t.get("name") == name:
            return t
    return None


def get_tool_metadata(name: str) -> dict:
    return TOOL_METADATA.get(name, {"risk": "caution", "owner_only": False,
                                   "requires_confirmation": False})


def to_openai_tools() -> list[dict]:
    """Convert registry to OpenAI function-calling schema."""
    out = []
    for t in TOOL_DEFINITIONS:
        name = t["name"]
        out.append({
            "type": "function",
            "function": {
                "name": name,
                "description": t.get("description", ""),
                "parameters": TOOL_SCHEMAS.get(name, {"type": "object", "properties": {}}),
            },
        })
    return out


def validate_tool_args(tool_name: str, params: dict) -> tuple[bool, dict | str]:
    """Lightweight argument validation + normalization. Returns (ok, cleaned|error)."""
    params = dict(params or {})
    schema = TOOL_SCHEMAS.get(tool_name, {})
    for req in schema.get("required", []):
        if req not in params or params[req] in (None, ""):
            # start_download accepts `urls` as str or list; be lenient on empty check
            if tool_name == "start_download" and req == "urls" and params.get("urls"):
                continue
            return False, f"Missing required parameter '{req}' for tool '{tool_name}'."
    if tool_name == "list_jobs":
        try:
            lim = int(params.get("limit", 5))
        except (TypeError, ValueError):
            lim = 5
        params["limit"] = max(1, min(lim, 10))
    if tool_name == "summarize_memory":
        try:
            k = int(params.get("keep_last", 6))
        except (TypeError, ValueError):
            k = 6
        params["keep_last"] = max(2, min(k, 20))
    if tool_name == "update_ai_config":
        if str(params.get("key", "")).lower() not in ("model", "api_key", "provider", "base_url", "temperature"):
            return False, "Invalid key. Allowed: model, api_key, provider, base_url, temperature."
    return True, params


async def execute_tool(tool_name: str, params: dict, context: dict) -> dict:
    """
    Execute a tool safely using the provided context (user_id, is_owner, client, chat_id).
    Returns a dictionary with execution results.
    """
    user_id = context.get("user_id")
    is_owner = context.get("is_owner", False)
    client = context.get("client")
    chat_id = context.get("chat_id")

    import time as _t
    _started = _t.time()
    try:
        # ── OpenClaw-level: validate args + enforce owner-only ──────
        if get_tool_def(tool_name) is not None:
            ok, cleaned = validate_tool_args(tool_name, params or {})
            if not ok:
                return {"status": "error", "message": cleaned}
            params = cleaned
            meta = get_tool_metadata(tool_name)
            if meta.get("owner_only") and not is_owner and tool_name == "update_ai_config":
                return {"status": "error",
                        "message": "Only the bot owner can change global AI configuration."}
        # ── 1. start_download ────────────────────────────────
        if tool_name == "start_download":
            raw_urls = params.get("urls")
            if not raw_urls:
                return {"status": "error", "message": "No URLs provided to download."}

            if isinstance(raw_urls, str):
                urls = extract_supported_links(raw_urls) or [raw_urls.strip()]
            elif isinstance(raw_urls, list):
                urls = []
                for u in raw_urls:
                    urls.extend(extract_supported_links(str(u)) or [str(u).strip()])
            else:
                return {"status": "error", "message": "Invalid format for URLs."}

            # Filter valid URLs
            valid_urls = [u for u in urls if is_supported_link(u)]
            if not valid_urls:
                return {
                    "status": "error",
                    "message": "No valid MEGA, MediaFire, MP4Upload, or TeraBox links found. Links must start with mega.nz, mediafire.com, mp4upload.com, or terabox/1024tera."
                }

            valid_urls = valid_urls[:5]  # limit 5 per batch

            # Check active jobs limit
            if user_id:
                active = await db.active_jobs_for_user(user_id)
                if active >= MAX_JOBS_PER_USER:
                    return {
                        "status": "error",
                        "message": f"User already has {active} active job(s). Maximum allowed is {MAX_JOBS_PER_USER}. Wait for one to finish."
                    }

            # Check folder link validity
            from megabot.downloaders.mega_raw import RawMega
            for u in valid_urls:
                if "mega." in u and "/folder/" in u and not RawMega.parse_folder_url(u):
                    return {
                        "status": "error",
                        "message": f"MEGA folder link '{u}' is truncated. It requires both folder ID and decryption key (#key)."
                    }

            # Check duplicate cache
            for u in valid_urls:
                key = get_link_key(u)
                cached = await db.get_cached_link(key)
                if cached:
                    c_jid = cached.get("job_id")
                    if c_jid:
                        job_doc = await db.get_job(c_jid)
                        if job_doc and job_doc.get("status") in ["queued", "downloading", "processing", "uploading"]:
                            return {
                                "status": "error",
                                "message": f"Link '{u}' is currently being processed in active job {c_jid}. Please wait for it to complete."
                            }

            instruction = str(params.get("instruction", "")).strip()
            job_id = uuid.uuid4().hex[:10]

            # Create status card message on Telegram if client & chat_id are present
            status_msg_id = 0
            if client and chat_id:
                try:
                    display_text = texts.status_queued(f"{len(valid_urls)} link(s)" if len(valid_urls) > 1 else valid_urls[0])
                    msg = await client.send_message(chat_id, display_text, disable_web_page_preview=True)
                    status_msg_id = msg.id
                except Exception as me:
                    log.warning("Could not send initial status card message: %s", me)

            job = await db.create_job(
                job_id,
                user_id=user_id or 0,
                chat_id=chat_id or 0,
                url=valid_urls if len(valid_urls) > 1 else valid_urls[0],
                message_id=status_msg_id,
                prompt=instruction
            )

            for u in valid_urls:
                await db.cache_link(get_link_key(u), {"job_id": job_id, "url": u})

            await job_queue.submit(job)

            return {
                "status": "success",
                "job_id": job_id,
                "urls": valid_urls,
                "instruction": instruction,
                "message": f"Job {job_id} queued successfully for {len(valid_urls)} link(s)."
            }

        # ── 2. list_jobs ─────────────────────────────────────
        elif tool_name == "list_jobs":
            status = params.get("status", "all")
            limit = min(int(params.get("limit", 5)), 10)
            target_user = None if is_owner else user_id
            jobs = await db.list_jobs(user_id=target_user, status=status, limit=limit)

            job_list = []
            for j in jobs:
                jid = j.get("_id")
                is_running = jid in job_queue.running
                st = "running" if is_running else j.get("status")
                raw_u = j.get("url")
                urls = raw_u if isinstance(raw_u, list) else [raw_u]
                job_list.append({
                    "job_id": jid,
                    "status": st,
                    "urls": urls,
                    "created_at": str(j.get("created_at")),
                    "files_sent": j.get("files_sent", 0),
                    "prompt": j.get("prompt", ""),
                    "error": j.get("error"),
                })
            return {"status": "success", "count": len(job_list), "jobs": job_list}

        # ── 3. get_job_details ───────────────────────────────
        elif tool_name == "get_job_details":
            job_id = str(params.get("job_id", "")).strip()
            if not job_id:
                return {"status": "error", "message": "job_id is required"}

            job = await db.get_job(job_id)
            if not job:
                return {"status": "error", "message": f"Job {job_id} not found."}
            if not is_owner and job.get("user_id") != user_id:
                return {"status": "error", "message": "Permission denied: not your job."}

            is_running = job_id in job_queue.running
            job_dir = os.path.join(DOWNLOAD_DIR, job_id)
            has_files_on_disk = os.path.exists(job_dir)

            return {
                "status": "success",
                "job_id": job_id,
                "current_status": "running" if is_running else job.get("status"),
                "urls": job.get("url"),
                "prompt": job.get("prompt", ""),
                "files_sent": job.get("files_sent", 0),
                "has_files_on_disk": has_files_on_disk,
                "error": job.get("error"),
                "created_at": str(job.get("created_at")),
            }

        # ── 4. list_job_files ────────────────────────────────
        elif tool_name == "list_job_files":
            job_id = str(params.get("job_id", "")).strip()
            if not job_id:
                return {"status": "error", "message": "job_id is required"}

            job = await db.get_job(job_id)
            if job and not is_owner and job.get("user_id") != user_id:
                return {"status": "error", "message": "Permission denied: not your job."}

            job_dir = os.path.join(DOWNLOAD_DIR, job_id)
            if not os.path.isdir(job_dir):
                return {"status": "success", "job_id": job_id, "file_count": 0, "files": [], "message": f"No files on disk for job {job_id}."}

            found_files = []
            for root, _, files in os.walk(job_dir):
                for f in files:
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, job_dir)
                    try:
                        sz = os.path.getsize(full)
                        found_files.append({
                            "path": rel,
                            "filename": f,
                            "size_bytes": sz,
                            "size_mb": round(sz / (1024 * 1024), 2),
                            "ext": os.path.splitext(f)[1].lower()
                        })
                    except Exception:
                        pass

            return {"status": "success", "job_id": job_id, "file_count": len(found_files), "files": found_files}

        # ── 5. cancel_job ────────────────────────────────────
        elif tool_name == "cancel_job":
            job_id = str(params.get("job_id", "")).strip()
            if not job_id and user_id:
                # Target user's active job automatically
                active_jobs = await db.list_jobs(user_id=user_id, limit=5)
                for j in active_jobs:
                    if j.get("status") in ["queued", "downloading", "processing", "uploading"]:
                        job_id = j.get("_id")
                        break

            if not job_id:
                return {"status": "error", "message": "No job_id provided and no active job found."}

            job = await db.get_job(job_id)
            if not job:
                return {"status": "error", "message": f"Job {job_id} not found."}
            if not is_owner and job.get("user_id") != user_id:
                return {"status": "error", "message": "Permission denied: you can only cancel your own jobs."}

            cancelled = await job_queue.cancel_job(job_id)
            await db.set_job_status(job_id, "cancelled")

            job_dir = os.path.join(DOWNLOAD_DIR, job_id)
            if os.path.exists(job_dir):
                shutil.rmtree(job_dir, ignore_errors=True)

            return {
                "status": "success",
                "job_id": job_id,
                "message": f"Job {job_id} has been cancelled and its workspace cleaned up.",
                "was_running": cancelled
            }

        # ── 6. delete_job_files ──────────────────────────────
        elif tool_name == "delete_job_files":
            job_id = str(params.get("job_id", "")).strip()
            if not job_id and user_id:
                # Target latest job
                user_jobs = await db.list_jobs(user_id=user_id, limit=1)
                if user_jobs:
                    job_id = user_jobs[0].get("_id")

            if not job_id:
                return {"status": "error", "message": "job_id is required"}

            job = await db.get_job(job_id)
            if job and not is_owner and job.get("user_id") != user_id:
                return {"status": "error", "message": "Permission denied: not your job files."}

            job_dir = os.path.join(DOWNLOAD_DIR, job_id)
            if not os.path.exists(job_dir):
                return {"status": "success", "message": f"No files found for job {job_id} on disk (already cleaned)."}

            size_mb = 0.0
            for root, _, files in os.walk(job_dir):
                for f in files:
                    try:
                        size_mb += os.path.getsize(os.path.join(root, f)) / (1024 * 1024)
                    except Exception:
                        pass

            shutil.rmtree(job_dir, ignore_errors=True)
            return {
                "status": "success",
                "job_id": job_id,
                "message": f"Successfully deleted files for job {job_id}.",
                "freed_mb": round(size_mb, 2)
            }

        # ── 7. clean_disk ────────────────────────────────────
        elif tool_name == "clean_disk":
            if not os.path.isdir(DOWNLOAD_DIR):
                return {"status": "success", "freed_mb": 0, "cleaned_folders": 0}

            cleaned = 0
            total_freed_mb = 0.0
            active_ids = set(job_queue.running.keys())

            for name in os.listdir(DOWNLOAD_DIR):
                if name in active_ids:
                    continue  # do not touch active jobs
                folder = os.path.join(DOWNLOAD_DIR, name)
                if not os.path.isdir(folder):
                    continue

                for root, _, files in os.walk(folder):
                    for f in files:
                        try:
                            total_freed_mb += os.path.getsize(os.path.join(root, f)) / (1024 * 1024)
                        except Exception:
                            pass

                shutil.rmtree(folder, ignore_errors=True)
                cleaned += 1

            return {
                "status": "success",
                "cleaned_folders": cleaned,
                "freed_mb": round(total_freed_mb, 2),
                "message": f"Cleaned {cleaned} stale directories, freeing {round(total_freed_mb, 2)} MB of disk space."
            }

        # ── 8. get_system_stats ──────────────────────────────
        elif tool_name == "get_system_stats":
            total_disk_mb, used_disk_mb, free_disk_mb = 0, 0, 0
            try:
                stat = shutil.disk_usage(DOWNLOAD_DIR if os.path.exists(DOWNLOAD_DIR) else ".")
                total_disk_mb = round(stat.total / (1024 * 1024), 1)
                used_disk_mb = round(stat.used / (1024 * 1024), 1)
                free_disk_mb = round(stat.free / (1024 * 1024), 1)
            except Exception:
                pass

            total_jobs = await db.count_jobs()
            queued_jobs = await db.count_jobs("queued")
            active_jobs = len(job_queue.running)

            return {
                "status": "success",
                "disk": {
                    "free_mb": free_disk_mb,
                    "used_mb": used_disk_mb,
                    "total_mb": total_disk_mb,
                },
                "queue": {
                    "active_running": active_jobs,
                    "waiting": queued_jobs,
                },
                "jobs_total": total_jobs,
            }

        # ── 9. get_user_settings ─────────────────────────────
        elif tool_name == "get_user_settings":
            archive_mode = await db.get_user_setting(user_id, "archive_mode")
            image_pdf = await db.get_user_setting(user_id, "image_pdf")
            video_thumbs = await db.get_user_setting(user_id, "video_thumbs")
            return {
                "status": "success",
                "settings": {
                    "archive_mode": archive_mode,
                    "image_pdf": image_pdf,
                    "video_thumbs": video_thumbs,
                }
            }

        # ── 10. update_user_setting ──────────────────────────
        elif tool_name == "update_user_setting":
            key = params.get("key")
            val = params.get("value")
            if key not in ["archive_mode", "image_pdf", "video_thumbs", "terabox_cookie"]:
                return {"status": "error", "message": f"Invalid setting key '{key}'. Must be archive_mode, image_pdf, video_thumbs, or terabox_cookie."}

            if key == "archive_mode":
                val = str(val).lower()
                if val not in ["extract", "archive", "ask"]:
                    return {"status": "error", "message": "archive_mode must be 'extract', 'archive', or 'ask'."}
            elif key in ["image_pdf", "video_thumbs"]:
                if isinstance(val, str):
                    val = val.lower() in ["true", "1", "yes", "on"]
                else:
                    val = bool(val)
            elif key == "terabox_cookie":
                val = str(val).strip()

            await db.set_user_setting(user_id, key, val)
            return {"status": "success", "message": f"Setting '{key}' successfully updated."}

        # ── 11. unzip_files ──────────────────────────────────
        elif tool_name == "unzip_files":
            if user_id:
                await db.set_user_setting(user_id, "archive_mode", "extract")

            client = context.get("client")
            chat_id = context.get("chat_id")
            message = context.get("message")

            # 1. Did the user reply to a Telegram message with media?
            if message and message.reply_to_message and client and chat_id:
                rm = message.reply_to_message
                file_name = None
                file_size = 0
                if rm.document:
                    file_name = rm.document.file_name or "archive.zip"
                    file_size = rm.document.file_size or 0
                elif rm.video:
                    file_name = rm.video.file_name or "video.mp4"
                    file_size = rm.video.file_size or 0
                elif rm.audio:
                    file_name = rm.audio.file_name or "audio.mp3"
                    file_size = rm.audio.file_size or 0
                elif rm.photo:
                    file_name = "photo.jpg"
                    file_size = rm.photo.file_size or 0

                if file_name:
                    new_job_id = uuid.uuid4().hex[:10]
                    status_card = await client.send_message(
                        chat_id,
                        texts.status_queued(f"Telegram file: {file_name}"),
                        disable_web_page_preview=True
                    )
                    new_job = await db.create_job(
                        new_job_id,
                        user_id=user_id,
                        chat_id=chat_id,
                        url=f"tg://media/{rm.id}",
                        message_id=status_card.id,
                        prompt="extract archive",
                    )
                    new_job["media_message_id"] = rm.id
                    new_job["media_file_name"] = file_name
                    new_job["media_file_size"] = file_size
                    new_job["is_telegram_media"] = True
                    new_job["_tg_message"] = rm
                    await job_queue.submit(new_job)

                    return {
                        "status": "success",
                        "job_id": new_job_id,
                        "message": f"🚀 Started extraction for replied file '{file_name}' (Job ID: <code>{new_job_id}</code>). The unzipped files will be delivered shortly."
                    }

            # 2. Check if a specified or active job folder exists on disk
            job_id = str(params.get("job_id", "")).strip()
            recent_jobs = await db.list_jobs(user_id=user_id, limit=5) if user_id else []
            if not job_id:
                for j in recent_jobs:
                    test_dir = os.path.join(DOWNLOAD_DIR, j["_id"])
                    if os.path.isdir(test_dir):
                        job_id = j["_id"]
                        break

            if job_id:
                job_dir = os.path.join(DOWNLOAD_DIR, job_id)
                if os.path.isdir(job_dir):
                    from megabot.processors.archives import safe_extract
                    from megabot.analyzers.classify import is_archive_file
                    extracted_count = 0
                    for root, _, files in os.walk(job_dir):
                        for f in files:
                            arc_path = os.path.join(root, f)
                            if is_archive_file(arc_path):
                                out = os.path.join(job_dir, "extracted")
                                try:
                                    safe_extract(arc_path, out)
                                    extracted_files = [
                                        os.path.join(dp, fn)
                                        for dp, _, fns in os.walk(out)
                                        for fn in fns
                                        if os.path.isfile(os.path.join(dp, fn)) and os.path.getsize(os.path.join(dp, fn)) > 0
                                    ]
                                    if extracted_files:
                                        try:
                                            os.remove(arc_path)
                                        except Exception:
                                            pass
                                        extracted_count += 1
                                    else:
                                        shutil.rmtree(out, ignore_errors=True)
                                except Exception as ee:
                                    log.warning("Tool unzip failed on %s: %s", arc_path, ee)
                                    shutil.rmtree(out, ignore_errors=True)
                    if extracted_count:
                        return {
                            "status": "success",
                            "message": f"✅ Successfully unzipped {extracted_count} archive(s) in active workspace (Job ID: <code>{job_id}</code>)."
                        }

            # 3. If disk was cleaned up, check recent jobs to re-download & extract
            if recent_jobs and client and chat_id:
                last_job = recent_jobs[0]
                if last_job.get("is_telegram_media") or str(last_job.get("url", "")).startswith("tg://media/"):
                    media_msg_id = last_job.get("media_message_id")
                    if not media_msg_id and str(last_job.get("url", "")).startswith("tg://media/"):
                        try:
                            media_msg_id = int(str(last_job.get("url")).split("/")[-1])
                        except Exception:
                            media_msg_id = None

                    file_name = last_job.get("media_file_name", "archive.zip")
                    new_job_id = uuid.uuid4().hex[:10]
                    status_card = await client.send_message(
                        chat_id,
                        texts.status_queued(f"Telegram file: {file_name}"),
                        disable_web_page_preview=True
                    )
                    new_job = await db.create_job(
                        new_job_id,
                        user_id=user_id,
                        chat_id=chat_id,
                        url=last_job.get("url", f"tg://media/{media_msg_id}"),
                        message_id=status_card.id,
                        prompt="extract archive",
                    )
                    new_job["media_message_id"] = media_msg_id
                    new_job["media_file_name"] = file_name
                    new_job["media_file_size"] = last_job.get("media_file_size", 0)
                    new_job["is_telegram_media"] = True
                    await job_queue.submit(new_job)

                    return {
                        "status": "success",
                        "job_id": new_job_id,
                        "message": f"🚀 Re-downloading and extracting '{file_name}' from your recent upload (Job ID: <code>{new_job_id}</code>). Extracted files will be sent shortly."
                    }

                elif last_job.get("url") and not str(last_job.get("url")).startswith("tg://"):
                    url = last_job["url"]
                    new_job_id = uuid.uuid4().hex[:10]
                    display_url = url if isinstance(url, str) else f"{len(url)} links"
                    status_card = await client.send_message(
                        chat_id,
                        texts.status_queued(display_url),
                        disable_web_page_preview=True
                    )
                    new_job = await db.create_job(
                        new_job_id,
                        user_id=user_id,
                        chat_id=chat_id,
                        url=url,
                        message_id=status_card.id,
                        prompt="extract archive",
                    )
                    await job_queue.submit(new_job)

                    return {
                        "status": "success",
                        "job_id": new_job_id,
                        "message": f"🚀 Re-queuing download and extraction for '{display_url}' (Job ID: <code>{new_job_id}</code>). Extracted files will be sent shortly."
                    }

            # 4. Fallback if no recent jobs found
            return {
                "status": "success",
                "message": "✅ <b>Automatic archive extraction is active!</b>\n\nAll zip, rar, 7z, and tar files you upload will now be unzipped automatically. You can also reply directly to any archive file with <i>'unzip'</i> to extract it."
            }

        # ── 12. clear_cache ──────────────────────────────────
        elif tool_name == "clear_cache":
            deleted = await db.clear_link_cache()
            return {"status": "success", "cleared_entries": deleted, "message": f"Cleared {deleted} cached link entries. All links can now be processed anew."}

        # ── 13. get_account_info ─────────────────────────────
        elif tool_name == "get_account_info":
            account = await db.get_mega_account(user_id)
            if not account:
                return {"status": "success", "logged_in": False, "message": "Using anonymous high-speed guest downloader."}
            email = account.get("email", "")
            masked = email[:3] + "***@" + email.split("@")[-1] if "@" in email else "user"
            return {"status": "success", "logged_in": True, "email_masked": masked}

        # ── 14. logout_mega_account ──────────────────────────
        elif tool_name == "logout_mega_account":
            await db.delete_mega_account(user_id)
            await db.delete_mega_session(user_id)
            return {"status": "success", "message": "Successfully logged out of custom MEGA account."}

        # ── 15. set_terabox_cookie ───────────────────────────
        elif tool_name == "set_terabox_cookie":
            raw_cookie = str(params.get("cookie", "")).strip()
            if not raw_cookie:
                return {"status": "error", "message": "cookie parameter is required."}

            from megabot.downloaders.terabox import _normalize_cookie, check_cookie_validity
            norm = _normalize_cookie(raw_cookie)
            if len(norm) < 10:
                return {"status": "error", "message": "Cookie seems invalid or too short. Please provide the full ndus value."}

            check = check_cookie_validity(norm)
            scope = str(params.get("scope", "global" if is_owner else "personal")).lower()

            if is_owner and scope == "global":
                await db.set_config("terabox_cookie", norm)
                if user_id:
                    await db.set_user_setting(user_id, "terabox_cookie", norm)
                scope_str = "Global (Active for all bot users)"
            else:
                if user_id:
                    await db.set_user_setting(user_id, "terabox_cookie", norm)
                scope_str = "Personal"

            uname = check.get("username") or "TeraBox User"
            valid_note = "Active & Verified ✅" if check.get("valid") else f"Saved with notice: {check.get('message')}"

            return {
                "status": "success",
                "scope": scope_str,
                "account": uname,
                "message": f"TeraBox session cookie successfully saved in MongoDB. Scope: {scope_str}. Account: {uname}. Status: {valid_note}.",
            }

        # ── 16. get_ai_config ────────────────────────────────
        elif tool_name == "get_ai_config":
            from megabot.ai.client import get_ai_config
            cfg = await get_ai_config(user_id)
            masked_key = (cfg["api_key"][:6] + "..." + cfg["api_key"][-4:]) if cfg["api_key"] else "Not configured"
            return {
                "status": "success",
                "provider": cfg["provider_name"],
                "model": cfg["model"],
                "base_url": cfg["base_url"],
                "temperature": cfg["temperature"],
                "api_key_status": masked_key,
                "is_active": cfg["is_configured"],
            }

        # ── 17. update_ai_config ─────────────────────────────
        elif tool_name == "update_ai_config":
            key = str(params.get("key", "")).strip().lower()
            val = params.get("value")
            if not key or val is None:
                return {"status": "error", "message": "Both 'key' and 'value' parameters are required."}

            allowed_keys = ["model", "api_key", "provider", "base_url", "temperature"]
            if key not in allowed_keys:
                return {"status": "error", "message": f"Invalid key '{key}'. Allowed keys: {', '.join(allowed_keys)}"}

            from megabot.ai.client import set_ai_config, PROVIDER_PRESETS
            if key == "temperature":
                try:
                    val = float(val)
                except ValueError:
                    return {"status": "error", "message": "Temperature must be a number between 0.0 and 2.0"}

            if key == "provider":
                val_s = str(val).strip()
                val_l = val_s.lower()
                if val_l in PROVIDER_PRESETS:
                    await set_ai_config("provider", val_l)
                    await set_ai_config("base_url", PROVIDER_PRESETS[val_l]["base_url"])
                    await set_ai_config("model", PROVIDER_PRESETS[val_l]["default_model"])
                    return {
                        "status": "success",
                        "message": f"AI provider switched to {PROVIDER_PRESETS[val_l]['name']}. Default model set to {PROVIDER_PRESETS[val_l]['default_model']}."
                    }
                elif val_s.startswith("http://") or val_s.startswith("https://"):
                    await set_ai_config("provider", "custom")
                    await set_ai_config("base_url", val_s)
                    return {
                        "status": "success",
                        "message": f"Custom OpenAI-compatible base URL set to {val_s}."
                    }
                else:
                    await set_ai_config("provider", val_s)
                    return {
                        "status": "success",
                        "message": f"AI provider set to '{val_s}'. Configure base_url and model as needed."
                    }

            if key == "base_url":
                val = str(val).strip()

            await set_ai_config(key, val)
            return {
                "status": "success",
                "key": key,
                "value": "******" if key == "api_key" else val,
                "message": f"Successfully updated AI setting '{key}' to {val if key != 'api_key' else '[hidden]'}."
            }

        # ── 18. test_ai_connection ───────────────────────────
        elif tool_name == "test_ai_connection":
            from megabot.ai.client import test_ai_connection
            res = await test_ai_connection()
            return res

        # ── 19. clear_conversation_memory ────────────────────
        elif tool_name == "clear_conversation_memory":
            if not user_id:
                return {"status": "error", "message": "User ID is required to clear conversation memory."}
            count = await db.clear_conversation_history(user_id)
            return {
                "status": "success",
                "message": f"Successfully cleared conversation memory ({count} message(s) forgotten).",
                "count": count,
            }

        # ── 20. summarize_memory (OpenClaw-level compaction) ───
        elif tool_name == "summarize_memory":
            if not user_id:
                return {"status": "error", "message": "User ID is required to summarize memory."}
            keep_last = int(params.get("keep_last", 6))
            try:
                from megabot.ai.memory import compact_memory
                res = await compact_memory(user_id, keep_last=keep_last)
                return {"status": "success", **res}
            except Exception as e:
                return {"status": "error", "message": f"Memory compaction failed: {e}"}

        # ── 21. get_agent_state (OpenClaw-level observability) ─
        elif tool_name == "get_agent_state":
            from megabot.ai.client import get_ai_config as _get_cfg
            cfg = await _get_cfg(user_id)
            masked = (cfg["api_key"][:6] + "..." + cfg["api_key"][-4:]) if cfg["api_key"] else "Not configured"
            history = await db.get_conversation_history(user_id or 0, limit=50)
            total_jobs = await db.count_jobs()
            return {
                "status": "success",
                "provider": cfg["provider_name"],
                "model": cfg["model"],
                "temperature": cfg["temperature"],
                "api_key_status": masked,
                "is_active": cfg["is_configured"],
                "memory_turns": len(history),
                "jobs_total": total_jobs,
                "running_jobs": len(job_queue.running),
            }

        else:
            return {"status": "error", "message": f"Unknown tool: '{tool_name}'"}

    except Exception as e:
        log.exception("Tool execution error in %s", tool_name)
        return {"status": "error", "message": f"Internal tool execution error: {e}"}
