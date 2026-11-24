"""Output Router (Gate 1): decides HOW and WHETHER Jarvis tells the owner something.

Every message Jarvis wants to give the owner becomes an Event. `decide()` is a pure function:
event + the owner's active rules + context → Decision (speak / HUD text / Windows toast /
Telegram / hold for the digest / drop). The executor in main.py carries the decision out.

Decision order:
  1. safety floor: critical things always reach at least one text channel
  2. answers to the owner: always shown; spoken only if voice is allowed
  3. the owner's active rules (most restrictive value wins when several overlap)
  4. legacy quiet hours (a built-in rule)
  5. defaults: today's behaviour per kind of message
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime

from core import directives

IMPORTANCE_RANK = {imp: i for i, imp in enumerate(directives.IMPORTANCE)}

# What each kind of message did before rules existed (and still does with no rules active).
DEFAULT_CHANNELS: dict[str, set[str]] = {
    "reply":     {"voice", "hud"},
    "progress":  {"voice"},
    "security":  {"voice", "hud", "toast", "telegram"},
    "task":      {"voice", "hud", "toast", "telegram"},
    "forge":     {"voice", "hud", "toast", "telegram"},
    "routine":   {"toast"},
    "relay":     {"voice", "hud"},
    "proactive": {"voice"},
    "assist":    {"voice", "hud"},
    "approval":  {"hud", "toast"},
    "system":    {"voice", "hud"},
    "digest":    {"voice", "hud"},
}
DEFAULT_IMPORTANCE = {
    "security": "important", "approval": "critical", "task": "normal", "forge": "normal",
    "routine": "normal", "relay": "normal", "proactive": "low", "assist": "low",
    "system": "normal", "digest": "normal", "reply": "important", "progress": "normal",
}
ASKED_KINDS = {"reply", "progress"}


@dataclass
class Event:
    kind: str
    text: str                              # plain text for screen / Telegram
    spoken: str = ""                       # what to hand the voice model (may be an instruction); defaults to text
    title: str = ""
    private_text: str = ""                 # lock-screen-safe toast text
    importance: str | None = None
    asked_by_owner: bool | None = None
    source_app: str = ""
    sender: str = ""
    panel: str = ""
    buttons: list | None = None
    telegram_text: str = ""

    def __post_init__(self):
        if self.asked_by_owner is None:
            self.asked_by_owner = self.kind in ASKED_KINDS


@dataclass
class Context:
    in_front: bool = False                 # the owner is looking at the Jarvis window
    telegram_ok: bool = False
    quiet_hours: bool = False
    now: datetime | None = None


@dataclass
class OutputState:
    voice: str = "on"
    channels: set[str] = field(default_factory=lambda: set(directives.CHANNELS))
    min_importance: str = "low"
    proactive: bool = True
    hold_rest: bool = False
    digest_all: bool = False
    ignore: list[dict] = field(default_factory=list)
    rule_ids: list[str] = field(default_factory=list)
    label: str = ""


@dataclass
class Decision:
    voice: bool = False
    hud: bool = False
    toast: bool = False
    telegram: bool = False
    hold: bool = False
    importance: str = "normal"
    reason: str = "default"
    rule_id: str = ""

    @property
    def delivered(self) -> bool:
        return self.voice or self.hud or self.toast or self.telegram

    def channels(self) -> str:
        names = [n for n in ("voice", "hud", "toast", "telegram") if getattr(self, n)]
        return "+".join(names) + ("+held" if self.hold else "") if (names or self.hold) else "dropped"


# ── state from rules ─────────────────────────────────────────────────────────
def merge_state(rules: list[dict], quiet_hours: bool = False) -> OutputState:
    st = OutputState()
    voice_rank = {v: i for i, v in enumerate(directives.VOICE_LEVELS)}
    effects = [(r["id"], r.get("output") or {}, r.get("mode") or "") for r in rules if r.get("output")]
    if quiet_hours:
        effects.append(("quiet_hours", {"voice": "urgent_only", "channels": ["hud", "telegram"],
                                        "proactive": False, "hold_rest": True}, "quiet hours"))
    for rid, o, mode in effects:
        st.rule_ids.append(rid)
        if mode and not st.label:
            st.label = mode
        if "voice" in o and voice_rank[o["voice"]] < voice_rank[st.voice]:
            st.voice = o["voice"]
        if o.get("channels"):
            ch = set(o["channels"])
            if o.get("voice") in (None, "on"):
                ch.add("voice")
            st.channels &= ch | {"hud"}
        if o.get("min_importance") and IMPORTANCE_RANK[o["min_importance"]] > IMPORTANCE_RANK[st.min_importance]:
            st.min_importance = o["min_importance"]
        if o.get("proactive") is False:
            st.proactive = False
        if o.get("hold_rest"):
            st.hold_rest = True
        if rid == "quiet_hours":
            st.digest_all = True
        st.ignore.extend(o.get("ignore") or [])
    if st.voice != "on":
        st.channels.discard("voice")
    st.channels.add("hud")                 # the screen is never taken away: it's where you look
    return st


def rate(ev: Event) -> str:
    if ev.importance in IMPORTANCE_RANK:
        return ev.importance
    return DEFAULT_IMPORTANCE.get(ev.kind, "normal")


def _ignored(ev: Event, st: OutputState) -> bool:
    app = (ev.source_app or "").lower()
    who = (ev.sender or "").lower()
    for ig in st.ignore:
        src = ig.get("source", "")
        if src and (src == app or src in app or src == ev.kind):
            exc = ig.get("except", "").replace("from:", "").strip()
            if exc and exc in who:
                continue
            return True
    return False


# ── the decision (pure) ──────────────────────────────────────────────────────
def decide(ev: Event, st: OutputState, ctx: Context) -> Decision:
    imp = rate(ev)
    base = set(DEFAULT_CHANNELS.get(ev.kind, {"voice", "hud"}))
    rid = st.rule_ids[-1] if st.rule_ids else ""
    voice_ok = st.voice == "on" or (st.voice == "urgent_only" and imp == "critical")

    # 1. safety floor
    if imp == "critical":
        d = Decision(voice=voice_ok and "voice" in base, hud=True, importance=imp,
                     reason="critical: always delivered", rule_id=rid)
        d.toast = not ctx.in_front
        d.telegram = ctx.telegram_ok and (not ctx.in_front or "telegram" in base)
        if not d.telegram and not ctx.telegram_ok and not ctx.in_front:
            d.toast = True
        return d

    # 2. answers to the owner
    if ev.asked_by_owner:
        d = Decision(hud=True, importance=imp, rule_id=rid,
                     voice=voice_ok and "voice" in base,
                     reason="you asked" if voice_ok else "you asked; voice is off, so as text")
        if not d.voice and not ctx.in_front and ev.kind == "reply":
            d.telegram = ctx.telegram_ok and "telegram" in st.channels
            d.toast = not d.telegram and "toast" in st.channels
        if ev.kind == "progress" and not voice_ok:
            d.hud = True
        return d

    # 3. owner rules
    if _ignored(ev, st):
        return Decision(hold=st.hold_rest, importance=imp, reason="you asked me to ignore this", rule_id=rid)
    if ev.kind in ("proactive", "assist") and not st.proactive:
        return Decision(importance=imp, reason="check-ins are off", rule_id=rid)
    if IMPORTANCE_RANK[imp] < IMPORTANCE_RANK[st.min_importance]:
        return Decision(hold=st.hold_rest, importance=imp, rule_id=rid,
                        reason=f"below {st.min_importance}: " + ("held for your summary" if st.hold_rest else "skipped"))

    allowed = st.channels
    d = Decision(importance=imp, rule_id=rid, reason="rules" if st.rule_ids else "default")
    d.voice = voice_ok and "voice" in base and "voice" in allowed
    d.hud = "hud" in base and "hud" in allowed
    d.toast = "toast" in base and "toast" in allowed and not ctx.in_front
    d.telegram = "telegram" in base and "telegram" in allowed and ctx.telegram_ok
    if "voice" in base and not d.voice:
        # it would have been spoken: put the words where you'll see them instead
        d.hud = True
        if not ctx.in_front and "telegram" in allowed and ctx.telegram_ok:
            d.telegram = True
        elif not ctx.in_front and "toast" in allowed and not d.telegram:
            d.toast = True
        d.reason = "voice is off, so as text"
        if st.digest_all or (st.hold_rest and ev.kind not in ("digest", "system")):
            d.hold = ev.kind not in ("digest", "system", "routine")
    if not d.delivered and not d.hold:
        d.hud = True
    return d


# ── held messages (the digest) ───────────────────────────────────────────────
_held: list[Event] = []
_held_lock = threading.Lock()


def hold(ev: Event) -> None:
    with _held_lock:
        _held.append(ev)
        del _held[:-40]


def held_count() -> int:
    with _held_lock:
        return len(_held)


def take_held() -> list[Event]:
    with _held_lock:
        out = list(_held)
        _held.clear()
        return out


def digest_text(events: list[Event]) -> str:
    lines = [f"• {e.title + ': ' if e.title else ''}{e.text[:220]}" for e in events]
    return "\n".join(lines)


# ── convenience for callers ──────────────────────────────────────────────────
def current_state(now: datetime | None = None) -> OutputState:
    quiet = False
    try:
        from core.quiet import is_quiet
        quiet = is_quiet(now)
    except Exception:
        pass
    rules = directives.active(now) if directives.feature_on("rules") else []
    return merge_state(rules, quiet)


def voice_allowed(kind: str = "reply", importance: str | None = None) -> bool:
    st = current_state()
    imp = importance or DEFAULT_IMPORTANCE.get(kind, "normal")
    return st.voice == "on" or (st.voice == "urgent_only" and imp == "critical")
