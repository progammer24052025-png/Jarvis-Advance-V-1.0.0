"""Secret files (API keys, bot tokens) encrypted at rest with Windows DPAPI.

`KEYS_FILE` and anything wrapped with `protect()` behave like normal pathlib paths, so existing
code that does `json.loads(KEYS_FILE.read_text())` or `with KEYS_FILE.open("w") as f: json.dump(...)`
keeps working, but what's on disk is an encrypted blob that only this Windows account can open:

    {"_jarvis_encrypted": "dpapi-v1", "data": "<base64>"}

  • a plain (old-style) file is read normally and re-saved encrypted the first time it's read
  • copying the file to another PC or user account is useless: DPAPI ties it to this account
  • off Windows (or without pywin32) files stay plain, so tests and other platforms still work
"""

from __future__ import annotations

import base64
import io
import json
import os
import threading
from pathlib import Path

from core.paths import CONFIG_DIR

MARK = "_jarvis_encrypted"
VERSION = "dpapi-v1"
_targets: set[str] = set()
_lock = threading.RLock()


def _norm(p) -> str:
    return os.path.normcase(os.path.abspath(os.fspath(p)))


def dpapi_available() -> bool:
    try:
        import win32crypt  # noqa: F401
        return True
    except Exception:
        return False


def _encrypt(text: str) -> str:
    import win32crypt
    blob = win32crypt.CryptProtectData(text.encode("utf-8"), "Jarvis secrets", None, None, None, 0)
    return json.dumps({MARK: VERSION, "data": base64.b64encode(blob).decode("ascii")})


def _decrypt(raw: str) -> str:
    import win32crypt
    data = json.loads(raw)
    blob = base64.b64decode(data["data"])
    return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1].decode("utf-8")


def _raw_read(path) -> str:
    with open(os.fspath(path), "r", encoding="utf-8-sig") as f:
        return f.read()


def _raw_write(path, text: str) -> None:
    with open(os.fspath(path), "w", encoding="utf-8") as f:
        f.write(text)


def is_encrypted_text(raw: str) -> bool:
    return MARK in raw[:80] and raw.lstrip().startswith("{")


class SecretPath(Path):
    """A path whose file content is transparently decrypted on read and encrypted on write."""

    def _is_secret(self) -> bool:
        return _norm(self) in _targets

    # ── reads ──
    def read_text(self, encoding=None, errors=None, newline=None):
        if not self._is_secret():
            return super().read_text(encoding=encoding, errors=errors)
        with _lock:
            raw = _raw_read(self)
            if is_encrypted_text(raw):
                return _decrypt(raw)
            # old plain file: return it, and encrypt it in place from now on
            if raw.strip() and dpapi_available() and os.environ.get("JARVIS_SECRETS_MIGRATE", "1") != "0":
                try:
                    json.loads(raw)
                    _raw_write(self, _encrypt(raw))
                except Exception:
                    pass
            return raw

    def read_bytes(self):
        if not self._is_secret():
            return super().read_bytes()
        return self.read_text().encode("utf-8")

    # ── writes ──
    def write_text(self, data, encoding=None, errors=None, newline=None):
        if not self._is_secret():
            return super().write_text(data, encoding=encoding, errors=errors, newline=newline)
        with _lock:
            self.parent.mkdir(parents=True, exist_ok=True)
            out = _encrypt(data) if dpapi_available() else data
            tmp = os.fspath(self) + ".tmp"
            _raw_write(tmp, out)
            os.replace(tmp, os.fspath(self))
            return len(data)

    def write_bytes(self, data):
        if not self._is_secret():
            return super().write_bytes(data)
        return self.write_text(bytes(data).decode("utf-8"))

    def open(self, mode="r", buffering=-1, encoding=None, errors=None, newline=None):
        if not self._is_secret():
            return super().open(mode, buffering, encoding, errors, newline)
        if "r" in mode and "+" not in mode:
            if not self.exists():
                raise FileNotFoundError(str(self))
            text = self.read_text()
            return io.BytesIO(text.encode("utf-8")) if "b" in mode else io.StringIO(text)
        if "w" in mode:
            return _SecretWriter(self, binary="b" in mode)
        raise ValueError(f"unsupported mode for a secret file: {mode}")


class _SecretWriter(io.StringIO):
    def __init__(self, path: SecretPath, binary: bool = False):
        super().__init__()
        self._path = path
        self._binary = binary

    def write(self, s):
        if isinstance(s, (bytes, bytearray)):
            s = bytes(s).decode("utf-8")
        return super().write(s)

    def close(self):
        if not self.closed:
            self._path.write_text(self.getvalue())
        super().close()

    def __exit__(self, *exc):
        if exc[0] is None:
            self.close()
        else:
            super().close()
        return False


def protect(path) -> SecretPath:
    """Wrap any path so its file is kept encrypted (registers it as a secret file)."""
    sp = SecretPath(path)
    _targets.add(_norm(sp))
    return sp


def secret_path(name: str) -> SecretPath:
    return protect(CONFIG_DIR / name)


def load_json(path, default=None):
    try:
        return json.loads(protect(path).read_text(encoding="utf-8")) or (default if default is not None else {})
    except Exception:
        return default if default is not None else {}


def save_json(path, data) -> None:
    protect(path).write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")


KEYS_FILE = secret_path("api_keys.json")
TELEGRAM_FILE = secret_path("telegram_bot.json")
DISCORD_FILE = secret_path("discord_bot.json")
