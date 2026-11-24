"""Activity log: what Jarvis delivered, held, dropped, ran, adjusted or blocked, and why.

Kept in memory (last 500) and appended to logs/activity.jsonl in the owner's data folder,
so "why didn't you tell me?" and "what did you do today?" are answered from real data.
"""

from __future__ import annotations

import json
import threading
import time
from collections import deque
from datetime import datetime

from core.paths import LOGS_DIR

LOG_FILE = LOGS_DIR / "activity.jsonl"
_MAX_FILE = 2_000_000
_buf: deque = deque(maxlen=500)
_lock = threading.Lock()


def record(kind: str, what: str, decision: str, reason: str = "", rule_id: str = "", **extra) -> dict:
    """kind: output | tool | rule | system."""
    entry = {"t": time.time(), "kind": kind, "what": str(what)[:300], "decision": decision,
             "reason": str(reason)[:300], "rule": rule_id}
    entry.update({k: (v if isinstance(v, (int, float, bool)) else str(v)[:300]) for k, v in extra.items()})
    with _lock:
        _buf.append(entry)
        try:
            LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
            if LOG_FILE.exists() and LOG_FILE.stat().st_size > _MAX_FILE:
                LOG_FILE.replace(LOG_FILE.with_suffix(".old.jsonl"))
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except OSError:
            pass
    return entry


def recent(n: int = 50, kind: str | None = None, query: str = "") -> list[dict]:
    with _lock:
        items = list(_buf)
    if kind:
        items = [e for e in items if e["kind"] == kind]
    if query:
        q = query.lower()
        items = [e for e in items if q in (e["what"] + " " + e["reason"]).lower()]
    return items[-n:]


def explain(query: str = "", n: int = 8) -> str:
    """Plain-words answer for 'why didn't you…' / 'what did you do'."""
    items = recent(n, query=query) or recent(n)
    if not items:
        return "Nothing has been logged yet."
    lines = []
    for e in items:
        when = datetime.fromtimestamp(e["t"]).strftime("%H:%M")
        why = f" ({e['reason']})" if e["reason"] else ""
        lines.append(f"{when} · {e['kind']} · {e['what']} → {e['decision']}{why}")
    return "\n".join(lines)
