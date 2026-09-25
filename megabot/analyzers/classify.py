# Content classifier — decide what the downloaded files actually are
import os
import re

from natsort import natsorted

from config import MIN_IMAGES_FOR_PDF

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif"}
VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".m4v", ".ts", ".wmv"}
ARCHIVE_EXTS = {".zip", ".rar", ".7z", ".tar", ".gz", ".tgz", ".bz2", ".xz"}

# split-archive volume suffixes: .part1.rar / .r00 / .z01 / .7z.001 …
_VOLUME_RE = re.compile(r"\.(part\d+|rar|r\d{2,3}|z\d{2,3}|7z\.\d{3})$", re.I)


def _volume_number(fname: str):
    """Volume number of a split-archive member, or None if not a volume.
    Handles x.partN.rar, x.partN.zip, x.rNN, x.zNN, x.7z.NNN, and plain
    x.rar (volume 1 of old-style sets)."""
    m = re.search(r"\.part(\d+)\.[^.]+$", fname, re.I)
    if m:                              # .part2.rar → _VOLUME_RE would only see ".rar"
        return int(m.group(1))
    m = _VOLUME_RE.search(fname)
    if not m:
        return None
    tok = m.group(1).lower()
    if tok == "rar":
        return 1                       # plain .rar = first volume of .rar/.r00 sets
    if tok.startswith(("r", "z")):     # WinRAR old-style: .r00/.z00 is volume 2
        digits = re.sub(r"\D", "", tok)
        return (int(digits) if digits else 0) + 2
    if tok.startswith("7z."):          # 7z.NNN — digits AFTER the dot only
        digits = re.sub(r"\D", "", tok.split(".", 1)[1])
        return int(digits) if digits else 1
    digits = re.sub(r"\D", "", tok)    # partN uses its number directly
    return int(digits) if digits else 1


def volume_base(fname: str) -> str:
    """Common stem of a split set: 'x.part2.rar' / 'x.r00' / 'x.rar' → 'x.'"""
    m = re.search(r"\.part\d+\.[^.]+$", fname, flags=re.I)
    if m:
        return fname[:m.start()] + "."
    m = _VOLUME_RE.search(fname)
    return fname[:m.start()] + "." if m else fname


def lone_continuation_volume(path: str) -> bool:
    """True if `path` is a NON-FIRST volume of a split archive set
    (x.part2.rar+ / x.r00+ / x.z01+ / x.7z.002+). Such a file can never
    be extracted on its own — volume 1 must be present."""
    n = _volume_number(os.path.basename(path))
    return n is not None and n >= 2


def _split_archive_set(archives: list[str]):
    """If all `archives` are volumes of ONE split set, return them ordered
    with volume 1 first (extraction must start there). Else None."""
    def base(path):
        b = _VOLUME_RE.sub("", os.path.basename(path))
        return re.sub(r"\.part\d+$", "", b, flags=re.I)

    if len({base(a) for a in archives}) != 1:
        return None

    def vol_key(path):
        n = _volume_number(os.path.basename(path))
        return n if n is not None else 0

    return sorted(archives, key=vol_key)


def _walk_files(root: str) -> list[str]:
    files = []
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            path = os.path.join(dirpath, n)
            if os.path.isfile(path) and os.path.getsize(path) > 0:
                files.append(path)
    return files


def is_archive_file(path: str) -> bool:
    """Return True if path has an archive extension or archive magic bytes."""
    ext = os.path.splitext(path)[1].lower()
    if ext in ARCHIVE_EXTS:
        return True
    try:
        if os.path.isfile(path) and os.path.getsize(path) >= 4:
            with open(path, "rb") as f:
                hdr = f.read(265)
            if hdr.startswith((b"PK\x03\x04", b"PK\x05\x06")):
                return True
            if hdr.startswith(b"7z\xbc\xaf\x27\x1c"):
                return True
            if hdr.startswith(b"Rar!\x1a\x07"):
                return True
            if hdr.startswith((b"\x1f\x8b", b"BZh", b"\xfd7zXZ\x00")):
                return True
            if len(hdr) >= 262 and hdr[257:262] == b"ustar":
                return True
    except OSError:
        pass
    return False


def classify(root: str) -> dict:
    """Classify everything under `root`.

    Returns dict(kind, name, images, videos, archives, others) where kind is one of:
      archive     — exactly one archive dominates
      image_set   — ≥ MIN_IMAGES_FOR_PDF images, no videos
      video_set   — one or more videos, no images worth merging
      single      — exactly one file of any kind
      mixed       — everything else
    """
    files = natsorted(_walk_files(root), key=lambda p: os.path.basename(p).lower())

    images = [f for f in files if os.path.splitext(f)[1].lower() in IMAGE_EXTS]
    videos = [f for f in files if os.path.splitext(f)[1].lower() in VIDEO_EXTS]
    archives = [f for f in files if is_archive_file(f)]
    others = [f for f in files if f not in images and f not in videos and f not in archives]

    # readable display name: folder name or single file name
    if len(files) == 1:
        name = os.path.basename(files[0])
    else:
        name = os.path.basename(os.path.normpath(root))

    # split-archive set (x.part1.rar + x.part2.rar …) → one archive, volume 1
    # first, so extraction chains through every volume
    split = _split_archive_set(archives) if len(archives) > 1 else None

    if split is not None and not images and not videos:
        kind, archives = "archive", split
    elif len(files) == 1 and archives:
        kind = "archive"      # lone archive → same choice flow as any archive
    elif len(files) == 1:
        kind = "single"
    elif len(archives) == 1 and not images and not videos and len(others) <= 1:
        kind = "archive"
    elif len(archives) >= 1 and not images and not videos and len(others) <= 2:
        kind = "archive"
    elif images and len(images) >= MIN_IMAGES_FOR_PDF and not videos:
        kind = "image_set"
    elif videos and not images:
        kind = "video_set"
    else:
        kind = "mixed"

    return dict(kind=kind, name=name, images=images, videos=videos,
                archives=archives, others=others)