"""Tool Policy Engine (Gate 2): every tool call is checked against the owner's rules.

Order (deterministic, no model judgement):
  1. Guardian / safety core (runs before this, in the dispatcher; can't be loosened)
  2. stop-everything pause            → DENY until the owner says "resume"
  3. tampered rules file              → ASK before anything medium/high risk
  4. owner DENY rules                 → DENY with the owner's reason
  5. owner LIMIT rules                → DENY when over a rate limit, or clamp a value
  6. owner ASK / ASK_ONCE rules       → hold and ask; the owner's next turn confirms
  7. owner PREFER / ADJUST rules      → change the call's arguments (shown in the activity log)
  8. owner AUTO rules / defaults      → ALLOW

Most specific rule wins inside a step (a rule naming a target beats one naming only the tool).
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime

from core import activity, directives
from core import tool_catalog as catalog

CONFIRM_WORDS = ("yes", "yeah", "yep", "sure", "ok", "okay", "go ahead", "do it", "confirm", "allow", "proceed",
                 "haan", "ha ", "haa", "kar do", "karo", "chalo", "theek")
DENY_WORDS = ("no", "nope", "don't", "do not", "cancel", "stop", "nahi", "mat", "rehne do", "na ")
PENDING_TTL = 180


@dataclass
class PolicyResult:
    decision: str                      # allow | ask | deny
    args: dict
    reason: str = ""
    rule_id: str = ""
    changes: dict = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision == "allow"


@dataclass
class _Pending:
    tool: str
    sig: str
    owner_turn: int
    created: float
    rule_id: str
    once: bool


_lock = threading.Lock()
_pending: _Pending | None = None
_session_ok: set[tuple[str, str]] = set()      # (rule_id, tool) approved for this session (ask_once)
_calls: dict[str, list[float]] = {}            # rate-limit history per rule
_task_calls: dict[str, int] = {}


def _sig(tool: str, args: dict) -> str:
    return hashlib.sha256((tool + json.dumps(args, sort_keys=True, default=str)).encode()).hexdigest()[:16]


def _specificity(m: dict) -> int:
    return (2 if m.get("target") else 0) + (1 if m.get("action") else 0) + (0 if m.get("tool") == "*" else 1)


def rule_matches(m: dict, tool: str, args: dict) -> bool:
    if not catalog.matches_tool(m.get("tool", ""), tool):
        return False
    if m.get("action"):
        act = str(args.get("action") or args.get("mode") or "").lower()
        if not any(fnmatch.fnmatch(act, a.strip()) for a in m["action"].split("|")):
            return False
    if m.get("target"):
        vals = catalog.target_values(tool, args)
        pats = [p.strip() for p in m["target"].split("|") if p.strip()]
        if not any(fnmatch.fnmatch(v, p) or p.strip("*") in v for v in vals for p in pats):
            return False
    return True


def _matching(tool: str, args: dict, now: datetime | None) -> list[tuple[dict, dict]]:
    out = []
    for r in directives.active(now):
        for tr in r.get("tools") or []:
            if rule_matches(tr["match"], tool, args):
                out.append((r, tr))
    out.sort(key=lambda rt: _specificity(rt[1]["match"]), reverse=True)
    return out


def _owner_turn() -> tuple[int, str, bool]:
    try:
        from core.guardian import get_guardian
        s = get_guardian().owner_state()
        return s["turn"], (s["text"] or "").lower(), s["trusted_source"]
    except Exception:
        return 0, "", True


def _has(text: str, words: tuple[str, ...]) -> bool:
    t = f" {text.lower()} "
    return any(f" {w.strip()} " in t or (len(w.strip()) > 3 and w.strip() in t) for w in words)


def _limit_check(r: dict, tr: dict, tool: str, args: dict) -> PolicyResult | None:
    lim = tr.get("limit") or {}
    if "param" in lim:
        p = lim["param"]
        if p in args:
            try:
                val = float(args[p])
            except (TypeError, ValueError):
                return None
            new = val
            if "max" in lim and val > float(lim["max"]):
                new = float(lim["max"])
            if "min" in lim and val < float(lim["min"]):
                new = float(lim["min"])
            if new != val:
                args = dict(args)
                args[p] = int(new) if float(new).is_integer() else new
                return PolicyResult("allow", args, f"{p} kept at {args[p]} (your limit)", r["id"], {p: args[p]})
        return None
    mx = int(lim.get("max", 0) or 0)
    per = str(lim.get("per", "hour")).lower()
    window = {"minute": 60, "hour": 3600, "day": 86400, "session": 10 ** 9}.get(per)
    key = r["id"] + "|" + tool
    now = time.time()
    with _lock:
        if per == "task":
            used = _task_calls.get(key, 0)
            if used >= mx:
                return PolicyResult("deny", args, f"limit reached: {mx} per task (your rule)", r["id"])
            _task_calls[key] = used + 1
            return None
        hist = [t for t in _calls.get(key, []) if window is None or now - t < window]
        if len(hist) >= mx:
            _calls[key] = hist
            return PolicyResult("deny", args, f"limit reached: {mx} per {per} (your rule)", r["id"])
        hist.append(now)
        _calls[key] = hist
    return None


def new_task_scope() -> None:
    """Call when a new owner request starts, so 'per task' limits reset."""
    with _lock:
        _task_calls.clear()


def evaluate(tool: str, args: dict | None = None, *, now: datetime | None = None, record: bool = True) -> PolicyResult:
    global _pending
    args = dict(args or {})
    if not directives.feature_on("tool_policy"):
        return PolicyResult("allow", args, "tool rules switched off")

    def done(res: PolicyResult) -> PolicyResult:
        if record:
            activity.record("tool", tool, res.decision + (" (adjusted)" if res.changes else ""), res.reason, res.rule_id,
                            changes=json.dumps(res.changes) if res.changes else "")
        return res

    info = catalog.get(tool)
    if tool in directives.PROTECTED_TOOLS:
        return done(PolicyResult("allow", args, "owner-control tool"))

    # 2. stop-everything
    p = directives.paused(now)
    if p:
        return done(PolicyResult("deny", args, "everything is stopped: say 'resume' to continue", p["id"]))

    # 3. tampered rules → be careful
    if directives.tampered() and info.risk in ("medium", "high"):
        return done(_ask(tool, args, "", once=False, reason="your rules file was changed outside Jarvis, so I'm asking first"))

    matches = _matching(tool, args, now)

    # 4. deny
    for r, tr in matches:
        if tr["decision"] == "deny":
            return done(PolicyResult("deny", args, tr.get("explain") or f"blocked by your rule: {r['summary']}", r["id"]))

    # 5. limits
    for r, tr in matches:
        if tr["decision"] == "limit":
            res = _limit_check(r, tr, tool, args)
            if res is not None:
                if res.decision == "deny":
                    return done(res)
                args, changes, lim_reason, lim_rule = res.args, res.changes, res.reason, res.rule_id
                break
    else:
        changes, lim_reason, lim_rule = {}, "", ""

    # 6. ask
    for r, tr in matches:
        if tr["decision"] in ("ask", "ask_once"):
            once = tr["decision"] == "ask_once"
            if once and (r["id"], tool) in _session_ok:
                break
            res = _ask(tool, args, r["id"], once=once, reason=tr.get("explain") or f"your rule: {r['summary']}")
            if not res.allowed:
                return done(res)
            break

    # 7. prefer / adjust
    for r, tr in reversed(matches):          # least specific first, so the most specific wins last
        if tr["decision"] in ("prefer", "adjust"):
            for k, v in (tr.get("prefer") or {}).items():
                if args.get(k) != v:
                    args[k] = v
                    changes[k] = v
            for k, v in (tr.get("defaults") or {}).items():
                if k not in args or args.get(k) in (None, ""):
                    args[k] = v
                    changes[k] = v
            if changes:
                lim_rule = lim_rule or r["id"]
    reason = lim_reason or ("used your preferences: " + ", ".join(f"{k}={v}" for k, v in changes.items()) if changes else "")

    # 8. allow
    if not reason:
        auto = next((r for r, tr in matches if tr["decision"] == "auto"), None)
        reason = f"allowed by your rule: {auto['summary']}" if auto else "default"
        lim_rule = lim_rule or (auto["id"] if auto else "")
    return done(PolicyResult("allow", args, reason, lim_rule, changes))


def _ask(tool: str, args: dict, rule_id: str, *, once: bool, reason: str) -> PolicyResult:
    """Hold the call until the owner's next turn says yes (or no)."""
    global _pending
    sig = _sig(tool, args)
    turn, said, trusted = _owner_turn()
    with _lock:
        p = _pending
        if p and p.tool == tool and time.time() - p.created < PENDING_TTL and turn > p.owner_turn and trusted:
            if _has(said, DENY_WORDS) and not _has(said, CONFIRM_WORDS):
                _pending = None
                return PolicyResult("deny", args, "you said no", rule_id)
            if _has(said, CONFIRM_WORDS):
                _pending = None
                if once:
                    _session_ok.add((rule_id, tool))
                return PolicyResult("allow", args, "you confirmed", rule_id)
        _pending = _Pending(tool, sig, turn, time.time(), rule_id, once)
    what = tool.replace("_", " ")
    detail = ", ".join(f"{k}: {str(v)[:40]}" for k, v in list(args.items())[:3])
    return PolicyResult("ask", args,
                        f"OWNER RULE HOLD: ask the owner before running {what}" + (f" ({detail})" if detail else "")
                        + f". Reason: {reason}. Do not run it until they say yes; if they say no, drop it.", rule_id)


def pending_question() -> str:
    p = _pending
    if not p or time.time() - p.created > PENDING_TTL:
        return ""
    return f"Waiting for your OK to run {p.tool.replace('_', ' ')}."


def reset_session() -> None:
    global _pending
    with _lock:
        _pending = None
        _session_ok.clear()
        _calls.clear()
        _task_calls.clear()
