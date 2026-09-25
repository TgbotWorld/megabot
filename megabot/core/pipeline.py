# Central job pipeline: probe → download → analyze → (ask) → process → upload
import asyncio
import logging
import os
import time

from config import (DOWNLOAD_DIR, MAX_FILE_SIZE_MB, MIN_FREE_DISK_MB,
                    RETRY_ATTEMPTS)
from megabot.core.database import db
from megabot.ui import texts
from megabot.ui.keyboards import archive_choice_kb, cancel_kb

log = logging.getLogger(__name__)


class JobCancelled(Exception):
    pass


async def _free_disk_mb() -> float:
    import shutil
    usage = shutil.disk_usage(DOWNLOAD_DIR)
    return usage.free / (1024 * 1024)


async def _edit_status(app, job, text, kb=None):
    try:
        await app.edit_message_text(
            job["chat_id"], job["message_id"], text,
            reply_markup=kb, disable_web_page_preview=True,
        )
    except Exception as e:
        # message identical or edited too fast — non-fatal
        log.debug("status edit skipped: %s", e)


def _get_host_label(url: str) -> str:
    url_l = str(url).lower()
    if "mega." in url_l:
        return "MEGA"
    if "mediafire." in url_l:
        return "MediaFire"
    if "mp4upload." in url_l:
        return "MP4Upload"
    if any(k in url_l for k in ["terabox", "1024tera", "4funbox"]):
        return "TeraBox"
    if url_l.startswith("tg://"):
        return "Telegram"
    return "Direct Web"


async def run_job(app, job: dict):
    job_id = job["_id"]
    urls = job["url"] if isinstance(job.get("url"), list) else [job["url"]]
    multi = len(urls) > 1

    is_tg_media = bool(job.get("is_telegram_media") or (urls and str(urls[0]).startswith("tg://")))
    dest_dir = os.path.join(DOWNLOAD_DIR, job_id)
    os.makedirs(dest_dir, exist_ok=True)

    try:
        if is_tg_media:
            # ── Telegram Direct Media Download ───────────────────────
            await db.set_job_status(job_id, "downloading")
            name = job.get("media_file_name") or "telegram_file"
            size = int(job.get("media_file_size") or 0)
            await _edit_status(app, job, texts.status_queued(f"Telegram file: {name}"), cancel_kb(job_id))

            if size and size > MAX_FILE_SIZE_MB * 1024 * 1024:
                await db.set_job_status(job_id, "failed", error="too large")
                await _edit_status(app, job, texts.error_too_large(name, size))
                return
            if size and await _free_disk_mb() < size / (1024 * 1024) * 2 + MIN_FREE_DISK_MB:
                await db.set_job_status(job_id, "failed", error="not enough disk space")
                await _edit_status(app, job, texts.error_disk_space())
                return

            last_edit = {"t": 0.0}
            loop = asyncio.get_running_loop()

            def tg_progress_cb(done_bytes: int, total_bytes: int):
                now = time.time()
                if now - last_edit["t"] < 3.0:
                    return
                last_edit["t"] = now
                asyncio.run_coroutine_threadsafe(
                    _edit_status(app, job, texts.progress_download(
                        name, done_bytes, total_bytes or size, host="Telegram"),
                        cancel_kb(job_id)),
                    loop,
                )

            media_msg_id = job.get("media_message_id")
            if not media_msg_id and str(urls[0]).startswith("tg://media/"):
                media_msg_id = int(str(urls[0]).split("/")[-1])

            media_msg = await app.get_messages(job["chat_id"], media_msg_id)
            target_path = os.path.join(dest_dir, name)
            await app.download_media(media_msg, file_name=target_path, progress=tg_progress_cb)

        else:
            # ── URL Download (MEGA, MediaFire, MP4Upload, TeraBox, Direct) ──
            from megabot.downloaders import get_downloader

            await db.set_job_status(job_id, "downloading")
            display_url = urls[0] if not multi else f"{len(urls)} links (batch)"
            await _edit_status(app, job, texts.status_queued(display_url), cancel_kb(job_id))

            downloader = await get_downloader(urls[0], user_id=job["user_id"])
            infos = []
            for attempt in range(1, RETRY_ATTEMPTS + 1):
                try:
                    await asyncio.to_thread(downloader.login)
                    if hasattr(downloader, "session_fresh") and downloader.session_fresh:
                        state = downloader.session_state()
                        await db.save_mega_session(job["user_id"],
                                                   state["sid"], state["master_key"])
                    infos = []
                    for u in urls:
                        infos.append(await asyncio.to_thread(downloader.probe, u))
                    break
                except asyncio.CancelledError:
                    raise
                except Exception as e:
                    log.warning("probe attempt %s failed: %s", attempt, e)
                    if "blocked" in str(e).lower():
                        await db.set_job_status(job_id, "failed", error=str(e)[:300])
                        await _edit_status(app, job, texts.error_blocked())
                        return
                    if attempt == RETRY_ATTEMPTS:
                        await db.set_job_status(job_id, "failed", error=str(e)[:300])
                        await _edit_status(app, job, texts.error_probe(e))
                        return
                    await asyncio.sleep(2 ** attempt)

            name = infos[0]["name"] if len(infos) == 1 else f"{len(infos)}-part archive set"
            size = sum(i.get("size", 0) for i in infos)

            # guards: size limit and disk space
            if size and size > MAX_FILE_SIZE_MB * 1024 * 1024:
                await db.set_job_status(job_id, "failed", error="too large")
                await _edit_status(app, job, texts.error_too_large(name, size))
                return
            if size and await _free_disk_mb() < size / (1024 * 1024) * 2 + MIN_FREE_DISK_MB:
                await db.set_job_status(job_id, "failed", error="not enough disk space")
                await _edit_status(app, job, texts.error_disk_space())
                return

            last_edit = {"t": 0.0}
            loop = asyncio.get_running_loop()
            done_before = [0]
            host_label = _get_host_label(urls[0])

            def progress_cb(done_bytes: int, total_bytes: int):
                now = time.time()
                if now - last_edit["t"] < 3.0:
                    return
                last_edit["t"] = now
                asyncio.run_coroutine_threadsafe(
                    _edit_status(app, job, texts.progress_download(
                        name, done_before[0] + done_bytes, size, host=host_label),
                        cancel_kb(job_id)),
                    loop,
                )

            for idx, url in enumerate(urls, 1):
                part_label = f" ({idx}/{len(urls)})" if multi else ""
                for attempt in range(1, RETRY_ATTEMPTS + 1):
                    try:
                        await asyncio.to_thread(
                            downloader.download, url, dest_dir, progress_cb
                        )
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        log.warning("download attempt %s failed%s: %s", attempt, part_label, e)
                        if "blocked" in str(e).lower():
                            await db.set_job_status(job_id, "failed", error=str(e)[:300])
                            await _edit_status(app, job, texts.error_blocked())
                            return
                        if attempt == RETRY_ATTEMPTS:
                            await db.set_job_status(job_id, "failed", error=str(e)[:300])
                            await _edit_status(app, job, texts.error_download(e))
                            return
                        await asyncio.sleep(2 ** attempt)
                done_before[0] += infos[idx - 1].get("size", 0) or 0

        await db.set_job_status(job_id, "processing")

        # ── 3. AI Processing (if configured) ─────────────────────
        from megabot.ai.client import get_ai_config
        ai_cfg = await get_ai_config()
        ai_files = None
        if ai_cfg.get("api_key"):
            try:
                from megabot.ai.pipeline_hook import run_ai_pipeline
                await _edit_status(app, job, texts.status_ai_analyzing(name), cancel_kb(job_id))
                user_prompt = job.get("prompt", "")
                ai_files = await run_ai_pipeline(app, job, dest_dir, user_prompt, _edit_status)
            except Exception as e:
                log.warning("AI processing hook failed: %s", e)
                ai_files = None

        if ai_files:
            await db.set_job_status(job_id, "uploading")
            await _upload_files(app, job, name, ai_files)
            return

        await _edit_status(app, job, texts.status_analyzing(name), cancel_kb(job_id))

        # ── 4. analyze downloaded content (rule-based fallback) ─
        from megabot.analyzers.classify import classify
        analysis = await asyncio.to_thread(classify, dest_dir)
        kind = analysis["kind"]

        # ── 5. branch on content kind ────────────────────────────
        if kind == "archive":
            # lone middle volume of a split set → try to rescue the missing
            # volumes from the user's own MEGA account before giving up
            from megabot.analyzers.classify import lone_continuation_volume
            if len(analysis["archives"]) == 1 and \
                    lone_continuation_volume(analysis["archives"][0]):
                lone = analysis["archives"][0]
                await _edit_status(app, job, texts.status_rescue_search(), cancel_kb(job_id))

                def rescue_cb(d, t, label=""):
                    now = time.time()
                    if now - last_edit["t"] < 3.0:
                        return
                    last_edit["t"] = now
                    asyncio.run_coroutine_threadsafe(
                        _edit_status(app, job, texts.progress_rescue(label, d, t),
                                     cancel_kb(job_id)),
                        loop,
                    )

                rescued = await asyncio.to_thread(
                    downloader.rescue_siblings, lone, dest_dir, rescue_cb)
                if rescued:
                    analysis = await asyncio.to_thread(classify, dest_dir)
                    kind = analysis["kind"]
                    await _edit_status(app, job, texts.status_analyzing(name), cancel_kb(job_id))
                else:
                    # Don't refuse the job: a lone middle volume usually still
                    # holds fully readable files (WinRAR opens it for the same
                    # reason). Continue into the normal archive flow — extraction
                    # is best-effort now and recovers whatever is inside.
                    log.info("no sibling volumes in account — best-effort extract of %s",
                             os.path.basename(lone))

        if kind == "archive":
            archive_path = analysis["archives"][0]
            mode = await db.get_user_setting(job["user_id"], "archive_mode")
            u_prompt = (job.get("prompt") or "").lower()

            wants_extract = any(w in u_prompt for w in ["unzip", "extract", "unpack", "decompress"])
            wants_as_is = any(w in u_prompt for w in ["as is", "as-is", "don't extract", "keep archive", "keep zip"])

            if wants_extract:
                extract = True
            elif wants_as_is:
                extract = False
            elif mode == "extract":
                extract = True
            elif mode == "archive":
                extract = False
            elif mode == "ask":
                await db.set_job_status(job_id, "awaiting_choice")
                await _edit_status(
                    app, job,
                    texts.archive_choice(name, archive_path),
                    archive_choice_kb(job_id),
                )
                return  # resumed from the callback handler
            else:
                extract = True
        else:
            extract = False

        await db.set_job_status(job_id, "uploading")
        await _process_and_upload(app, job, dest_dir, analysis, extract_archive=extract)

    finally:
        # Automatically remove downloaded files after job finishes/fails
        # to save VPS disk space, unless job is awaiting user choice
        try:
            import shutil
            job_doc = await db.get_job(job_id)
            if not (job_doc and job_doc.get("status") == "awaiting_choice"):
                if os.path.exists(dest_dir):
                    shutil.rmtree(dest_dir, ignore_errors=True)
                    log.info("Cleaned up job download dir: %s", dest_dir)
        except Exception as ce:
            log.warning("Cleanup error for %s: %s", dest_dir, ce)


async def _process_and_upload(app, job, dest_dir: str, analysis: dict,
                              extract_archive: bool = False):
    """Turn downloaded content into Telegram uploads."""
    job_id = job["_id"]
    kind = analysis["kind"]

    files_to_send: list[str] = []
    pdf_path = None

    if kind == "archive" and extract_archive:
        import shutil
        from megabot.processors.archives import safe_extract
        archive_path = analysis["archives"][0]
        out_dir = os.path.join(dest_dir, "extracted")
        os.makedirs(out_dir, exist_ok=True)
        await _edit_status(app, job, texts.status_extracting(analysis["name"]), cancel_kb(job_id))
        extract_succeeded = False
        try:
            await asyncio.to_thread(safe_extract, archive_path, out_dir)
            from megabot.analyzers.classify import classify
            inner = await asyncio.to_thread(classify, out_dir)
            total_extracted = (len(inner["archives"]) + len(inner["videos"]) +
                               len(inner["images"]) + len(inner["others"]))
            if total_extracted > 0:
                extract_succeeded = True
                log.info("archive %s extracted → kind=%s, %d file(s)",
                         archive_path, inner["kind"], total_extracted)
                # continue processing the EXTRACTED content — replacing kind/analysis entirely.
                kind, analysis, dest_dir = inner["kind"], inner, out_dir
            else:
                log.warning("archive %s extraction yielded 0 non-empty files; keeping original archive", archive_path)
                shutil.rmtree(out_dir, ignore_errors=True)
        except Exception as e:
            log.warning("extraction failed for %s: %s; keeping original archive", archive_path, e)
            shutil.rmtree(out_dir, ignore_errors=True)

    if kind == "image_set":
        from megabot.processors.images2pdf import images_to_pdf
        await _edit_status(app, job, texts.status_making_pdf(analysis["name"]), cancel_kb(job_id))
        pdf_name = os.path.splitext(os.path.basename(analysis["name"]))[0] or "images"
        pdf_path = os.path.join(dest_dir, f"{pdf_name}.pdf")
        await asyncio.to_thread(images_to_pdf, analysis["images"], pdf_path)
        files_to_send = [pdf_path]
    elif kind == "video_set":
        files_to_send = analysis["videos"]
    elif kind == "single":
        files_to_send = analysis["others"] + analysis["images"] + analysis["videos"]
    elif kind == "archive":  # upload archive as-is
        files_to_send = analysis["archives"]
    else:  # mixed
        files_to_send = analysis["archives"] + analysis["videos"] + \
            analysis["images"] + analysis["others"]

    await _upload_files(app, job, analysis["name"], files_to_send)


async def _upload_files(app, job: dict, display_name: str, files_to_send: list[str]):
    """Sequential upload with progress edits."""
    job_id = job["_id"]
    if not files_to_send:
        await db.set_job_status(job_id, "failed", error="nothing to upload")
        await _edit_status(app, job, texts.error_empty())
        return

    from megabot.processors.uploader import UploadProgress
    prog = UploadProgress(app, job, files_to_send)
    sent = 0
    for i, path in enumerate(files_to_send, 1):
        await _edit_status(app, job, texts.status_uploading(display_name, i, len(files_to_send)))
        ok = await prog.send(path, thumbs_enabled=await db.get_user_setting(job["user_id"], "video_thumbs"))
        if ok:
            sent += 1
        await asyncio.sleep(1.2)  # be gentle with Telegram flood limits

    if sent == 0:
        # every upload failed — don't pretend success
        err_msg = prog.last_error or "all uploads failed"
        await db.set_job_status(job_id, "failed", error=str(err_msg)[:300])
        await _edit_status(app, job, f"❌ <b>Upload failed</b>\n<code>{err_msg}</code>")
        return
    await db.set_job_status(job_id, "done", files_sent=sent)
    await db.bump_user_jobs(job["user_id"])
    await _edit_status(app, job, texts.status_done(display_name, sent, len(files_to_send)))