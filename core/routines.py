"""Routines: things Jarvis does on its own, on a schedule, that survive restarts.

Examples
  "Every morning at 8, give me a briefing"            daily 08:00
  "Every weekday at 9:30, check my store's website"   mon-fri 09:30
  "Every 2 hours, check if Bitcoin is above 100k"     every 120 minutes

Stored in config/routines.json. When a routine is due, its prompt is handed to
Jarvis as a command from source "routine". The Guardian treats that source as
unattended, so anything high-risk a routine tries is held until the owner
confirms. A routine can never silently delete, send or install things.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

DAY_NAMES = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def parse_days(text: str | None) -> list[int]:
    """'daily' | 'weekdays' | 'weekends' | 'mon,wed,fri' | 'mon-fri' → [0..6]."""
    t = (text or "daily").strip().lower().replace(" ", "")
    if t in ("", "daily", "everyday", "all"):
        return list(range(7))
    if t in ("weekdays", "workdays"):
        return [0, 1, 2, 3, 4]
    if t in ("weekends", "weekend"):
        return [5, 6]
    days: set[int] = set()
    for part in t.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            ia, ib = DAY_NAMES.index(a[:3]), DAY_NAMES.index(b[:3])
            rng = range(ia, ib + 1) if ia <= ib else list(range(ia, 7)) + list(range(0, ib + 1))
            days.update(rng)
        elif part[:3] in DAY_NAMES:
            days.add(DAY_NAMES.index(part[:3]))
    return sorted(days) or list(range(7))


def parse_time(text: str) -> str:
    """'8', '8am', '20:30', '8:30 pm' → 'HH:MM'."""
    t = text.strip().lower().replace(" ", "").replace(".", ":")
    pm = t.endswith("pm")
    am = t.endswith("am")
    t = t.rstrip("apm")
    if ":" in t:
        h, m = t.split(":", 1)
    else:
        h, m = t, "0"
    h, m = int(h), int(m or 0)
    if pm and h < 12:
        h += 12
    if am and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= m < 60):
        raise ValueError(f"Invalid time: {text}")
    return f"{h:02d}:{m:02d}"


class RoutineStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()

    def load(self) -> list[dict]:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    def save(self, items: list[dict]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self.path)

    def add(self, name: str, prompt: str, *, time_of_day: str | None = None, days: str | None = None,
            every_minutes: int | None = None, notify: str = "voice") -> dict:
        if not prompt.strip():
            raise ValueError("A routine needs something to do.")
        if every_minutes:
            sched = {"type": "interval", "minutes": max(5, int(every_minutes))}
        else:
            sched = {"type": "daily", "time": parse_time(time_of_day or "09:00"), "days": parse_days(days)}
        notify = notify if notify in ("voice", "telegram", "both") else "voice"
        item = {
            "id": uuid.uuid4().hex[:8], "name": (name or prompt)[:60], "prompt": prompt.strip(),
            "schedule": sched, "notify": notify, "enabled": True,
            "created": datetime.now().isoformat(timespec="seconds"), "last_run": None,
        }
        with self._lock:
            items = self.load()
            items.append(item)
            self.save(items)
        return item

    def find(self, key: str) -> dict | None:
        key = (key or "").strip().lower()
        for r in self.load():
            if r["id"] == key or r["name"].lower() == key or key in r["name"].lower():
                return r
        return None

    def update(self, rid: str, **changes) -> dict | None:
        with self._lock:
            items = self.load()
            for r in items:
                if r["id"] == rid:
                    r.update(changes)
                    self.save(items)
                    return r
        return None

    def remove(self, rid: str) -> bool:
        with self._lock:
            items = self.load()
            kept = [r for r in items if r["id"] != rid]
            if len(kept) == len(items):
                return False
            self.save(kept)
            return True


def describe(r: dict) -> str:
    s = r["schedule"]
    if s["type"] == "interval":
        when = f"every {s['minutes']} min"
    else:
        days = s.get("days", list(range(7)))
        dtxt = "daily" if len(days) == 7 else ("weekdays" if days == [0, 1, 2, 3, 4] else ",".join(DAY_NAMES[d] for d in days))
        when = f"{dtxt} at {s['time']}"
    state = "" if r.get("enabled", True) else " (paused)"
    return f"[{r['id']}] {r['name']}: {when}, notify {r.get('notify', 'voice')}{state}"


def is_due(r: dict, now: datetime) -> bool:
    if not r.get("enabled", True):
        return False
    last = datetime.fromisoformat(r["last_run"]) if r.get("last_run") else None
    s = r["schedule"]
    if s["type"] == "interval":
        return last is None or now - last >= timedelta(minutes=s["minutes"])
    if now.weekday() not in s.get("days", list(range(7))):
        return False
    h, m = map(int, s["time"].split(":"))
    slot = now.replace(hour=h, minute=m, second=0, microsecond=0)
    # due once the slot has passed today, at most once per day, and not more than
    # 3 hours late (so a PC switched on at night doesn't fire the 8 a.m. briefing)
    return slot <= now <= slot + timedelta(hours=3) and (last is None or last < slot)


class RoutineRunner:
    def __init__(self, store: RoutineStore, fire: Callable[[dict], None], tick_secs: float = 20.0):
        self.store = store
        self.fire = fire
        self.tick = tick_secs
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="routines")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def run_now(self, r: dict) -> None:
        self.store.update(r["id"], last_run=datetime.now().isoformat(timespec="seconds"))
        self.fire(r)

    def _loop(self) -> None:
        time.sleep(30)  # let Jarvis finish booting first
        while not self._stop.is_set():
            now = datetime.now()
            for r in self.store.load():
                try:
                    if is_due(r, now):
                        self.run_now(r)
                except Exception as exc:
                    print(f"[Routines] {r.get('name')}: {exc}")
            self._stop.wait(self.tick)


def routine_manager(store: RoutineStore, runner: RoutineRunner | None, params: dict) -> str:
    """Tool entry point used by Jarvis."""
    action = str(params.get("action") or "list").lower()
    if action == "list":
        items = store.load()
        return "\n".join(describe(r) for r in items) if items else "No routines yet."
    if action == "add":
        every = params.get("every_minutes")
        r = store.add(
            name=str(params.get("name") or ""),
            prompt=str(params.get("prompt") or ""),
            time_of_day=params.get("time"),
            days=params.get("days"),
            every_minutes=int(every) if every not in (None, "", 0, "0") else None,
            notify=str(params.get("notify") or "voice"),
        )
        return "Routine saved: " + describe(r)
    target = store.find(str(params.get("routine") or params.get("name") or params.get("id") or ""))
    if target is None:
        return "I couldn't find that routine. Say 'list my routines' to see them."
    if action in ("remove", "delete"):
        store.remove(target["id"])
        return f"Removed routine '{target['name']}'."
    if action in ("pause", "disable"):
        store.update(target["id"], enabled=False)
        return f"Paused '{target['name']}'."
    if action in ("resume", "enable"):
        store.update(target["id"], enabled=True)
        return f"Resumed '{target['name']}'."
    if action in ("run", "run_now"):
        if runner is None:
            return "The routine runner isn't active."
        threading.Thread(target=runner.run_now, args=(target,), daemon=True).start()
        return f"Running '{target['name']}' now."
    return f"Unknown routine action '{action}'."
