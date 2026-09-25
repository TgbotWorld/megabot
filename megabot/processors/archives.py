# Archive extraction — zip / rar / 7z / tar with zip-slip protection
import logging
import os
import shutil
import subprocess
import tarfile
import zipfile

log = logging.getLogger(__name__)


class UnsafeArchiveError(Exception):
    pass


def _sanitize_member_path(member_name: str) -> str:
    """Normalize Windows slashes, strip drive letters and leading slashes."""
    clean = member_name.replace("\\", "/")
    # Strip Windows drive letters (e.g. C:)
    if len(clean) >= 2 and clean[1] == ":" and clean[0].isalpha():
        clean = clean[2:]
    clean = clean.lstrip("/ ").rstrip(" ")
    return clean


def _validate(member_name: str, dest_dir: str) -> str:
    """Refuse entries escaping dest_dir (zip-slip) or path traversal."""
    clean = _sanitize_member_path(member_name)
    if not clean:
        return os.path.realpath(dest_dir)
    target = os.path.realpath(os.path.join(dest_dir, clean))
    real_dest = os.path.realpath(dest_dir)
    if not target.startswith(real_dest + os.sep) and target != real_dest:
        raise UnsafeArchiveError(f"Unsafe path in archive: {member_name}")
    return target


def detect_archive_type(archive_path: str) -> str:
    """Detect archive extension, checking magic bytes if missing or generic."""
    ext = os.path.splitext(archive_path)[1].lower()
    if ext in (".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"):
        return ext
    try:
        if os.path.isfile(archive_path) and os.path.getsize(archive_path) >= 4:
            with open(archive_path, "rb") as f:
                hdr = f.read(265)
            if hdr.startswith((b"PK\x03\x04", b"PK\x05\x06")):
                return ".zip"
            if hdr.startswith(b"7z\xbc\xaf\x27\x1c"):
                return ".7z"
            if hdr.startswith(b"Rar!\x1a\x07"):
                return ".rar"
            if hdr.startswith((b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00")):
                return ".gz"
            if len(hdr) >= 262 and hdr[257:262] == b"ustar":
                return ".tar"
    except OSError:
        pass
    return ext


def safe_extract(archive_path: str, dest_dir: str) -> str:
    """Extract any supported archive safely into dest_dir."""
    os.makedirs(dest_dir, exist_ok=True)
    ext = detect_archive_type(archive_path)

    def _has_output(path: str) -> bool:
        for root, _, names in os.walk(path):
            for name in names:
                fp = os.path.join(root, name)
                if os.path.isfile(fp) and os.path.getsize(fp) > 0:
                    return True
        return False

    def _clean_dir(path: str):
        for entry in os.scandir(path):
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path, ignore_errors=True)
            else:
                try:
                    os.remove(entry.path)
                except OSError:
                    pass

    if ext == ".zip":
        extracted_ok = False
        try:
            with zipfile.ZipFile(archive_path) as zf:
                for info in zf.infolist():
                    clean_name = _sanitize_member_path(info.filename)
                    if not clean_name:
                        continue
                    target_path = _validate(info.filename, dest_dir)
                    if info.is_dir():
                        os.makedirs(target_path, exist_ok=True)
                    else:
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        with zf.open(info) as src, open(target_path, "wb") as dst:
                            shutil.copyfileobj(src, dst)
            extracted_ok = _has_output(dest_dir)
        except UnsafeArchiveError:
            raise
        except Exception as ze:
            log.warning("Python zipfile failed on %s: %s; falling back to CLI", archive_path, ze)
            _clean_dir(dest_dir)

        if not extracted_ok:
            for cmd in [
                ["unzip", "-o", "-q", archive_path, "-d", dest_dir],
                ["bsdtar", "-xf", archive_path, "-C", dest_dir],
                ["7z", "x", f"-o{dest_dir}", "-y", archive_path],
            ]:
                if shutil.which(cmd[0]):
                    try:
                        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except subprocess.CalledProcessError:
                        pass
                    if _has_output(dest_dir):
                        extracted_ok = True
                        break
                    _clean_dir(dest_dir)

        if not extracted_ok:
            raise RuntimeError(f"Could not extract zip archive: {archive_path}")

        # Post-extraction security check
        for root, _, names in os.walk(dest_dir):
            for n in names:
                fp = os.path.join(root, n)
                _validate(os.path.relpath(fp, dest_dir), dest_dir)

    elif ext in (".tar", ".gz", ".tgz", ".bz2", ".xz"):
        extracted_ok = False
        try:
            with tarfile.open(archive_path) as tf:
                for member in tf.getmembers():
                    _validate(member.name, dest_dir)
                tf.extractall(dest_dir)
            extracted_ok = _has_output(dest_dir)
        except UnsafeArchiveError:
            raise
        except Exception as te:
            log.warning("Python tarfile failed on %s: %s; falling back to CLI", archive_path, te)
            _clean_dir(dest_dir)

        if not extracted_ok:
            for cmd in [
                ["bsdtar", "-xf", archive_path, "-C", dest_dir],
                ["tar", "-xf", archive_path, "-C", dest_dir],
            ]:
                if shutil.which(cmd[0]):
                    try:
                        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except subprocess.CalledProcessError:
                        pass
                    if _has_output(dest_dir):
                        extracted_ok = True
                        break
                    _clean_dir(dest_dir)

        if not extracted_ok:
            raise RuntimeError(f"Could not extract tar/compressed archive: {archive_path}")

        # Post-extraction security check
        for root, _, names in os.walk(dest_dir):
            for n in names:
                fp = os.path.join(root, n)
                _validate(os.path.relpath(fp, dest_dir), dest_dir)

    elif ext == ".rar":
        # We try modern extractors in order:
        # 1. unar (The Unarchiver) - best RAR5 & modern split RAR support
        # 2. 7z (p7zip)
        # 3. bsdtar (libarchive - RAR4)
        # 4. unrar / unrar-free
        attempts = []
        if shutil.which("unar"):
            attempts.append(["unar", "-o", dest_dir, "-D", "-f", archive_path])
        if shutil.which("7z"):
            attempts.append(["7z", "x", f"-o{dest_dir}", "-y", archive_path])
        if shutil.which("bsdtar"):
            attempts.append(["bsdtar", "-xf", archive_path, "-C", dest_dir])
        unrar_tool = next((t for t in ("unrar", "unrar-free") if shutil.which(t)), None)
        if unrar_tool:
            attempts.append([unrar_tool, "x", "-y", "-o+", archive_path,
                             dest_dir + os.sep])

        for cmd in attempts:
            try:
                subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
            except subprocess.CalledProcessError:
                pass
            if _has_output(dest_dir):
                break
            _clean_dir(dest_dir)
        else:
            raise RuntimeError(
                "No extractor could read anything from this RAR — it may be "
                "a split volume whose content lives in another part, "
                "corrupted, or password-protected.")

        # Post-extraction security check: ensure no member escaped dest_dir
        for root, _, names in os.walk(dest_dir):
            for n in names:
                fp = os.path.join(root, n)
                _validate(os.path.relpath(fp, dest_dir), dest_dir)

    elif ext == ".7z":
        extracted_ok = False
        try:
            import py7zr
            with py7zr.SevenZipFile(archive_path) as sz:
                for name in sz.getnames():
                    _validate(name, dest_dir)
                sz.extractall(dest_dir)
            extracted_ok = _has_output(dest_dir)
        except UnsafeArchiveError:
            raise
        except Exception as se:
            log.warning("py7zr failed on %s: %s; falling back to CLI", archive_path, se)
            _clean_dir(dest_dir)

        if not extracted_ok:
            for cmd in [
                ["7z", "x", f"-o{dest_dir}", "-y", archive_path],
                ["bsdtar", "-xf", archive_path, "-C", dest_dir],
            ]:
                if shutil.which(cmd[0]):
                    try:
                        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except subprocess.CalledProcessError:
                        pass
                    if _has_output(dest_dir):
                        extracted_ok = True
                        break
                    _clean_dir(dest_dir)

        if not extracted_ok:
            raise RuntimeError(f"Could not extract 7z archive: {archive_path}")

        for root, _, names in os.walk(dest_dir):
            for n in names:
                fp = os.path.join(root, n)
                _validate(os.path.relpath(fp, dest_dir), dest_dir)

    else:
        raise ValueError(f"Unsupported archive format: {ext}")

    log.info("Extracted %s → %s", archive_path, dest_dir)
    return dest_dir


def safe_zip(file_paths: list[str], output_zip_path: str, dest_dir: str) -> str:
    """Safely create a zip archive containing file_paths inside dest_dir."""
    _validate(os.path.basename(output_zip_path), dest_dir)
    os.makedirs(dest_dir, exist_ok=True)
    with zipfile.ZipFile(output_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in file_paths:
            if os.path.isfile(p):
                # Ensure each file is inside dest_dir
                _validate(os.path.relpath(p, dest_dir), dest_dir)
                arcname = os.path.relpath(p, dest_dir)
                zf.write(p, arcname=arcname)
    log.info("Zipped %d files → %s", len(file_paths), output_zip_path)
    return output_zip_path