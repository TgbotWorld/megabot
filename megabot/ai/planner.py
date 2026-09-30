# AI Planner — uses OpenRouter to inspect file metadata and create execution plans
import json
import logging

from megabot.ai.client import call_openrouter_json

log = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the AI Dispatcher and Intelligent File Processor for MegaBot.
Your task is to analyze file metadata (names, formats, extensions, sizes, and structure) along with any user request, and return a safe, ordered execution plan.

IMPORTANT PRIVACY & SECURITY POLICIES:
1. You only see sanitized structural metadata (names, sizes, types). You do NOT see raw file contents.
2. You can only operate inside the bot's temporary job directory.
3. You must NEVER attempt to access system files or environment files (.env).
4. All actions you emit must come from the allowed list below.

ALLOWED ACTIONS:
1. {"action": "extract_archive", "file": "<relative_path_to_archive>"}
   - Extracts a zip, rar, 7z, or tar file inside the workspace.
2. {"action": "images_to_pdf", "output_name": "<name>.pdf", "files": ["img1.jpg", "img2.jpg"]}
   - Merges image files into an ordered PDF. If "files" is omitted, all images in the folder are merged.
3. {"action": "create_zip", "output_name": "<name>.zip", "files": ["file1", "file2"]}
   - Packages specified files into a single zip archive.
4. {"action": "filter_files", "keep_extensions": [".mp4", ".mkv"]} or {"action": "filter_files", "keep_files": ["f1", "f2"]}
   - Filters the candidate list of files to keep for final processing or upload.
5. {"action": "rename_file", "from": "<old_name>", "to": "<new_name>"}
   - Renames a file cleanly within the job directory.
6. {"action": "upload", "files": ["<file1>", "<file2>"]}
   - Specifies which files to deliver to the user on Telegram. If omitted, all resulting files will be uploaded.
7. {"action": "delete_file", "files": ["<file1>", "<file2>"]} or {"action": "delete_file", "file": "<file>"}
   - Deletes specified unwanted, junk, sample, or temporary files safely inside the job directory.

OUTPUT FORMAT:
Respond with valid JSON only:
{
  "summary": "Brief 1-sentence explanation of what will be done for the user",
  "actions": [
    ... list of actions in the order they should be executed ...
  ]
}

DECISION HEURISTICS:
- Any archive (.zip, .rar, .7z, .tar, .gz): ALWAYS emit action "extract_archive" to decompress the contents so the user gets the files inside, UNLESS the user explicitly requested "keep archive", "do not extract", or "as-is".
- If the user gave an explicit instruction (e.g., "unzip", "extract", "convert to pdf", "extract only videos", "zip all files", "delete samples", "remove txt"), STRICTLY prioritize fulfilling the user's intent!
- If user requested deleting/removing files or discarding certain formats, emit action "delete_file".
- If the user gave NO explicit instruction:
  - Archive (.zip/.rar/.7z): emit action "extract_archive".
  - ≥ 3 images and no video: action "images_to_pdf".
  - Video files: keep videos ready for stream upload.
  - Many mixed/loose files (>10 files): bundle them into a zip with "create_zip" for clean delivery.
  - Single non-archive file (like a single .mp4 or .pdf): upload as-is.
"""


async def plan_actions(metadata: dict, user_prompt: str = "") -> dict | None:
    """
    Query AI provider for an execution plan, validate it, and fall back to a
    deterministic heuristic plan when AI is unavailable (OpenClaw-level robustness).
    """
    user_instruction = user_prompt.strip() if user_prompt else "No specific instruction provided. Choose the best processing strategy."

    prompt_content = {
        "user_instruction": user_instruction,
        "files_metadata": metadata,
    }

    user_message = f"Please analyze these files and generate an execution plan:\n\n{json.dumps(prompt_content, indent=2)}"

    plan = await call_openrouter_json(SYSTEM_PROMPT, user_message)
    validated = validate_plan(plan)
    if validated:
        log.info("AI generated plan: %s (summary: %s)", len(validated.get("actions", [])), validated.get("summary"))
        return validated

    log.warning("AI plan invalid/empty (%s); using heuristic fallback.", plan)
    return heuristic_plan(metadata, user_prompt)


ALLOWED_PLAN_ACTIONS = {
    "extract_archive", "images_to_pdf", "create_zip", "filter_files",
    "rename_file", "upload", "delete_file",
}


def validate_plan(plan: dict | None) -> dict | None:
    """Filter unknown/malicious actions; return None if nothing actionable."""
    if not plan or not isinstance(plan, dict):
        return None
    actions = plan.get("actions")
    if not isinstance(actions, list):
        return None
    clean: list[dict] = []
    for act in actions:
        if not isinstance(act, dict):
            continue
        name = act.get("action")
        if name not in ALLOWED_PLAN_ACTIONS:
            log.warning("Dropping unknown plan action: %s", name)
            continue
        # Reject path traversal in file references
        blob = json.dumps(act)
        if ".." in blob or blob.count("/") > 10:
            # Allow simple relative paths like extracted/file.mp4 but not escapes
            if ".." in blob:
                log.warning("Dropping plan action with traversal: %s", act)
                continue
        clean.append(act)
        if len(clean) >= 10:
            break
    if not clean:
        return None
    summary = str(plan.get("summary", ""))[:300]
    return {"summary": summary or "Processing files.", "actions": clean}


def heuristic_plan(metadata: dict, user_prompt: str = "") -> dict | None:
    """Deterministic rule-based plan mirroring SYSTEM_PROMPT heuristics."""
    try:
        files = metadata.get("files", []) or []
        total = metadata.get("total_files", len(files))
        breakdown = metadata.get("category_breakdown", {}) or {}
        prompt = (user_prompt or "").lower()
        if total == 0:
            return None

        keep_archive = any(k in prompt for k in ["keep archive", "do not extract", "as-is", "as is"])
        wants_pdf = any(k in prompt for k in ["pdf", "merge"])
        wants_zip = any(k in prompt for k in ["zip", "bundle", "pack"])
        wants_videos_only = "video" in prompt and any(k in prompt for k in ["only", "extract", "keep"])

        actions: list[dict] = []
        summary = "Processing files."
        archives = [f["name"] for f in files if f.get("category") == "archive"]
        images = [f["name"] for f in files if f.get("category") == "image"]
        videos = [f["name"] for f in files if f.get("category") == "video"]

        if archives and not keep_archive:
            for a in archives[:3]:
                actions.append({"action": "extract_archive", "file": a})
            summary = f"Extracting {len(actions)} archive(s)."
        elif wants_videos_only and videos:
            exts = sorted({__import__("os").path.splitext(v)[1].lower() for v in videos})
            actions.append({"action": "filter_files", "keep_extensions": exts})
            summary = "Keeping only video files."
        elif (wants_pdf or (len(images) >= 3 and not videos)) and images:
            actions.append({"action": "images_to_pdf", "output_name": "document.pdf"})
            summary = f"Merging {len(images)} images into a PDF."
        elif wants_zip or total > 10:
            actions.append({"action": "create_zip", "output_name": "bundle.zip"})
            summary = "Bundling files into a zip for clean delivery."
        else:
            summary = "Uploading files as-is."

        if not actions:
            return {"summary": summary, "actions": [{"action": "upload"}]}
        return {"summary": summary, "actions": actions}
    except Exception as e:
        log.warning("heuristic_plan failed: %s", e)
        return None
