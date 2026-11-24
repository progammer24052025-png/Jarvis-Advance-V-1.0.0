"""Telegram bridge: talk to Jarvis from anywhere, owner-only.

Setup (once):
  1. In Telegram, message @BotFather → /newbot → copy the bot token.
  2. Put it in config/telegram_bot.json:  {"bot_token": "...", "owner_chat_id": ""}
  3. Start Jarvis and send your bot any message. Jarvis logs
     "Telegram: ignored message from <you> (chat id 123...)".
  4. Copy that number into "owner_chat_id" and restart Jarvis.

From then on only that chat can command Jarvis. Everyone else is ignored
(and logged). No ports are opened: Jarvis polls Telegram over HTTPS, so it
works from anywhere without exposing your PC.

Quick commands: /status  /routines  /security  /help
Anything else is passed to Jarvis as if you had typed it.
"""

from __future__ import annotations

from core.secrets import protect  # keys/tokens are encrypted at rest (DPAPI)
import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import requests

API = "https://api.telegram.org/bot{token}/{method}"


class TelegramBridge:
    def __init__(self, base_dir: Path, log: Callable[[str], None] = print):
        self.base_dir = Path(base_dir)
        self.config_path = protect(self.base_dir / "config" / "telegram_bot.json")
        self._ui_log = log
        self._submit: Optional[Callable[[str, str], None]] = None
        self._commands: dict[str, Callable[[], str]] = {}
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._offset = 0
        self._session = requests.Session()

    def log(self, message: str) -> None:
        """Every Telegram event goes to the console + logs/jarvis.log AND the Jarvis window."""
        text = message.replace("SYS: ", "", 1).replace("Telegram: ", "", 1)
        try:
            print(f"[Telegram] {text}")
        except Exception:
            pass  # never let logging break message handling
        if self._ui_log is not print:
            try:
                self._ui_log(message)
            except Exception:
                pass

    # ── config ───────────────────────────────────────────────────────────
    def _cfg(self) -> dict:
        try:
            return json.loads(self.config_path.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    @property
    def token(self) -> str:
        return str(self._cfg().get("bot_token") or "").strip()

    @property
    def owner_chat_id(self) -> int | None:
        try:
            v = int(str(self._cfg().get("owner_chat_id") or "").strip())
            return v if v != 0 else None
        except Exception:
            return None

    def configured(self) -> bool:
        return bool(self.token) and self.owner_chat_id is not None

    # ── wiring ───────────────────────────────────────────────────────────
    def bind_submitter(self, fn: Callable[[str, str], None]) -> None:
        self._submit = fn

    def add_command(self, name: str, fn: Callable[[], str]) -> None:
        self._commands[name.lstrip("/").lower()] = fn

    def start(self) -> bool:
        if not self.token:
            if not self.config_path.exists():
                self.config_path.parent.mkdir(parents=True, exist_ok=True)
                self.config_path.write_text(json.dumps({"bot_token": "", "owner_chat_id": ""}, indent=2), encoding="utf-8")
            return False
        if self._thread and self._thread.is_alive():
            return True
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="telegram-bridge")
        self._thread.start()
        self.log("SYS: Telegram bridge online." if self.owner_chat_id else
                 "SYS: Telegram bridge waiting for owner. Message your bot once, then copy your chat id into config/telegram_bot.json.")
        return True

    def stop(self) -> None:
        self._stop.set()

    # ── sending ──────────────────────────────────────────────────────────
    def send(self, text: str) -> bool:
        """Send a message to the owner (no-op if not configured)."""
        chat = self.owner_chat_id
        token = self.token
        text = (text or "").strip()
        if not (chat and token and text):
            return False
        ok = True
        for i in range(0, len(text), 3900):
            try:
                r = self._session.post(API.format(token=token, method="sendMessage"),
                                       json={"chat_id": chat, "text": text[i:i + 3900]}, timeout=15)
                ok = ok and r.ok
            except Exception:
                ok = False
        self.log(("📤 reply sent: " if ok else "⚠️ reply FAILED: ") + text[:60].replace("\n", " "))
        return ok

    def _typing(self) -> None:
        try:
            self._session.post(API.format(token=self.token, method="sendChatAction"),
                               json={"chat_id": self.owner_chat_id, "action": "typing"}, timeout=5)
        except Exception:
            pass

    # ── receiving ────────────────────────────────────────────────────────
    def _loop(self) -> None:
        backoff = 2
        while not self._stop.is_set():
            token = self.token
            if not token:
                time.sleep(10)
                continue
            try:
                r = self._session.get(API.format(token=token, method="getUpdates"),
                                      params={"timeout": 25, "offset": self._offset, "allowed_updates": json.dumps(["message"])},
                                      timeout=35)
                data = r.json()
                if not data.get("ok"):
                    raise RuntimeError(data.get("description") or f"HTTP {r.status_code}")
                for upd in data.get("result", []):
                    self._offset = max(self._offset, int(upd["update_id"]) + 1)
                    self._handle(upd.get("message") or {})
                backoff = 2
            except Exception as exc:
                msg = str(exc)
                if "409" in msg or "Conflict" in msg or "terminated by other getUpdates" in msg:
                    msg = ("Another copy of Jarvis is already using this Telegram bot. "
                           "Close the extra copy (only one Jarvis can read the bot at a time).")
                self.log(f"⚠️ poll error: {msg}")
                time.sleep(backoff)
                backoff = min(backoff * 2, 120)

    def _handle(self, msg: dict) -> None:
        chat = (msg.get("chat") or {})
        chat_id = chat.get("id")
        sender = (msg.get("from") or {})
        name = sender.get("username") or sender.get("first_name") or "unknown"
        text = (msg.get("text") or "").strip()
        owner = self.owner_chat_id

        if owner is None or chat_id != owner:
            self.log(f"SYS: Telegram: ignored message from {name} (chat id {chat_id})."
                     + (" Put this chat id in config/telegram_bot.json as owner_chat_id if it's you." if owner is None else " Not the owner."))
            return
        if not text:
            self.send("I can read text messages for now. Type what you need.")
            return

        if text.startswith("/"):
            cmd = text[1:].split()[0].split("@")[0].lower()
            if cmd in ("start", "help"):
                self.send("Jarvis here. Just type to me like you talk to me at the PC.\n"
                          "/status - am I online\n/routines - your scheduled routines\n/security - run a security check now")
                return
            if cmd in self._commands:
                try:
                    self.send(self._commands[cmd]() or "Done.")
                except Exception as exc:
                    self.send(f"That failed: {exc}")
                return

        if self._submit is None:
            self.send("I'm still starting up. Try again in a moment.")
            return
        self.log(f"📩 from you: {text[:80]}")
        self._typing()
        self._submit(text, "telegram")
