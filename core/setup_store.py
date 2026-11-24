"""First-run setup: what the wizard asks, how it's checked, and how it's saved.

The wizard (gui/setup_wizard.py) only collects answers. Nothing is written until the owner presses
Finish; then `save()` writes everything in one go:

  • owner profile         → config/identity.json         (their name, how to address them, languages, city)
  • AI keys               → config/api_keys.json         (encrypted with DPAPI, core/secrets.py)
  • Telegram bot          → config/telegram_bot.json     (encrypted)
  • voice/audio/look/behaviour → config/app_settings.json
  • quiet hours           → core/quiet.py, start with Windows → core/app_integration.py

The developer credit is NOT asked or stored here: it's fixed in core/brand.py.
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.paths import CONFIG_DIR, ensure_data_dirs
from core.secrets import KEYS_FILE, TELEGRAM_FILE

SETUP_VERSION = 1
SETTINGS_FILE = CONFIG_DIR / "app_settings.json"
IDENTITY_FILE = CONFIG_DIR / "identity.json"

ADDRESS_FORMS = ("sir", "ma'am", "name", "boss")
GEMINI_KEY_RX = re.compile(r"^AIza[0-9A-Za-z_\-]{35}$")
TELEGRAM_TOKEN_RX = re.compile(r"^\d{6,12}:[A-Za-z0-9_\-]{30,}$")
HEX_RX = re.compile(r"^#[0-9a-fA-F]{6}$")

DEFAULTS: dict[str, Any] = {
    "owner_name": "", "address_as": "sir", "languages": ["English"], "city": "",
    "gemini_key": "", "openrouter_key": "", "groq_key": "", "anthropic_key": "",
    "input_device": "", "output_device": "", "voice_name": "Charon", "push_to_talk": False,
    "app_theme": "#ffb300", "assistant_name": "Jarvis",
    "telegram_token": "", "telegram_chat_id": "",
    "start_with_windows": True, "quiet_enabled": False, "quiet_start": "23:00", "quiet_end": "07:00",
    "security_watch": True, "clipboard_helper": True, "lock_screen_content": False,
}


# ── reading the current state ────────────────────────────────────────────────
def _json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig")) or default
    except Exception:
        return default


def settings() -> dict:
    return _json(SETTINGS_FILE, {})


def needs_setup() -> bool:
    """True on a fresh install (no owner name or no Gemini key yet)."""
    keys = _json(KEYS_FILE, {})
    ident = _json(IDENTITY_FILE, {})
    owner = (ident.get("owner") or {}).get("name", "") if isinstance(ident, dict) else ""
    return not (str(keys.get("gemini_api_key") or "").strip() and str(owner).strip())


def current_answers() -> dict:
    """Pre-fill the wizard with whatever is already set (so re-running setup is an edit, not a reset)."""
    a = dict(DEFAULTS)
    ident = _json(IDENTITY_FILE, {})
    owner = ident.get("owner") or {}
    a["owner_name"] = owner.get("name", "")
    a["city"] = owner.get("location", "")
    a["address_as"] = owner.get("address_as", a["address_as"])
    a["languages"] = owner.get("languages") or a["languages"]
    a["assistant_name"] = (ident.get("assistant") or {}).get("name", "Jarvis")
    keys = _json(KEYS_FILE, {})
    a["gemini_key"] = keys.get("gemini_api_key", "")
    a["openrouter_key"] = keys.get("openrouter_api_key", "")
    a["groq_key"] = keys.get("groq_api_key", "")
    a["anthropic_key"] = keys.get("anthropic_api_key", "")
    tg = _json(TELEGRAM_FILE, {})
    a["telegram_token"] = tg.get("bot_token", "")
    a["telegram_chat_id"] = str(tg.get("owner_chat_id") or "")
    st = settings()
    for k in ("input_device", "output_device", "voice_name", "push_to_talk", "app_theme"):
        if st.get(k) not in (None, ""):
            a[k] = st[k]
    a["security_watch"] = bool(st.get("security_watch_enabled", True))
    a["clipboard_helper"] = bool(st.get("clipboard_helper_enabled", True))
    a["lock_screen_content"] = bool(st.get("notify_content_on_lock_screen", False))
    q = st.get("quiet_hours") or {}
    a["quiet_enabled"] = bool(q.get("enabled", False))
    a["quiet_start"] = q.get("start", "23:00")
    a["quiet_end"] = q.get("end", "07:00")
    try:
        from core.app_integration import get_start_with_windows
        a["start_with_windows"] = get_start_with_windows() or not st.get("setup_version")
    except Exception:
        pass
    return a


# ── validation (one message per problem, in plain words) ─────────────────────
def validate(a: dict) -> dict[str, str]:
    errs: dict[str, str] = {}
    name = str(a.get("owner_name") or "").strip()
    if not name:
        errs["owner_name"] = "Please enter your name so Jarvis knows who it works for."
    elif len(name) > 60:
        errs["owner_name"] = "That name is too long (60 characters max)."
    if str(a.get("address_as")) not in ADDRESS_FORMS:
        errs["address_as"] = "Choose how Jarvis should address you."
    key = str(a.get("gemini_key") or "").strip()
    if not key:
        errs["gemini_key"] = "A Gemini key is needed for Jarvis's voice and brain (it's free)."
    elif not GEMINI_KEY_RX.match(key):
        errs["gemini_key"] = "That doesn't look like a Gemini key (it starts with 'AIza' and is 39 characters)."
    tok = str(a.get("telegram_token") or "").strip()
    if tok and not TELEGRAM_TOKEN_RX.match(tok):
        errs["telegram_token"] = "That doesn't look like a Telegram bot token (like 123456789:AA…)."
    cid = str(a.get("telegram_chat_id") or "").strip()
    if cid and not re.fullmatch(r"-?\d{5,15}", cid):
        errs["telegram_chat_id"] = "The chat ID should be a number."
    if not HEX_RX.match(str(a.get("app_theme") or "")):
        errs["app_theme"] = "Pick a colour."
    an = str(a.get("assistant_name") or "").strip()
    if not an or len(an) > 20:
        errs["assistant_name"] = "The assistant's name should be 1–20 characters."
    try:
        from core.routines import parse_time
        if a.get("quiet_enabled"):
            parse_time(str(a.get("quiet_start")))
            parse_time(str(a.get("quiet_end")))
    except Exception:
        errs["quiet_hours"] = "Quiet hours need times like 23:00 and 07:00."
    return errs


# ── checks the wizard's Test buttons use (network; never called from tests) ───
def test_gemini_key(key: str, timeout: float = 12.0) -> tuple[bool, str]:
    key = (key or "").strip()
    if not GEMINI_KEY_RX.match(key):
        return False, "That doesn't look like a Gemini key."
    try:
        import requests
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                         headers={"x-goog-api-key": key}, timeout=timeout)
        if r.status_code == 200:
            return True, "Key works."
        if r.status_code in (400, 401, 403):
            return False, "Google rejected this key. Check you copied all of it."
        return False, f"Google answered {r.status_code}. Try again in a moment."
    except Exception as exc:
        return False, f"Couldn't reach Google ({type(exc).__name__}). Check the internet connection."


def telegram_find_chat(token: str, timeout: float = 10.0) -> tuple[dict | None, str]:
    """After the owner messages their bot, find who sent it (so they can confirm 'is this you?')."""
    token = (token or "").strip()
    if not TELEGRAM_TOKEN_RX.match(token):
        return None, "That doesn't look like a bot token."
    try:
        import requests
        me = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=timeout).json()
        if not me.get("ok"):
            return None, "Telegram rejected this token."
        upd = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", params={"limit": 20}, timeout=timeout).json()
        msgs = [u.get("message") for u in upd.get("result", []) if u.get("message")]
        private = [m for m in msgs if (m.get("chat") or {}).get("type") == "private"]
        if not private:
            return None, f"Now open Telegram, send any message to @{me['result'].get('username', 'your bot')}, then press Find me again."
        m = private[-1]
        who = m.get("from") or {}
        name = " ".join(x for x in (who.get("first_name"), who.get("last_name")) if x) or who.get("username") or "someone"
        return {"chat_id": str(m["chat"]["id"]), "name": name, "username": who.get("username") or "",
                "text": (m.get("text") or "")[:60]}, f"Found {name}. Is this you?"
    except Exception as exc:
        return None, f"Couldn't reach Telegram ({type(exc).__name__})."


def audio_devices() -> tuple[list[str], list[str]]:
    try:
        import sounddevice as sd
        devs = sd.query_devices()
        ins = sorted({d["name"] for d in devs if d["max_input_channels"] > 0})
        outs = sorted({d["name"] for d in devs if d["max_output_channels"] > 0})
        return ins, outs
    except Exception:
        return [], []


# ── saving (all at once, at the end) ─────────────────────────────────────────
def _merge_json(path, update: dict) -> dict:
    data = _json(path, {})
    if not isinstance(data, dict):
        data = {}
    data.update(update)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=4, ensure_ascii=False), encoding="utf-8")
    return data


def save(a: dict) -> dict:
    """Write every answer. Raises ValueError (with the problems) if anything is invalid."""
    a = {**DEFAULTS, **(a or {})}
    errs = validate(a)
    if errs:
        raise ValueError("; ".join(errs.values()))
    ensure_data_dirs()
    name = str(a["owner_name"]).strip()
    addr = a["address_as"]
    preferred = {"sir": f"{name.split()[0]} Sir", "ma'am": f"{name.split()[0]} Ma'am",
                 "boss": "Boss", "name": name.split()[0]}[addr]

    ident = _json(IDENTITY_FILE, {})
    ident.setdefault("owner", {}).update({"name": name, "preferred_name": preferred, "address_as": addr,
                                          "location": str(a.get("city") or "").strip(),
                                          "languages": [str(x) for x in (a.get("languages") or ["English"])][:6]})
    ident.setdefault("assistant", {}).update({"name": str(a["assistant_name"]).strip(),
                                              "application_name": str(a["assistant_name"]).strip()})
    IDENTITY_FILE.parent.mkdir(parents=True, exist_ok=True)
    IDENTITY_FILE.write_text(json.dumps(ident, indent=4, ensure_ascii=False), encoding="utf-8")

    keys = _json(KEYS_FILE, {})
    keys.update({"gemini_api_key": str(a["gemini_key"]).strip(), "os_system": "windows"})
    for field, k in (("openrouter_key", "openrouter_api_key"), ("groq_key", "groq_api_key"),
                     ("anthropic_key", "anthropic_api_key")):
        if str(a.get(field) or "").strip():
            keys[k] = str(a[field]).strip()
    KEYS_FILE.write_text(json.dumps(keys, indent=4), encoding="utf-8")

    if str(a.get("telegram_token") or "").strip():
        TELEGRAM_FILE.write_text(json.dumps({"bot_token": str(a["telegram_token"]).strip(),
                                             "owner_chat_id": str(a.get("telegram_chat_id") or "").strip()},
                                            indent=4), encoding="utf-8")

    from core.routines import parse_time
    _merge_json(SETTINGS_FILE, {
        "input_device": a.get("input_device") or "", "output_device": a.get("output_device") or "",
        "voice_name": a.get("voice_name") or "Charon", "push_to_talk": bool(a.get("push_to_talk")),
        "app_theme": a["app_theme"], "security_watch_enabled": bool(a.get("security_watch", True)),
        "clipboard_helper_enabled": bool(a.get("clipboard_helper", True)),
        "notify_content_on_lock_screen": bool(a.get("lock_screen_content", False)),
        "quiet_hours": {"enabled": bool(a.get("quiet_enabled")),
                        "start": parse_time(str(a.get("quiet_start") or "23:00")),
                        "end": parse_time(str(a.get("quiet_end") or "07:00"))},
        "setup_version": SETUP_VERSION,
    })
    try:
        from core.app_integration import set_start_with_windows
        set_start_with_windows(bool(a.get("start_with_windows")))
    except Exception:
        pass
    try:
        from core.notify import register
        register()           # re-apply the lock-screen privacy choice
    except Exception:
        pass
    return {"name": name, "preferred": preferred, "assistant": str(a["assistant_name"]).strip()}


def first_hello(result: dict) -> str:
    return (f"Hello {result['preferred']}. I'm {result['assistant']}, and I'm all set up. "
            "Ask me anything, or say 'what can you do?' to get started.")
