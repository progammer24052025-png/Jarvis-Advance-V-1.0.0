"""Owner control Phases 0–3: rules, output router, tool policy, stop-everything."""

import json
from datetime import datetime, timedelta

from core import activity, attention, directives, owner_control, tool_policy
from core import tool_catalog as catalog
from core.attention import Context, Event


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(directives, "RULES_FILE", tmp_path / "directives.json")
    monkeypatch.setattr(directives, "KEY_FILE", tmp_path / ".rules_key")
    monkeypatch.setattr(directives, "AUDIT_FILE", tmp_path / "audit.log")
    monkeypatch.setattr(directives, "SETTINGS_FILE", tmp_path / "app_settings.json")
    monkeypatch.setattr(activity, "LOG_FILE", tmp_path / "activity.jsonl")
    directives.reload()
    import core.quiet as _quiet
    monkeypatch.setattr(_quiet, "is_quiet", lambda now=None: False)   # don't depend on this PC's quiet hours
    tool_policy.reset_session()
    attention.take_held()


NOW = datetime(2026, 11, 23, 19, 0)


# ── rules: time, validation, integrity ──────────────────────────────────────
def test_until_8_means_the_next_8(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    r = directives.make_rule(said="don't talk until 8 pm", output={"voice": "off"}, until="8 pm", now=NOW)
    assert r["lifetime"]["until"] == "2026-11-23T20:00:00"
    late = datetime(2026, 11, 23, 21, 0)
    r2 = directives.make_rule(said="quiet until 8", output={"voice": "off"}, until="8:00", now=late)
    assert r2["lifetime"]["until"] == "2026-11-24T08:00:00"
    assert directives.is_active(r, NOW) and not directives.is_active(r, datetime(2026, 11, 23, 20, 0))


def test_recurring_window_crossing_midnight():
    r = {"status": "active", "lifetime": {"recurring": {"days": "weekdays", "from": "23:00", "to": "07:00"}}}
    assert directives.is_active(r, datetime(2026, 11, 23, 23, 30))      # Monday night
    assert directives.is_active(r, datetime(2026, 11, 24, 6, 0))        # Tuesday early = Monday's window
    assert not directives.is_active(r, datetime(2026, 11, 24, 12, 0))
    assert not directives.is_active(r, datetime(2026, 11, 29, 6, 0))    # Sunday early: Saturday isn't a weekday


def test_rules_only_from_owner_and_cannot_lock_owner_out(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    for bad in (dict(source="clipboard", output={"voice": "off"}),
                dict(source="voice", tools=[{"tool": "owner_rules", "decision": "deny"}]),
                dict(source="voice", tools=[{"tool": "send_message", "decision": "teleport"}]),
                dict(source="voice")):
        try:
            directives.make_rule(said="x", **bad)
        except directives.RuleError:
            continue
        raise AssertionError(f"accepted a bad rule: {bad}")


def test_tampered_rules_file_is_set_aside_and_audit_chain_holds(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    directives.add(directives.make_rule(said="always ask before messages",
                                        tools=[{"tool": "messaging", "decision": "ask"}]))
    assert directives.verify_audit_chain()
    data = json.loads(directives.RULES_FILE.read_text(encoding="utf-8"))
    data["rules"][0]["tools"][0]["decision"] = "auto"          # malware loosens the rule
    directives.RULES_FILE.write_text(json.dumps(data), encoding="utf-8")
    directives.reload()
    assert directives.active() == [] and directives.tampered()
    assert list(tmp_path.glob("directives.tampered-*.json"))
    # while tampered, risky tools are asked about
    res = tool_policy.evaluate("send_message", {"receiver": "Mom", "message_text": "hi"})
    assert res.decision == "ask"


# ── output router ──────────────────────────────────────────────────────────
def _st(*outputs, quiet=False):
    rules = [{"id": f"r{i}", "output": o} for i, o in enumerate(outputs)]
    return attention.merge_state(rules, quiet)


def test_no_rules_keeps_todays_behaviour():
    st, ctx = _st(), Context(in_front=False, telegram_ok=True)
    d = attention.decide(Event("security", "new startup program"), st, ctx)
    assert d.voice and d.hud and d.toast and d.telegram and not d.hold
    d = attention.decide(Event("reply", "it's 22 degrees"), st, ctx)
    assert d.voice and not d.telegram


def test_silent_text_me_on_telegram():
    st = _st({"voice": "off", "channels": ["hud", "telegram"], "hold_rest": True})
    away = Context(in_front=False, telegram_ok=True)
    d = attention.decide(Event("reply", "it's 22 degrees"), st, away)
    assert not d.voice and d.hud and d.telegram                       # you asked: text, sent where you are
    d = attention.decide(Event("reply", "it's 22 degrees"), st, Context(in_front=True, telegram_ok=True))
    assert not d.voice and d.hud and not d.telegram                    # looking at Jarvis: screen is enough
    d = attention.decide(Event("task", "report ready"), st, away)
    assert not d.voice and d.telegram and not d.toast and d.hold
    d = attention.decide(Event("proactive", "how about a break?"), st, away)
    assert not d.voice


def test_critical_always_gets_through_even_in_focus():
    st = _st({"voice": "off", "channels": ["telegram"], "min_importance": "critical", "proactive": False})
    d = attention.decide(Event("security", "unknown program added to startup", importance="critical"), st,
                         Context(in_front=False, telegram_ok=True))
    assert d.telegram and d.hud and not d.voice
    d = attention.decide(Event("approval", "Jarvis needs your OK"), st, Context(in_front=False, telegram_ok=False))
    assert d.toast and d.hud                                           # no Telegram: falls back to a toast
    d = attention.decide(Event("task", "report ready"), st, Context(in_front=False, telegram_ok=True))
    assert not d.delivered and not d.hold                              # focus without hold_rest: skipped


def test_ignore_app_except_person():
    st = _st({"ignore": [{"source": "whatsapp", "except": "mom"}], "hold_rest": True})
    ctx = Context(in_front=True, telegram_ok=False)
    assert not attention.decide(Event("relay", "promo", source_app="WhatsApp", sender="Walk Wallet"), st, ctx).delivered
    assert attention.decide(Event("relay", "call me", source_app="WhatsApp", sender="Mom"), st, ctx).voice


def test_quiet_hours_behave_like_before():
    st = _st(quiet=True)
    d = attention.decide(Event("task", "report ready"), st, Context(in_front=False, telegram_ok=True))
    assert not d.voice and d.telegram and not d.toast and d.hold       # text now, spoken summary later
    d = attention.decide(Event("security", "x", importance="critical"), st, Context(in_front=False, telegram_ok=True))
    assert d.voice                                                     # urgent still speaks in quiet hours


def test_most_restrictive_wins_when_rules_overlap():
    st = _st({"voice": "urgent_only"}, {"voice": "off", "min_importance": "important"}, {"voice": "on"})
    assert st.voice == "off" and st.min_importance == "important"


# ── tool policy ─────────────────────────────────────────────────────────────
def _rule(tmp_path, monkeypatch, *tool_rules, **kw):
    _isolate(tmp_path, monkeypatch)
    directives.add(directives.make_rule(said="test", tools=list(tool_rules), **kw))


def test_deny_youtube_during_rule(tmp_path, monkeypatch):
    _rule(tmp_path, monkeypatch, {"tool": "open_app|youtube_video|browser_control", "target": "*youtube*", "decision": "deny",
                                  "explain": "No YouTube until 8, you said."}, until="2099-01-01T00:00:00")
    assert tool_policy.evaluate("open_app", {"app_name": "YouTube"}).decision == "deny"
    assert tool_policy.evaluate("open_app", {"app_name": "Notepad"}).allowed
    assert tool_policy.evaluate("browser_control", {"action": "open", "url": "https://www.youtube.com"}).decision == "deny"


def test_prefer_edge_and_default_folder(tmp_path, monkeypatch):
    _rule(tmp_path, monkeypatch,
          {"tool": "browser", "decision": "prefer", "prefer": {"browser": "edge"}},
          {"tool": "documents", "decision": "adjust", "defaults": {"output_path": "D:/Work"}})
    r = tool_policy.evaluate("browser_control", {"action": "open", "url": "x.com", "browser": "chrome"})
    assert r.allowed and r.args["browser"] == "edge" and r.changes == {"browser": "edge"}
    r = tool_policy.evaluate("word_document", {"action": "create", "title": "Letter"})
    assert r.args["output_path"] == "D:/Work"
    r = tool_policy.evaluate("word_document", {"action": "create", "output_path": "C:/x.docx"})
    assert r.args["output_path"] == "C:/x.docx"                       # a path you gave beats the default


def test_limits_rate_and_value(tmp_path, monkeypatch):
    _rule(tmp_path, monkeypatch,
          {"tool": "web_search", "decision": "limit", "limit": {"max": 2, "per": "hour"}},
          {"tool": "spotify_controller", "decision": "limit", "limit": {"param": "volume", "max": 40}})
    assert tool_policy.evaluate("web_search", {"query": "a"}).allowed
    assert tool_policy.evaluate("web_search", {"query": "b"}).allowed
    assert tool_policy.evaluate("web_search", {"query": "c"}).decision == "deny"
    r = tool_policy.evaluate("spotify_controller", {"action": "volume", "volume": 90})
    assert r.allowed and r.args["volume"] == 40


def test_ask_then_owner_confirms_or_refuses(tmp_path, monkeypatch):
    _rule(tmp_path, monkeypatch, {"tool": "messaging", "decision": "ask_once"})
    state = {"turn": 1, "text": "send hi to mom", "trusted": True}
    monkeypatch.setattr(tool_policy, "_owner_turn", lambda: (state["turn"], state["text"], state["trusted"]))
    args = {"receiver": "Mom", "message_text": "hi"}
    assert tool_policy.evaluate("send_message", args).decision == "ask"
    state.update(turn=2, text="haan bhej do, yes")
    assert tool_policy.evaluate("send_message", args).allowed
    assert tool_policy.evaluate("send_message", args).allowed             # ask_once: not asked again this session
    tool_policy.reset_session()
    assert tool_policy.evaluate("send_message", args).decision == "ask"
    state.update(turn=3, text="no, cancel")
    assert tool_policy.evaluate("send_message", args).decision == "deny"


def test_pasted_text_cannot_confirm(tmp_path, monkeypatch):
    _rule(tmp_path, monkeypatch, {"tool": "send_message", "decision": "ask"})
    state = {"turn": 1, "text": "", "trusted": True}
    monkeypatch.setattr(tool_policy, "_owner_turn", lambda: (state["turn"], state["text"], state["trusted"]))
    assert tool_policy.evaluate("send_message", {"receiver": "x"}).decision == "ask"
    state.update(turn=2, text="yes confirm", trusted=False)                # e.g. came from the clipboard
    assert tool_policy.evaluate("send_message", {"receiver": "x"}).decision == "ask"


def test_stop_everything_and_resume(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    stopped = []
    monkeypatch.setattr(owner_control, "on_stop", lambda: stopped.append(1) or "2 tasks cancelled.")
    out = owner_control.handle({"action": "stop_everything", "said": "ruk jao sab"})
    assert "stopped" in out.lower() and stopped
    assert tool_policy.evaluate("open_app", {"app_name": "Notepad"}).decision == "deny"
    assert tool_policy.evaluate("owner_rules", {"action": "resume"}).allowed   # you can always resume
    assert "silent" in directives.status_chip().lower() or "stopped" in directives.status_chip().lower()
    owner_control.handle({"action": "resume"})
    assert tool_policy.evaluate("open_app", {"app_name": "Notepad"}).allowed


def test_owner_rules_tool_end_to_end(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    out = owner_control.handle({"action": "set", "said": "chup raho 8 baje tak, Telegram par message karo",
                                "voice": "off", "channels": ["hud", "telegram"], "until": "20:00",
                                "ignore": ["whatsapp except Mom"],
                                "tool_rules": [{"tool": "youtube", "decision": "deny"},
                                               {"tool": "browser", "decision": "prefer", "values_json": "{\"browser\": \"edge\"}"}],
                                "mode": "silent"})
    assert out.startswith("SET.") and "readback" in out
    assert "Silent" in directives.status_chip()
    st = attention.current_state()
    assert st.voice == "off" and "telegram" in st.channels
    assert tool_policy.evaluate("youtube_video", {"action": "play", "query": "lofi"}).decision == "deny"
    assert "no voice" in owner_control.handle({"action": "list"}).lower()
    bad = owner_control.handle({"action": "set", "said": "never let me change rules",
                                "tool_rules": [{"tool": "owner_rules", "decision": "deny"}]})
    assert bad.startswith("NOT SET")
    assert "Cancelled" in owner_control.handle({"action": "cancel", "query": "all"})
    assert attention.current_state().voice == "on"


def test_expiry_fires_and_ends_silence(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    seen = []
    directives.on_change(lambda what, r: seen.append(what))
    directives.add(directives.make_rule(said="quiet 30 min", output={"voice": "off"}, for_minutes=30, now=NOW))
    assert directives.expire_due(NOW + timedelta(minutes=10)) == []
    assert len(directives.expire_due(NOW + timedelta(minutes=31))) == 1
    assert "expired" in seen


def test_catalog_knows_every_declared_tool():
    import ast
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    decl = next(n for n in tree.body if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "TOOL_DECLARATIONS")
    names = [d["name"] for d in ast.literal_eval(decl.value)]
    unknown = [n for n in names if catalog.get(n).category == "other"]
    assert unknown == [], unknown
    assert catalog.matches_tool("whatsapp", "send_message") and catalog.matches_tool("messaging", "instagram_reply")
    assert "messaging" in catalog.describe("message").lower()


# ── main.py wiring: the real deliver() with a fake screen and Telegram ─────
class _FakeUI:
    def __init__(self):
        self.logs, self.muted = [], False

    def write_log(self, t):
        self.logs.append(t)


class _FakeTelegram:
    def __init__(self):
        self.sent = []

    def configured(self):
        return True

    def owner_chat_id(self):
        return 42

    def send(self, text):
        self.sent.append(text)
        return True


def _live(monkeypatch, in_front):
    import main
    from core import notify
    jl = main.JarvisLive.__new__(main.JarvisLive)
    jl.ui, jl.telegram, jl.spoken = _FakeUI(), _FakeTelegram(), []
    jl._speak_voice = lambda text: jl.spoken.append(text)
    monkeypatch.setattr(notify, "_jarvis_in_front", lambda ui: in_front)
    monkeypatch.setattr(notify, "notify_async", lambda *a, **k: None)
    monkeypatch.setattr("threading.Thread", _SyncThread)
    return jl


class _SyncThread:
    def __init__(self, target=None, args=(), daemon=None, name=None, kwargs=None):
        self._t, self._a = target, args

    def start(self):
        self._t(*self._a)


def test_main_deliver_follows_silent_rule(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    import core.quiet as quiet
    monkeypatch.setattr(quiet, "is_quiet", lambda now=None: False)
    jl = _live(monkeypatch, in_front=False)
    from core.attention import Event
    jl.speak("Your report is ready", kind="task", spoken="Tell the owner the report is ready")
    assert jl.spoken == ["Tell the owner the report is ready"] and jl.telegram.sent  # normal: spoken + Telegram
    owner_control.handle({"action": "set", "said": "don't talk until 8, text me", "voice": "off",
                          "channels": ["hud", "telegram"], "for_minutes": 60})
    jl._voice_cache = (0, True)
    assert jl._voice_now() is False
    jl.spoken.clear(); jl.telegram.sent.clear()
    jl.speak("It's 22 degrees in Ahmedabad")                    # an answer to the owner, while silent
    assert jl.spoken == [] and "Jarvis: It's 22 degrees in Ahmedabad" in jl.ui.logs
    assert jl.telegram.sent == ["It's 22 degrees in Ahmedabad"]
    jl.deliver(Event(kind="proactive", text="How about a break?"))
    assert jl.spoken == []
    assert attention.held_count() >= 0


def test_ui_and_main_still_import():
    import importlib
    for m in ("main", "gui.command_center", "gui.ptt", "agent.executor", "core.notify"):
        importlib.import_module(m)
    import main
    assert any(d["name"] == "owner_rules" for d in main.TOOL_DECLARATIONS)
    assert main._is_stop_phrase("Jarvis, ruk jao sab!") and not main._is_stop_phrase("stop the music")
