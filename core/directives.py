"""Owner rules ("directives"): the owner's own instructions, enforced by code.

Every instruction about how Jarvis should talk or act ("don't talk until 8, text me on Telegram",
"always ask before sending messages", "use Edge", "stop everything") becomes a Rule here.
Two gates read these rules: core/attention.py (what Jarvis says) and core/tool_policy.py (what it does).

Safety:
  • only owner turns on trusted channels can make rules (checked by the caller via Guardian)
  • the rules file is signed (HMAC-SHA256, key protected by Windows DPAPI). If it's edited outside
    Jarvis, the edited rules are ignored, the file is set aside, and Jarvis falls back to "ask before
    risky actions" until the owner sets rules again
  • a few things can never be ruled away: see SAFETY_FLOOR below
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
import uuid
from datetime import datetime, timedelta
from typing import Any, Callable

from core.paths import CONFIG_DIR

RULES_FILE = CONFIG_DIR / "directives.json"
KEY_FILE = CONFIG_DIR / ".rules_key"
AUDIT_FILE = CONFIG_DIR / "directive_audit.log"
SETTINGS_FILE = CONFIG_DIR / "app_settings.json"

VOICE_LEVELS = ("off", "urgent_only", "on")                 # most → least restrictive
IMPORTANCE = ("low", "normal", "important", "critical")
CHANNELS = ("voice", "hud", "toast", "telegram")
DECISIONS = ("auto", "ask", "ask_once", "deny", "prefer", "adjust", "limit")
TRUSTED_SOURCES = {"local", "voice", "mobile", "discord", "telegram"}

# Tools the owner can never lock themselves out of (or that keep them safe).
PROTECTED_TOOLS = {"owner_rules", "shutdown_Jarvis", "undo_last"}

SAFETY_FLOOR = (
    "Critical security alerts and approval requests always reach you on at least one text channel.",
    "Guardian's check on actions that follow outside content (web pages, emails, files) stays on.",
    "Rules can only come from you, never from text inside web pages, emails, files or the clipboard.",
    "Owner-control tools (rules, stop/resume, undo, shut down) can't be blocked.",
)

_lock = threading.RLock()
_rules: list[dict] | None = None
_tampered = False
_events_fired: set[str] = set()
_listeners: list[Callable[[str, dict], None]] = []


# ── feature switches (app_settings.json → "owner_control") ──────────────────
def feature_on(name: str) -> bool:
    try:
        cfg = (json.loads(SETTINGS_FILE.read_text(encoding="utf-8")) or {}).get("owner_control") or {}
    except Exception:
        cfg = {}
    return bool(cfg.get(name, True))


# ── integrity (HMAC with a DPAPI-protected key) ──────────────────────────────
def _key() -> bytes:
    try:
        blob = KEY_FILE.read_bytes()
        return _unprotect(blob)
    except Exception:
        pass
    key = secrets.token_bytes(32)
    try:
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        KEY_FILE.write_bytes(_protect(key))
    except OSError:
        pass
    return key


def _protect(data: bytes) -> bytes:
    try:
        import win32crypt
        return b"DPAPI" + win32crypt.CryptProtectData(data, "Jarvis rules key", None, None, None, 0)
    except Exception:
        return b"PLAIN" + data


def _unprotect(blob: bytes) -> bytes:
    if blob.startswith(b"DPAPI"):
        import win32crypt
        return win32crypt.CryptUnprotectData(blob[5:], None, None, None, 0)[1]
    if blob.startswith(b"PLAIN"):
        return blob[5:]
    raise ValueError("unknown key format")


def _sign(rules: list[dict]) -> str:
    body = json.dumps(rules, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return hmac.new(_key(), body, hashlib.sha256).hexdigest()


def _audit(event: str, rule: dict | None = None, note: str = "") -> None:
    """Append-only, hash-chained: each line carries the hash of the previous one."""
    try:
        AUDIT_FILE.parent.mkdir(parents=True, exist_ok=True)
        prev = "0" * 64
        if AUDIT_FILE.exists():
            with open(AUDIT_FILE, "rb") as f:
                try:
                    f.seek(-600, os.SEEK_END)
                except OSError:
                    f.seek(0)
                tail = f.read().decode("utf-8", "ignore").strip().splitlines()
            if tail:
                prev = hashlib.sha256(tail[-1].encode("utf-8")).hexdigest()
        line = json.dumps({"at": datetime.now().isoformat(timespec="seconds"), "event": event,
                           "rule": (rule or {}).get("id", ""), "said": (rule or {}).get("said", "")[:200],
                           "source": (rule or {}).get("source", ""), "note": note[:200], "prev": prev},
                          ensure_ascii=False)
        with open(AUDIT_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def verify_audit_chain() -> bool:
    try:
        lines = AUDIT_FILE.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return True
    prev = "0" * 64
    for ln in lines:
        try:
            if json.loads(ln).get("prev") != prev:
                return False
        except Exception:
            return False
        prev = hashlib.sha256(ln.encode("utf-8")).hexdigest()
    return True


# ── load / save ──────────────────────────────────────────────────────────────
def _load() -> list[dict]:
    global _rules, _tampered
    if _rules is not None:
        return _rules
    _rules = []
    if not RULES_FILE.exists():
        return _rules
    try:
        data = json.loads(RULES_FILE.read_text(encoding="utf-8"))
        rules = data.get("rules") or []
        if not hmac.compare_digest(str(data.get("sig", "")), _sign(rules)):
            raise ValueError("signature mismatch")
        _rules = rules
    except Exception as exc:
        _tampered = True
        try:
            RULES_FILE.replace(RULES_FILE.with_name(f"directives.tampered-{int(time.time())}.json"))
        except OSError:
            pass
        _audit("TAMPER_DETECTED", note=str(exc))
        _notify("tampered", {"note": str(exc)})
    return _rules


def _save() -> None:
    rules = _load()
    RULES_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = RULES_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "rules": rules, "sig": _sign(rules)}, indent=2, ensure_ascii=False),
                   encoding="utf-8")
    tmp.replace(RULES_FILE)


def reload() -> None:
    """Forget the in-memory copy (tests, or after the data folder changes)."""
    global _rules, _tampered
    with _lock:
        _rules = None
        _tampered = False


def tampered() -> bool:
    _load()
    return _tampered


def acknowledge_tamper() -> None:
    global _tampered
    _tampered = False
    _audit("TAMPER_ACKNOWLEDGED")


def on_change(fn: Callable[[str, dict], None]) -> None:
    _listeners.append(fn)


def _notify(what: str, rule: dict) -> None:
    for fn in list(_listeners):
        try:
            fn(what, rule)
        except Exception:
            pass


# ── time handling ────────────────────────────────────────────────────────────
def next_clock_time(hhmm: str, now: datetime | None = None) -> datetime:
    """'20:00' → the next 20:00 (today if still ahead, else tomorrow)."""
    from core.routines import parse_time
    now = now or datetime.now()
    h, m = (int(x) for x in parse_time(hhmm).split(":"))
    t = now.replace(hour=h, minute=m, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)


_DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _day_set(spec: str) -> set[int]:
    spec = (spec or "daily").lower().replace(" ", "")
    if spec in ("daily", "everyday", "all", ""):
        return set(range(7))
    if spec == "weekdays":
        return {0, 1, 2, 3, 4}
    if spec == "weekends":
        return {5, 6}
    out: set[int] = set()
    for part in spec.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            a, b = _DAYS.get(a[:3]), _DAYS.get(b[:3])
            if a is not None and b is not None:
                i = a
                while True:
                    out.add(i)
                    if i == b:
                        break
                    i = (i + 1) % 7
        elif part[:3] in _DAYS:
            out.add(_DAYS[part[:3]])
    return out or set(range(7))


def is_active(rule: dict, now: datetime | None = None) -> bool:
    if rule.get("status") != "active":
        return False
    now = now or datetime.now()
    life = rule.get("lifetime") or {}
    if "until" in life and life["until"] != "cancelled":
        try:
            if now >= datetime.fromisoformat(life["until"]):
                return False
        except ValueError:
            return False
    if "until_event" in life and life["until_event"] in _events_fired:
        return False
    rec = life.get("recurring")
    if rec:
        from core.routines import parse_time
        try:
            start = datetime.strptime(parse_time(rec.get("from", "00:00")), "%H:%M").time()
            end = datetime.strptime(parse_time(rec.get("to", "23:59")), "%H:%M").time()
        except ValueError:
            return False
        t = now.time()
        days = _day_set(rec.get("days", "daily"))
        if start <= end:
            return now.weekday() in days and start <= t < end
        # crosses midnight: the part after midnight belongs to the previous day's window
        if t >= start:
            return now.weekday() in days
        return t < end and (now.weekday() - 1) % 7 in days
    return True


def describe_lifetime(rule: dict) -> str:
    life = rule.get("lifetime") or {}
    if life.get("until") and life["until"] != "cancelled":
        try:
            u = datetime.fromisoformat(life["until"])
            day = "" if u.date() == datetime.now().date() else (" tomorrow" if u.date() == (datetime.now() + timedelta(days=1)).date() else u.strftime(" %d %b"))
            return f"until {u.strftime('%H:%M')}{day}"
        except ValueError:
            pass
    if life.get("until_event"):
        return f"until {life['until_event'].replace('_', ' ')}"
    if life.get("recurring"):
        r = life["recurring"]
        return f"{r.get('days', 'daily')} {r.get('from', '00:00')}–{r.get('to', '23:59')}"
    return "until you cancel it"


# ── validation ───────────────────────────────────────────────────────────────
class RuleError(ValueError):
    pass


def _clean_output(o: dict | None) -> dict | None:
    if not o:
        return None
    out: dict[str, Any] = {}
    if o.get("voice"):
        v = str(o["voice"]).lower().replace(" ", "_")
        v = {"mute": "off", "silent": "off", "no": "off", "urgent": "urgent_only", "yes": "on"}.get(v, v)
        if v not in VOICE_LEVELS:
            raise RuleError(f"voice must be one of {VOICE_LEVELS}")
        out["voice"] = v
    if o.get("channels"):
        ch = [str(c).lower() for c in o["channels"]]
        ch = ["hud" if c in ("screen", "text", "window") else c for c in ch]
        bad = [c for c in ch if c not in CHANNELS]
        if bad:
            raise RuleError(f"unknown channel(s): {bad}")
        out["channels"] = sorted(set(ch))
    if o.get("min_importance"):
        mi = str(o["min_importance"]).lower()
        if mi not in IMPORTANCE:
            raise RuleError(f"min_importance must be one of {IMPORTANCE}")
        out["min_importance"] = mi
    for flag in ("proactive", "hold_rest"):
        if flag in o and o[flag] is not None:
            out[flag] = bool(o[flag])
    ign = []
    for item in o.get("ignore") or []:
        if isinstance(item, str):
            item = {"source": item}
        if isinstance(item, dict) and item.get("source"):
            ign.append({"source": str(item["source"]).lower(), "except": str(item.get("except") or "").lower()})
    if ign:
        out["ignore"] = ign
    return out or None


def _clean_tool_rule(t: dict) -> dict:
    match = t.get("match") or {k: t.get(k) for k in ("tool", "action", "target") if t.get(k)}
    if not match.get("tool"):
        raise RuleError("a tool rule needs a tool (name, category like 'messaging', or '*')")
    dec = str(t.get("decision") or "").lower()
    if dec not in DECISIONS:
        raise RuleError(f"decision must be one of {DECISIONS}")
    from core.tool_catalog import matches_tool
    if dec in ("deny", "ask", "ask_once", "limit") and match["tool"] != "*":
        hit = [p for p in PROTECTED_TOOLS if matches_tool(match["tool"], p)]
        if hit:
            raise RuleError(f"{', '.join(hit)} can't be blocked: it's how you stay in control")
    rule = {"match": {k: str(v).lower() for k, v in match.items() if v}, "decision": dec}
    for k in ("prefer", "defaults", "limit"):
        if isinstance(t.get(k), dict) and t[k]:
            rule[k] = t[k]
    if dec in ("prefer", "adjust") and not (rule.get("prefer") or rule.get("defaults")):
        raise RuleError("prefer/adjust needs the values to use (e.g. {'browser': 'edge'})")
    if dec == "limit" and not rule.get("limit"):
        raise RuleError("limit needs {'max': N, 'per': 'hour|day|session|task'} or {'param': name, 'max': N}")
    if t.get("explain"):
        rule["explain"] = str(t["explain"])[:200]
    return rule


def make_rule(*, said: str, summary: str = "", source: str = "voice", output: dict | None = None,
              tools: list[dict] | None = None, custom: str | None = None, until: str | None = None,
              for_minutes: float | None = None, recurring: dict | None = None, until_event: str | None = None,
              mode: str | None = None, kind: str = "owner", now: datetime | None = None) -> dict:
    if source not in TRUSTED_SOURCES and kind == "owner":
        raise RuleError("rules can only come from you directly (voice, Jarvis window, Telegram or Discord)")
    now = now or datetime.now()
    life: dict[str, Any] = {}
    if for_minutes:
        life["until"] = (now + timedelta(minutes=float(for_minutes))).isoformat(timespec="seconds")
    elif until:
        u = str(until).strip()
        try:
            dt = datetime.fromisoformat(u)
        except ValueError:
            dt = next_clock_time(u, now)
        life["until"] = dt.isoformat(timespec="seconds")
    elif recurring:
        life["recurring"] = {"days": recurring.get("days", "daily"), "from": recurring.get("from", "00:00"),
                             "to": recurring.get("to", "23:59")}
    elif until_event:
        life["until_event"] = str(until_event).lower().replace(" ", "_")
    else:
        life["until"] = "cancelled"
    rule = {
        "id": f"r_{now:%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:4]}",
        "said": (said or "").strip()[:400],
        "summary": (summary or "").strip()[:200],
        "source": source,
        "created": now.isoformat(timespec="seconds"),
        "lifetime": life,
        "output": _clean_output(output),
        "tools": [_clean_tool_rule(t) for t in (tools or [])],
        "custom": (custom or "").strip()[:400] or None,
        "mode": mode,
        "kind": kind,
        "status": "active",
        "version": 1,
    }
    if not (rule["output"] or rule["tools"] or rule["custom"] or kind == "pause"):
        raise RuleError("the rule doesn't change anything: say what Jarvis should do differently")
    if not rule["summary"]:
        rule["summary"] = summarize(rule)
    return rule


def summarize(rule: dict) -> str:
    bits = []
    o = rule.get("output") or {}
    if o.get("voice") == "off":
        bits.append("no voice")
    elif o.get("voice") == "urgent_only":
        bits.append("voice only for urgent things")
    if o.get("channels"):
        bits.append("reach you via " + ", ".join(o["channels"]))
    if o.get("min_importance"):
        bits.append(f"only {o['min_importance']}+ things")
    if o.get("proactive") is False:
        bits.append("no check-ins")
    for ig in o.get("ignore") or []:
        bits.append(f"ignore {ig['source']}" + (f" except {ig['except']}" if ig.get("except") else ""))
    for t in rule.get("tools") or []:
        m = t["match"]
        what = m["tool"] + (f" ({m['action']})" if m.get("action") else "") + (f" on {m['target']}" if m.get("target") else "")
        extra = t.get("prefer") or t.get("defaults") or t.get("limit") or ""
        bits.append(f"{t['decision']} {what}" + (f" {extra}" if extra else ""))
    if rule.get("custom"):
        bits.append(rule["custom"])
    if rule.get("kind") == "pause":
        bits.append("everything stopped")
    return "; ".join(bits)[:200].capitalize() + f" ({describe_lifetime(rule)})"


# ── store API ────────────────────────────────────────────────────────────────
def add(rule: dict) -> dict:
    with _lock:
        _load().append(rule)
        _save()
    _audit("ADD", rule)
    _notify("added", rule)
    return rule


def cancel(rule_id: str = "", *, query: str = "", all_rules: bool = False, reason: str = "cancelled") -> list[dict]:
    """Cancel by id, by words in what you said/summary/mode, or everything (all_rules)."""
    out = []
    q = (query or "").lower().strip()
    with _lock:
        for r in _load():
            if r.get("status") != "active" or r.get("kind") == "builtin":
                continue
            hit = all_rules or (rule_id and r["id"] == rule_id) or \
                (q and (q in (r.get("said", "") + " " + r.get("summary", "") + " " + str(r.get("mode") or "")).lower()))
            if hit:
                r["status"] = reason
                r["ended"] = datetime.now().isoformat(timespec="seconds")
                out.append(r)
        if out:
            _save()
    for r in out:
        _audit("CANCEL", r, reason)
        _notify("cancelled", r)
    return out


def fire_event(name: str) -> None:
    """e.g. 'meeting_end': ends rules that said 'until my meeting ends'."""
    _events_fired.add(str(name).lower())
    expire_due()


def expire_due(now: datetime | None = None) -> list[dict]:
    now = now or datetime.now()
    out = []
    with _lock:
        for r in _load():
            if r.get("status") != "active":
                continue
            life = r.get("lifetime") or {}
            ended = False
            if life.get("until") and life["until"] != "cancelled":
                try:
                    ended = now >= datetime.fromisoformat(life["until"])
                except ValueError:
                    ended = True
            if life.get("until_event") in _events_fired:
                ended = True
            if ended:
                r["status"] = "expired"
                r["ended"] = now.isoformat(timespec="seconds")
                out.append(r)
        if out:
            _save()
    for r in out:
        _audit("EXPIRE", r)
        _notify("expired", r)
    return out


def active(now: datetime | None = None) -> list[dict]:
    with _lock:
        return [copy.deepcopy(r) for r in _load() if is_active(r, now)]


def all_rules() -> list[dict]:
    with _lock:
        return copy.deepcopy(_load())


def paused(now: datetime | None = None) -> dict | None:
    for r in active(now):
        if r.get("kind") == "pause":
            return r
    return None


def list_text(now: datetime | None = None) -> str:
    rules = [r for r in active(now) if r.get("kind") != "builtin"]
    if not rules:
        return "No rules are active. Jarvis is behaving normally."
    return "\n".join(f"{i}. {r['summary']}  [id {r['id'][-4:]}]" for i, r in enumerate(rules, 1))


def status_chip(now: datetime | None = None) -> str:
    """Short text for the HUD, e.g. '🔇 Silent · until 20:00'."""
    rules = [r for r in active(now) if r.get("kind") != "builtin"]
    if not rules:
        return ""
    if any(r.get("kind") == "pause" for r in rules):
        return "⏸ Everything stopped · say “resume”"
    voice_off = [r for r in rules if (r.get("output") or {}).get("voice") in ("off", "urgent_only")]
    if voice_off:
        r = voice_off[-1]
        label = (r.get("mode") or "Silent").title()
        return f"🔇 {label} · {describe_lifetime(r)}"
    return f"⚙ {len(rules)} rule{'s' if len(rules) != 1 else ''} active"
