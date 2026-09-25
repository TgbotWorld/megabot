# All user-facing texts — HTML parse mode, consistent emoji style
from megabot.processors.uploader import human_size, make_progress_bar

LOGO = "⚡"

WELCOME = """<blockquote>{} <b>MegaBot AI</b></blockquote>
🤖 <b>Your Autonomous Cloud Downloader & File Assistant.</b>

I am powered by an intelligent AI Agent with real tools to:
📥 <b>Download:</b> MEGA.nz, MediaFire.com & MP4Upload.com at maximum speed
📦 <b>Smart Unzip:</b> Automatically extracts ZIP, RAR, 7Z, and TAR archives
🖼️ <b>Image Sets:</b> Merged into clean, ordered <b>PDFs</b>
🎬 <b>Videos:</b> Stream-ready uploads with thumbnails
🛠️ <b>Autonomous Tools:</b> Unzip files, delete storage, clean disk, and manage jobs!

💬 <b>Talk to me naturally:</b>
• Paste any MEGA, MediaFire, or MP4Upload link
• Or ask me to unzip files, delete storage, check stats, or cancel jobs!""".format(LOGO)

HELP = """<blockquote>{} <b>MegaBot AI Guide</b></blockquote>
🤖 <b>How to Use Me:</b>
1️⃣ <b>Paste any link:</b> Send MEGA, MediaFire, or MP4Upload links (single or batch)
2️⃣ <b>Add custom instructions:</b> E.g. <i>"unzip and keep only videos"</i>
3️⃣ <b>Live progress:</b> Track downloads in real-time
4️⃣ <b>Instant Delivery:</b> Receive your files directly in Telegram!

🛠️ <b>Autonomous AI Tools:</b>
• <i>"Unzip my files"</i> — extracts archives
• <i>"Delete my files"</i> — cleans job files from disk
• <i>"Clean disk"</i> — frees up server storage
• <i>"Show my jobs"</i> — lists active and queued downloads
• <i>"Cancel job <id>"</i> — stops a running job

<b>Supported Hosts:</b> MEGA.nz • MediaFire.com • MP4Upload.com
<b>Supported Formats:</b> zip, rar, 7z, tar, iso • mp4, mkv • images → PDF • docs

<b>Commands:</b>
/start — Welcome & features
/agent — AI Agent dashboard & status
/settings — Preferences (archive mode, PDF, thumbnails)
/cancel — Cancel active job
/login & /logout — Custom MEGA account""".format(LOGO)

BANNED = "🚫 You are banned from using this bot."

ERROR_GENERIC = "❌ Something went wrong while processing your job. Please try again."

NO_LINK = ("🤔 I couldn't find a valid download link in that message.\n"
           "Send me a link formatted like:\n"
           "• <code>https://mega.nz/file/...#...</code>\n"
           "• <code>https://www.mediafire.com/file/...</code>\n"
           "• <code>https://www.mp4upload.com/...</code>")

BUSY = ("⏳ You already have an active job. Please wait for it to finish — "
        "max {limit} job(s) at a time.")

DUPLICATE = ("♻️ This link was processed recently.\n"
             "Send it again in a new message if you really want it re-processed.")

FOLDER_LINK_TRUNCATED = ("🔗 That folder link looks truncated — it must contain "
                         "both parts: <code>…/folder/XXXX#YYYY</code>.\n"
                         "Copy the full link from MEGA (Share → Copy link).")


# ── status cards ─────────────────────────────────────────────

def status_queued(url: str) -> str:
    return ("<blockquote>⏳ <b>Queued</b>\n"
            f"🔗 <code>{url[:80]}</code>\n"
            "🧭 Waiting for a free worker…</blockquote>")


def status_folder_listing(name: str, n: int) -> str:
    return ("<blockquote>📂 <b>Folder detected</b>\n"
            f"📁 <b>{name}</b> — {n} file(s)\n"
            "Listing contents…</blockquote>")


def status_analyzing(name: str) -> str:
    return f"<blockquote>🔍 <b>Analyzing</b>\n📁 <b>{name}</b>\nFiguring out what's inside…</blockquote>"


def status_ai_analyzing(name: str) -> str:
    return f"<blockquote>🤖 <b>AI Analyzing</b>\n📁 <b>{name}</b>\nAnalyzing file structure privately…</blockquote>"


def progress_download(name: str, done: int, total: int, host: str = "Cloud") -> str:
    percent = int(done * 100 / total) if total else 0
    return ("<blockquote>📥 <b>Downloading from {}</b>\n"
            f"{make_progress_bar(percent)} <b>{percent}%</b>\n"
            f"📁 <b>{name}</b>\n"
            f"📦 {human_size(done)} / {human_size(total)}</blockquote>").format(host)


def archive_choice(name: str, path: str) -> str:
    return ("<blockquote>📦 <b>Archive detected</b>\n"
            f"📁 <b>{name}</b></blockquote>\n"
            "What should I do with it?")


def status_extracting(name: str) -> str:
    return f"<blockquote>📂 <b>Decompressing</b>\n📁 <b>{name}</b>\nExtracting contents…</blockquote>"


def status_making_pdf(name: str) -> str:
    return f"<blockquote>🖼️ <b>Building PDF</b>\n📁 <b>{name}</b>\nMerging images in order…</blockquote>"


def status_uploading(name: str, i: int, n: int) -> str:
    return f"<blockquote>📤 <b>Uploading</b>\n📁 <b>{name}</b>\nFile {i} / {n}…</blockquote>"


def status_done(name: str, sent: int, total: int) -> str:
    return f"<blockquote>✅ <b>Done</b>\n📁 <b>{name}</b>\n📤 {sent}/{total} file(s) delivered 🎉</blockquote>"


def status_cancelled() -> str:
    return "<blockquote>🛑 <b>Cancelled</b>\nJob stopped, partial files removed.</blockquote>"


# ── errors ───────────────────────────────────────────────────

def error_probe(e: Exception) -> str:
    return (f"<blockquote>❌ <b>Link not readable</b>\n"
            f"<code>{str(e)[:200]}</code>\n"
            "Check the link is valid and public.</blockquote>")


def error_download(e: Exception) -> str:
    return (f"<blockquote>❌ <b>Download failed</b>\n"
            f"<code>{str(e)[:200]}</code>\n"
            "MEGA may be rate-limiting — try again in a few minutes.</blockquote>")


def error_too_large(name: str, size: int) -> str:
    return ("<blockquote>❌ <b>Too large</b>\n"
            f"📁 <b>{name}</b> is {human_size(size)} — over the bot limit.</blockquote>")


def error_disk_space() -> str:
    return "<blockquote>❌ <b>Not enough disk space</b>\nThe server can't hold this file right now.</blockquote>"


def error_empty() -> str:
    return "<blockquote>❌ <b>Nothing to upload</b>\nThe archive appears to be empty.</blockquote>"


def error_expired() -> str:
    return ("<blockquote>⌛ <b>Files expired</b>\n"
            "The download was cleaned up while waiting for your choice.\n"
            "Send the link again to retry.</blockquote>")


def error_blocked() -> str:
    return ("<blockquote>🚫 <b>MEGA account blocked</b>\n"
            "MEGA flagged this account as suspicious. Open mega.nz in a browser, "
            "follow the unlock steps, then use /login again.</blockquote>")


def status_rescue_search() -> str:
    return ("<blockquote>🧩 <b>Split archive detected</b>\n"
            "Searching your MEGA account for the missing parts…</blockquote>")


def progress_rescue(label: str, done: int, total: int) -> str:
    percent = int(done * 100 / total) if total else 0
    return ("<blockquote>🧩 <b>Fetching missing parts from your MEGA</b>\n"
            f"{make_progress_bar(percent)} <b>{percent}%</b>\n"
            f"📁 <b>{label}</b>\n"
            f"📦 {human_size(done)} / {human_size(total)}</blockquote>")


def error_missing_volumes(fname: str) -> str:
    return ("<blockquote>🧩 <b>Only one part received</b>\n"
            f"📁 <code>{fname[:80]}</code> is a <b>middle volume</b> of a split "
            "archive — I also checked your MEGA account but couldn't find "
            "the other parts there.\n\n"
            "👉 Send <b>ALL parts together in ONE message</b> (or the MEGA "
            "folder link) and I'll download and extract the whole set.</blockquote>")


def error_extract(e: Exception) -> str:
    msg = str(e)
    if "first volume" in msg.lower():
        return ("<blockquote>🧩 <b>Split archive — missing part 1</b>\n"
                "This RAR is one volume of a multi-part set and the first "
                "volume (.part1.rar / .rar) is missing.\n"
                "Send me <b>ALL parts together in one message</b> "
                "(paste every MEGA link at once) and I'll extract the whole "
                "set.</blockquote>")
    if "non-zero exit status" in msg.lower():
        return ("<blockquote>❌ <b>Extraction failed</b>\n"
                "The archive is likely incomplete, corrupted, or password-protected.\n"
                "If it's a split set, send <b>all parts in one message</b>.</blockquote>")
    return (f"<blockquote>❌ <b>Extraction failed</b>\n"
            f"<code>{msg[:200]}</code></blockquote>")