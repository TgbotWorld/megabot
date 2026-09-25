# Raw MEGA API layer — adds what mega.py lacks:
#   • persistent session cache → login ONCE per user, reuse the sid from
#     MongoDB afterwards (repeated logins are what trigger MEGA's
#     "suspicious login" lockouts)
#   • folder-link support      → 'f' request with the nk folder key,
#     per-node key decryption, sequential chunked download
#   • browser User-Agent on every API call (looks less bot-like)
import json
import logging
import os
import re
import time
from collections import deque

import requests
from Crypto.Cipher import AES
from Crypto.Util import Counter

from mega import Mega
from mega.crypto import (a32_to_str, base64_to_a32, base64_url_decode,
                         decrypt_attr, decrypt_key, get_chunks)
from mega.errors import RequestError

log = logging.getLogger(__name__)

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

FOLDER_URL_RE = re.compile(
    r"https?://mega\.(?:nz|io|co\.nz)/(?:folder/([\w\-]+)#([\w\-]+)|#F!([\w\-]+)!([\w\-]+))", re.I)
FILE_URL_RE = re.compile(
    r"https?://mega\.(?:nz|io|co\.nz)/(?:file/([\w\-]+)#([\w\-]+)|#!([\w\-]+)!([\w\-]+))", re.I)


class RawMega:
    """Thin raw-API client built on mega.py's login crypto."""

    def __init__(self, email: str = None, password: str = None):
        self.email = email
        self.password = password
        self._m = Mega()          # reuse its login + crypto internals
        self._http = requests.Session()
        self._http.headers["User-Agent"] = USER_AGENT
        self.logged_in = False

    # ── session management ──────────────────────────────────

    def login_with_session(self, saved: dict = None) -> bool:
        """Restore a saved session when possible, else do a full login.
        Returns True when a FRESH login happened (caller should persist
        session_state()). Never logs in twice inside one client."""
        if self.logged_in:
            return False

        if saved and saved.get("sid"):
            self._m.sid = saved["sid"]
            self._m.master_key = list(saved["master_key"])
            try:
                self._api({"a": "ug"})      # cheap "is this sid alive?" probe
                self.logged_in = True
                log.info("MEGA session restored (%s)", self.email or "account")
                return False
            except RequestError as e:
                log.warning("Saved MEGA session invalid (%s) — fresh login", e)
                self._m.sid = None

        if self.email and self.password:
            self._m._login_user(self.email, self.password)
        else:
            self._m.login_anonymous()
        self.logged_in = True
        log.info("MEGA fresh login (%s)", self.email or "anonymous")
        return bool(self.email)             # don't persist anonymous sessions

    def session_state(self) -> dict:
        return {"sid": self._m.sid, "master_key": list(self._m.master_key or [])}

    # ── raw API plumbing ────────────────────────────────────

    def _api(self, data, extra: dict = None, anon: bool = False):
        if not isinstance(data, list):
            data = [data]
        params = {"id": self._m.sequence_num}
        self._m.sequence_num += 1
        if self._m.sid and not anon:
            params["sid"] = self._m.sid
        if extra:
            params.update(extra)
        url = f"https://g.api.{self._m.domain}/cs"
        backoff = 2
        for _ in range(4):
            resp = self._http.post(url, params=params,
                                   data=json.dumps(data), timeout=120)
            j = resp.json()
            code = None
            if isinstance(j, int):
                code = j
            elif isinstance(j, list) and j and isinstance(j[0], int):
                code = j[0]
            if code is not None:
                if code == -3:              # rate limited — back off & retry
                    time.sleep(backoff)
                    backoff *= 2
                    continue
                raise RequestError(code)
            return j[0] if isinstance(j, list) else j
        raise RequestError(-3)

    # ── link parsing / probing ──────────────────────────────

    @staticmethod
    def parse_folder_url(url: str):
        m = FOLDER_URL_RE.search(url or "")
        if not m:
            return None
        groups = [g for g in m.groups() if g is not None]
        return (groups[0], groups[1]) if len(groups) >= 2 else None

    @staticmethod
    def parse_file_url(url: str):
        m = FILE_URL_RE.search(url or "")
        if not m:
            return None
        groups = [g for g in m.groups() if g is not None]
        return (groups[0], groups[1]) if len(groups) >= 2 else None

    def probe_file(self, url: str) -> dict:
        parsed = self.parse_file_url(url)
        if not parsed:
            raise ValueError(f"Not a MEGA file link: {url}")
        handle, key_b64 = parsed
        key = base64_to_a32(key_b64)

        req = {"a": "g", "g": 1, "ssm": 1, "p": handle}
        try:
            data = self._api(req)
        except RequestError as e:
            if e.args and e.args[0] in (-9, -13, -14):
                data = self._api(req, anon=True)
            else:
                raise

        name = handle
        try:
            k_probe = (key[0] ^ key[4], key[1] ^ key[5],
                       key[2] ^ key[6], key[3] ^ key[7])
            attr = decrypt_attr(base64_url_decode(data.get("at", "")), k_probe)
            if attr and attr.get("n"):
                name = attr["n"]
        except Exception:
            pass

        size = int(data.get("s") or 0)
        return {"name": name, "size": size, "kind": "file"}

    def download_file(self, url: str, dest_dir: str, progress_cb=None) -> str:
        """Download a public file link straight into dest_dir.
        Raw streaming → progress_cb(done, total) fires per chunk, unlike
        mega.py's download_url which hides the file in /tmp until the end."""
        parsed = self.parse_file_url(url)
        if not parsed:
            raise ValueError("Not a MEGA file link")
        handle, key_b64 = parsed
        key = base64_to_a32(key_b64)

        req = {"a": "g", "g": 1, "ssm": 1, "p": handle}
        try:
            data = self._api(req)
        except RequestError as e:
            if e.args and e.args[0] in (-9, -13, -14):
                data = self._api(req, anon=True)
            else:
                raise

        name = handle
        try:
            k_probe = (key[0] ^ key[4], key[1] ^ key[5],
                       key[2] ^ key[6], key[3] ^ key[7])
            attr = decrypt_attr(base64_url_decode(data.get("at", "")), k_probe)
            if attr and attr.get("n"):
                name = attr["n"]
        except Exception:
            pass

        os.makedirs(dest_dir, exist_ok=True)
        target = self._unique_path(os.path.join(dest_dir, name))
        size = int(data.get("s") or 0)
        done = {"b": 0}

        def on_delta(d):
            done["b"] += d
            if progress_cb:
                try:
                    progress_cb(done["b"], size)
                except Exception:
                    pass

        self._stream_node(data, key, target, on_delta)
        return dest_dir

    # ── folder links ────────────────────────────────────────

    def list_folder(self, url: str) -> dict:
        """List every file inside a public folder link.
        Returns {"name", "size" (sum), "files" (count), "_nodes": [...]}."""
        handle, key_b64 = self.parse_folder_url(url)
        if not handle:
            raise ValueError("Not a MEGA folder link")
        resp = self._api({"a": "f", "c": 1, "r": 1, "n": handle},
                         extra={"nk": key_b64})
        nodes = resp.get("f", []) if isinstance(resp, dict) else resp
        nodes = [n for n in nodes if isinstance(n, dict)]

        children = {}
        for n in nodes:
            children.setdefault(n.get("p"), []).append(n)

        root_key = base64_to_a32(key_b64)
        name = "MEGA folder"
        for n in nodes:                       # decrypt the root folder name
            if n.get("h") == handle and n.get("t") == 2 and n.get("a"):
                try:
                    attr = decrypt_attr(base64_url_decode(n["a"]), root_key)
                    if attr and attr.get("n"):
                        name = attr["n"]
                except Exception:
                    pass
                break

        files = []
        queue = deque([(handle, root_key, "")])
        while queue:                          # BFS over subfolders
            ph, pkey, path = queue.popleft()
            for n in children.get(ph, []):
                key = self._node_key(n, pkey)
                if key is None:
                    continue
                attr = self._node_attr(n, key)
                if n.get("t") == 0:           # file
                    files.append({
                        "h": n["h"], "size": int(n.get("s") or 0),
                        "key": key,
                        "name": (attr or {}).get("n") or n["h"],
                        "path": path,
                    })
                elif n.get("t") == 2:         # subfolder
                    sub = (attr or {}).get("n") or n["h"]
                    queue.append((n["h"], key, f"{path}{sub}/"))

        return {"name": name, "size": sum(f["size"] for f in files),
                "files": len(files), "_nodes": files}

    @staticmethod
    def _node_key(node, parent_key):
        """Decrypt a node key with its parent folder's key."""
        kfield = node.get("k") or ""
        if ":" not in kfield:
            return None
        try:
            return decrypt_key(base64_to_a32(kfield.split(":", 1)[1]), parent_key)
        except Exception:
            return None

    @staticmethod
    def _node_attr(node, key):
        try:
            if node.get("t") == 0:            # file → derive the AES key
                k = (key[0] ^ key[4], key[1] ^ key[5],
                     key[2] ^ key[6], key[3] ^ key[7])
            else:                             # folder → key used directly
                k = key
            return decrypt_attr(base64_url_decode(node["a"]), k)
        except Exception:
            return None

    def download_folder(self, url: str, dest_dir: str,
                        progress_cb=None, listing: dict = None) -> str:
        listing = listing or self.list_folder(url)
        nodes = listing["_nodes"]
        if not nodes:
            raise RuntimeError("Folder is empty or not readable")
        handle, key_b64 = self.parse_folder_url(url)
        grand_total = listing["size"] or 1
        done = {"bytes": 0}
        os.makedirs(dest_dir, exist_ok=True)

        for f in nodes:                       # sequential = kind to MEGA limits
            subdir = os.path.join(dest_dir, f["path"]) if f["path"] else dest_dir
            os.makedirs(subdir, exist_ok=True)
            target = self._unique_path(os.path.join(subdir, f["name"]))
            log.info("Folder file: %s (%s bytes)", target, f["size"])
            self._download_node(f, target, extra={"nk": key_b64},
                                on_delta=lambda delta: self._tick(progress_cb, done, delta, grand_total))
        return dest_dir

    def _download_node(self, fnode, target, extra=None, on_delta=None):
        req = {"a": "g", "g": 1, "ssm": 1, "n": fnode["h"]}
        try:
            data = self._api(req, extra=extra)
        except RequestError as e:
            # some nodes only resolve without an account sid attached
            if e.args and e.args[0] in (-9, -13, -14):
                data = self._api(req, extra=extra, anon=True)
            else:
                raise
        self._stream_node(data, fnode["key"], target, on_delta)

    def _stream_node(self, data, key, target, on_delta=None):
        """Stream-decrypt a 'g' response straight into `target` with
        real per-chunk progress (no temp-file dance)."""
        if not isinstance(data, dict) or "g" not in data:
            raise RequestError("Node not downloadable")
        size = int(data.get("s") or 0)
        k = (key[0] ^ key[4], key[1] ^ key[5], key[2] ^ key[6], key[3] ^ key[7])
        iv = key[4:6] + (0, 0)

        raw = self._http.get(data["g"], stream=True, timeout=300).raw
        counter = Counter.new(128, initial_value=((iv[0] << 32) + iv[1]) << 64)
        aes = AES.new(a32_to_str(k), AES.MODE_CTR, counter=counter)
        # MAC integrity check skipped on purpose: halves CPU and MEGA already
        # serves over TLS; size is verified below.
        with open(target, "wb") as out:
            for _start, chunk_size in get_chunks(size):
                chunk = raw.read(chunk_size)
                if not chunk:
                    break
                out.write(aes.decrypt(chunk))
                if on_delta:
                    on_delta(len(chunk))
        if size and os.path.getsize(target) != size:
            raise RuntimeError(f"Incomplete download: {target}")

    @staticmethod
    def _tick(cb, done, delta, total):
        done["bytes"] += delta
        if cb:
            try:
                cb(done["bytes"], total)
            except Exception:
                pass

    @staticmethod
    def _unique_path(path):
        if not os.path.exists(path):
            return path
        stem, ext = os.path.splitext(path)
        i = 2
        while os.path.exists(f"{stem} ({i}){ext}"):
            i += 1
        return f"{stem} ({i}){ext}"

    # ── logged-in account access (sibling-volume rescue) ─────

    def account_files(self) -> list:
        """Files in the logged-in account's Cloud Drive/Inbox (not trash)."""
        if not self.logged_in:
            return []
        files = self._m.get_files()
        roots = {h for h, f in files.items() if f.get("t") in (2, 3)}
        trash = {h for h, f in files.items() if f.get("t") == 4}

        def in_cloud(h):
            seen = set()
            while h and h not in seen:
                seen.add(h)
                if h in roots:
                    return True
                if h in trash:
                    return False
                f = files.get(h)
                if not f:
                    return False
                h = f.get("p")
            return False

        out = []
        for h, f in files.items():
            if f.get("t") == 0 and isinstance(f.get("a"), dict) \
                    and "key" in f and in_cloud(h):
                out.append({"h": h, "name": f["a"].get("n") or h,
                            "size": int(f.get("s") or 0), "node": f})
        return out

    def download_account_file(self, f: dict, dest_dir: str, progress_cb=None) -> str:
        """Download one of the user's own account files into dest_dir."""
        os.makedirs(dest_dir, exist_ok=True)
        target = self._unique_path(os.path.join(dest_dir, f["name"] or f["h"]))
        size = f["size"]
        done = {"b": 0}

        def on_delta(d):
            done["b"] += d
            if progress_cb:
                try:
                    progress_cb(done["b"], size)
                except Exception:
                    pass

        # own-account node: 'n' handle + session sid, no folder key needed
        self._download_node({"h": f["h"], "size": size, "key": f["node"]["key"]},
                            target, on_delta=on_delta)
        return target