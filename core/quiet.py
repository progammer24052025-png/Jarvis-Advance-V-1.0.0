"""Quiet hours: a window in which Jarvis doesn't speak unless asked.

Held back during quiet hours (spoken later, when quiet hours end):
  • proactive check-ins, background-task announcements, non-urgent security notes
Still allowed:
  • anything you ask for, routines you scheduled for an exact time, urgent
    (high-severity) security alerts. Telegram messages are always sent.

Stored in config/app_settings.json as
  "quiet_hours": {"enabled": true, "start": "23:00", "end": "07:00"}
"""

from __future__ import annotations

from core.paths import DATA_DIR  # owner data lives apart from the program
import json
import threading
from datetime import datetime, time as dtime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SETTINGS = DATA_DIR / "config" / "app_settings.json"
_held: list[str] = []
_lock = threading.Lock()


def _settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def get_config() -> dict:
    q = _settings().get("quiet_hours") or {}
    return {"enabled": bool(q.get("enabled")), "start": q.get("start", "23:00"), "end": q.get("end", "07:00")}


def set_config(enabled: bool | None = None, start: str | None = None, end: str | None = None) -> dict:
    from core.routines import parse_time
    data = _settings()
    q = get_config()
    if enabled is not None:
        q["enabled"] = bool(enabled)
    if start:
        q["start"] = parse_time(start)
    if end:
        q["end"] = parse_time(end)
    data["quiet_hours"] = q
    SETTINGS.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS.write_text(json.dumps(data, indent=4), encoding="utf-8")
    return q


def _t(s: str) -> dtime:
    h, m = (int(x) for x in s.split(":"))
    return dtime(h, m)


def is_quiet(now: datetime | None = None) -> bool:
    q = get_config()
    if not q["enabled"]:
        return False
    now_t = (now or datetime.now()).time()
    start, end = _t(q["start"]), _t(q["end"])
    if start == end:
        return False
    if start < end:
        return start <= now_t < end
    return now_t >= start or now_t < end   # window crosses midnight


def hold(message: str) -> None:
    """Keep an unprompted announcement for when quiet hours end."""
    with _lock:
        _held.append(message)
        del _held[:-20]


def take_held() -> list[str]:
    with _lock:
        out = list(_held)
        _held.clear()
        return out


def describe() -> str:
    q = get_config()
    if not q["enabled"]:
        return "Quiet hours are off."
    return f"Quiet hours are on from {q['start']} to {q['end']}" + (" (quiet right now)." if is_quiet() else ".")


def quiet_hours_tool(params: dict) -> str:
    action = str(params.get("action") or "status").lower()
    if action in ("set", "on", "enable"):
        q = set_config(True, params.get("start"), params.get("end"))
        return f"Done. Quiet hours: {q['start']} to {q['end']}. I'll hold back anything you didn't ask for during that time."
    if action in ("off", "disable"):
        set_config(False)
        return "Quiet hours are off."
    return describe()
