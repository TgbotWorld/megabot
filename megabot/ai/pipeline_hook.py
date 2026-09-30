# AI Pipeline Hook — bridge between pipeline and AI agent
import asyncio
import logging

from megabot.ai.analyzer import extract_safe_metadata
from megabot.ai.executor import execute_plan
from megabot.ai.planner import plan_actions

log = logging.getLogger(__name__)


async def run_ai_pipeline(app, job: dict, dest_dir: str, user_prompt: str,
                          edit_status_fn) -> list[str] | None:
    """
    Analyzes metadata safely, calls AI for a tailored plan (with heuristic
    fallback), and executes it in the sandbox.
    Returns list of file paths ready for upload, or None if nothing actionable.
    """
    import time
    started = time.time()
    log.info("Starting AI pipeline analysis for job %s...", job.get("_id"))

    # 1. Safely extract non-sensitive metadata (no file contents exposed)
    metadata = await asyncio.to_thread(extract_safe_metadata, dest_dir)
    if not metadata or metadata.get("total_files", 0) == 0:
        log.warning("No valid files found in job directory for AI processing.")
        return None

    log.info("AI metadata extracted: %d files, total size %s",
             metadata["total_files"], metadata["total_size"])

    # 2. Query AI for plan (validated; heuristic fallback when AI is down)
    plan = await plan_actions(metadata, user_prompt)
    if not plan or not plan.get("actions"):
        log.warning("No actionable plan received (AI + heuristic).")
        return None

    # 3. Securely execute actions in the sandbox
    files_to_send = await execute_plan(app, job, dest_dir, plan, edit_status_fn)
    if not files_to_send:
        log.warning("AI execution completed but yielded no files.")
        return None

    log.info("AI pipeline completed: %d file(s) in %.1fs (summary: %s).",
             len(files_to_send), time.time() - started, plan.get("summary", "")[:120])
    return files_to_send
