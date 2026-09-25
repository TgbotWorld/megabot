# MongoDB layer — motor async singleton (pattern mirrors AniwatchTvdl/cantarella/core/database.py)
import functools
import logging
from collections import defaultdict
from datetime import datetime, timedelta

import motor.motor_asyncio

try:
    from pymongo.errors import PyMongoError
except ImportError:  # pragma: no cover — motor always pulls pymongo
    PyMongoError = Exception

from config import MONGO_URL, MONGO_NAME, LINK_CACHE_TTL_H

logging.basicConfig(level=logging.INFO)


def _mongo_safe(fn):
    """Graceful degradation: if MongoDB becomes unreachable mid-run (e.g.
    MONGO_URL points at localhost inside Docker with no mongo service),
    kill the collections once and retry on the in-memory/None path so
    downloads keep working instead of failing every job instantly."""
    @functools.wraps(fn)
    async def wrapper(self, *args, **kwargs):
        try:
            return await fn(self, *args, **kwargs)
        except PyMongoError as e:
            if not getattr(self, "_mongo_dead", False):
                self._mongo_dead = True
                logging.error(
                    "MongoDB unreachable (%s) — falling back to in-memory mode. "
                    "Fix MONGO_URL (use a reachable host, not container-localhost).", e)
                for attr in ("users", "jobs", "settings", "link_cache",
                             "mega_accounts", "mega_sessions", "conversations", "db"):
                    try:
                        setattr(self, attr, None)
                    except Exception:
                        pass
                return await fn(self, *args, **kwargs)
            raise
    return wrapper


class Database:
    def __init__(self, uri, db_name=MONGO_NAME):
        if uri:
            try:
                self.client = motor.motor_asyncio.AsyncIOMotorClient(
                    uri, serverSelectionTimeoutMS=5000
                )
                self.db = self.client[db_name]

                # ── Collections ──────────────────────────────────────
                self.users = self.db["users"]
                self.jobs = self.db["jobs"]
                self.settings = self.db["user_settings"]
                self.link_cache = self.db["link_cache"]
                self.mega_accounts = self.db["mega_accounts"]
                self.mega_sessions = self.db["mega_sessions"]
                self.conversations = self.db["conversations"]
            except Exception as e:
                self.client = self.db = None
                self.users = self.jobs = self.settings = self.link_cache = None
                self.mega_accounts = self.mega_sessions = None
                self.conversations = None
                logging.error("Failed to initialize MongoDB client: %s", e)
        else:
            # Graceful no-op when MONGO_URL is not set
            self.client = self.db = None
            self.users = self.jobs = self.settings = self.link_cache = None
            self.mega_accounts = None
            self.mega_sessions = None
            self.conversations = None
            logging.warning("MONGO_URL not set — database features will be disabled.")
        self._mem_config = {}
        self._mem_conversations = defaultdict(list)

    # ══════════════════════════════════════════════════════
    #  USERS
    # ══════════════════════════════════════════════════════

    def _new_user(self, user_id: int, username: str = None) -> dict:
        return dict(
            _id=int(user_id),
            username=username,
            first_seen=datetime.utcnow(),
            active=True,
            total_jobs=0,
            ban_status=dict(is_banned=False, ban_reason=""),
        )

    @_mongo_safe
    async def add_user(self, user_id: int, username: str = None):
        if self.users is None:
            return
        if not await self.is_user_exist(user_id):
            if self.users is None:  # mongo may have died inside is_user_exist
                return
            await self.users.insert_one(self._new_user(user_id, username))
            logging.info("New user added: %s", user_id)
        else:
            await self.users.update_one(
                {"_id": int(user_id)},
                {"$set": {"active": True, "username": username}},
            )

    @_mongo_safe
    async def is_user_exist(self, user_id: int) -> bool:
        if self.users is None:
            return False
        return bool(await self.users.find_one({"_id": int(user_id)}))

    @_mongo_safe
    async def get_all_users(self):
        if self.users is None:
            return []
        return await self.users.find({}).to_list(None)

    @_mongo_safe
    async def total_users_count(self) -> int:
        if self.users is None:
            return 0
        return await self.users.count_documents({})

    @_mongo_safe
    async def is_user_banned(self, user_id: int) -> bool:
        if self.users is None:
            return False
        user = await self.users.find_one({"_id": int(user_id)})
        if user:
            return user.get("ban_status", {}).get("is_banned", False)
        return False

    @_mongo_safe
    async def set_ban(self, user_id: int, banned: bool, reason: str = ""):
        if self.users is not None:
            await self.users.update_one(
                {"_id": int(user_id)},
                {"$set": {"ban_status": {"is_banned": banned, "ban_reason": reason}}},
            )

    @_mongo_safe
    async def bump_user_jobs(self, user_id: int):
        if self.users is not None:
            await self.users.update_one(
                {"_id": int(user_id)}, {"$inc": {"total_jobs": 1}}
            )

    # ══════════════════════════════════════════════════════
    #  JOBS  (status machine: queued → downloading → processing
    #         → awaiting_choice → uploading → done / failed / cancelled)
    # ══════════════════════════════════════════════════════

    @_mongo_safe
    async def create_job(self, job_id: str, user_id: int, chat_id: int, url,
                         message_id: int, prompt: str = "") -> dict:
        """url may be a single URL string or a list of URLs (multi-volume)."""
        doc = dict(
            _id=job_id,
            user_id=user_id,
            chat_id=chat_id,
            url=url if isinstance(url, list) else [url],
            message_id=message_id,
            prompt=prompt,
            status="queued",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            error=None,
            files_sent=0,
            bytes_total=0,
        )
        if self.jobs is not None:
            await self.jobs.insert_one(doc)
        return doc

    @_mongo_safe
    async def get_job(self, job_id: str):
        if self.jobs is None:
            return None
        return await self.jobs.find_one({"_id": job_id})

    @_mongo_safe
    async def set_job_status(self, job_id: str, status: str, **extra):
        if self.jobs is not None:
            update = {"status": status, "updated_at": datetime.utcnow()}
            update.update(extra)
            await self.jobs.update_one({"_id": job_id}, {"$set": update})

    @_mongo_safe
    async def count_jobs(self, status: str = None) -> int:
        if self.jobs is None:
            return 0
        query = {"status": status} if status else {}
        return await self.jobs.count_documents(query)

    @_mongo_safe
    async def list_jobs(self, user_id: int = None, status: str = None, limit: int = 10) -> list[dict]:
        if self.jobs is None:
            return []
        query = {}
        if user_id:
            query["user_id"] = int(user_id)
        if status and status != "all":
            query["status"] = status
        try:
            cursor = self.jobs.find(query).sort("created_at", -1).limit(limit)
            return await cursor.to_list(length=limit)
        except Exception as e:
            log.warning("list_jobs error: %s", e)
            return []

    @_mongo_safe
    async def delete_job_record(self, job_id: str) -> bool:
        if self.jobs is None:
            return False
        res = await self.jobs.delete_one({"_id": job_id})
        return res.deleted_count > 0

    @_mongo_safe
    async def active_jobs_for_user(self, user_id: int) -> int:
        if self.jobs is None:
            return 0
        return await self.jobs.count_documents({
            "user_id": user_id,
            "status": {"$in": ["queued", "downloading", "processing",
                                "awaiting_choice", "uploading"]},
        })

    # ══════════════════════════════════════════════════════
    #  USER SETTINGS  (generic key-value per user)
    # ══════════════════════════════════════════════════════

    DEFAULT_SETTINGS = {
        "archive_mode": "extract",     # extract by default (unzip archives)
        "image_pdf": True,             # auto-merge image sets into PDF
        "video_thumbs": True,          # generate video thumbnails
    }

    @_mongo_safe
    async def get_user_setting(self, user_id: int, key: str):
        default = self.DEFAULT_SETTINGS.get(key)
        if self.settings is None:
            return default
        user = await self.settings.find_one({"_id": int(user_id)})
        if user and key in user:
            return user[key]
        return default

    @_mongo_safe
    async def set_user_setting(self, user_id: int, key: str, value):
        if self.settings is not None:
            await self.settings.update_one(
                {"_id": int(user_id)}, {"$set": {key: value}}, upsert=True
            )

    # ══════════════════════════════════════════════════════
    #  MEGA ACCOUNTS  (per-user MEGA login, saved by /login)
    #  NOTE: password is base64-obfuscated, NOT encrypted —
    #  the Mongo URI itself must stay secret.
    # ══════════════════════════════════════════════════════

    @_mongo_safe
    async def save_mega_account(self, user_id: int, email: str, password: str):
        if self.mega_accounts is None:
            return False
        import base64
        encoded = base64.b64encode(password.encode()).decode()
        await self.mega_accounts.update_one(
            {"_id": int(user_id)},
            {"$set": {"email": email, "password": encoded,
                      "saved_at": datetime.utcnow()}},
            upsert=True,
        )
        return True

    @_mongo_safe
    async def get_mega_account(self, user_id: int):
        """Return {"email": ..., "password": ...} or None."""
        if self.mega_accounts is None:
            return None
        import base64
        doc = await self.mega_accounts.find_one({"_id": int(user_id)})
        if not doc:
            return None
        try:
            password = base64.b64decode(doc["password"]).decode()
        except Exception:
            password = doc["password"]
        return {"email": doc["email"], "password": password}

    @_mongo_safe
    async def delete_mega_account(self, user_id: int) -> bool:
        if self.mega_accounts is None:
            return False
        result = await self.mega_accounts.delete_one({"_id": int(user_id)})
        return result.deleted_count > 0

    # ══════════════════════════════════════════════════════
    #  MEGA SESSIONS  (sid + master key cache — the anti-lockout
    #  mechanism: login happens once, the session is reused after)
    # ══════════════════════════════════════════════════════

    @_mongo_safe
    async def save_mega_session(self, user_id: int, sid: str, master_key: list):
        if self.mega_sessions is None:
            return
        await self.mega_sessions.update_one(
            {"_id": int(user_id)},
            {"$set": {"sid": sid, "master_key": list(master_key),
                      "saved_at": datetime.utcnow()}},
            upsert=True,
        )

    @_mongo_safe
    async def get_mega_session(self, user_id: int):
        """Return {"sid": ..., "master_key": [...]} or None."""
        if self.mega_sessions is None:
            return None
        return await self.mega_sessions.find_one({"_id": int(user_id)})

    @_mongo_safe
    async def delete_mega_session(self, user_id: int):
        if self.mega_sessions is not None:
            await self.mega_sessions.delete_one({"_id": int(user_id)})

    # ══════════════════════════════════════════════════════
    #  LINK CACHE  (dedup: same MEGA link within TTL hours)
    # ══════════════════════════════════════════════════════

    @_mongo_safe
    async def get_cached_link(self, node_key: str):
        if self.link_cache is None:
            return None
        doc = await self.link_cache.find_one({"_id": node_key})
        if doc and doc.get("cached_at") and \
                datetime.utcnow() - doc["cached_at"] < timedelta(hours=LINK_CACHE_TTL_H):
            return doc
        return None

    @_mongo_safe
    async def cache_link(self, node_key: str, meta: dict):
        if self.link_cache is not None:
            await self.link_cache.update_one(
                {"_id": node_key},
                {"$set": {**meta, "cached_at": datetime.utcnow()}},
                upsert=True,
            )

    @_mongo_safe
    async def clear_link_cache(self) -> int:
        if self.link_cache is None:
            return 0
        try:
            res = await self.link_cache.delete_many({})
            return res.deleted_count
        except Exception as e:
            log.warning("clear_link_cache error: %s", e)
            return 0

    @_mongo_safe
    async def get_db_stats(self):
        if self.db is None:
            return None
        try:
            stats = await self.db.command("dbStats")
            return {
                "data_size": stats.get("dataSize", 0),
                "storage_size": stats.get("storageSize", 0),
                "index_size": stats.get("indexSize", 0),
            }
        except Exception as e:
            logging.error("Error fetching DB stats: %s", e)
            return None

    # ══════════════════════════════════════════════════════
    #  BOT GLOBAL CONFIG  (persistent bot settings in Mongo)
    # ══════════════════════════════════════════════════════

    async def get_config(self, key: str, default=None):
        if self.db is None:
            return self._mem_config.get(key, default)
        try:
            doc = await self.db["bot_config"].find_one({"_id": key})
            return doc.get("value", default) if doc else default
        except Exception:
            return self._mem_config.get(key, default)

    async def set_config(self, key: str, value):
        self._mem_config[key] = value
        if self.db is not None:
            try:
                await self.db["bot_config"].update_one(
                    {"_id": key},
                    {"$set": {"value": value, "updated_at": datetime.utcnow()}},
                    upsert=True,
                )
            except Exception as e:
                logging.warning("Error saving config to MongoDB: %s", e)

    async def delete_config(self, key: str) -> bool:
        self._mem_config.pop(key, None)
        if self.db is None:
            return True
        try:
            res = await self.db["bot_config"].delete_one({"_id": key})
            return res.deleted_count > 0
        except Exception:
            return False

    # ══════════════════════════════════════════════════════
    #  CONVERSATIONS  (persistent multi-turn AI memory)
    # ══════════════════════════════════════════════════════

    async def add_conversation_message(self, user_id: int, role: str, content: str):
        """Record a conversation turn ('user' or 'assistant') for a user."""
        if not content:
            return
        doc = {
            "user_id": int(user_id),
            "role": role,
            "content": str(content)[:1000],
            "created_at": datetime.utcnow(),
        }
        uid = int(user_id)
        self._mem_conversations[uid].append(doc)
        if len(self._mem_conversations[uid]) > 50:
            self._mem_conversations[uid] = self._mem_conversations[uid][-50:]

        if self.conversations is not None:
            try:
                await self.conversations.insert_one(doc)
            except Exception as e:
                logging.warning("Error saving conversation turn to MongoDB: %s", e)

    async def get_conversation_history(self, user_id: int, limit: int = 10) -> list[dict]:
        """Get recent conversation turns for a user, ordered chronologically (oldest to newest)."""
        uid = int(user_id)
        if self.conversations is not None:
            try:
                cursor = self.conversations.find({"user_id": uid}).sort("created_at", -1).limit(limit)
                docs = await cursor.to_list(length=limit)
                docs.reverse()
                return [{"role": d["role"], "content": d["content"]} for d in docs]
            except Exception as e:
                logging.warning("Error retrieving conversation history from MongoDB: %s", e)
        history = self._mem_conversations.get(uid, [])
        return [{"role": d["role"], "content": d["content"]} for d in history[-limit:]]

    async def clear_conversation_history(self, user_id: int) -> int:
        """Delete all conversation memory for a user. Returns count of deleted messages."""
        uid = int(user_id)
        deleted_count = len(self._mem_conversations.pop(uid, []))
        if self.conversations is not None:
            try:
                res = await self.conversations.delete_many({"user_id": uid})
                deleted_count = res.deleted_count
            except Exception as e:
                logging.warning("Error clearing conversation history in MongoDB: %s", e)
        return deleted_count


# ── Singleton instance ───────────────────────────────────────
db = Database(MONGO_URL)