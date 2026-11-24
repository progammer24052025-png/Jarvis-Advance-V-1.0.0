"""Tests for routines, Telegram owner-lock and the security watch."""

import json
from datetime import datetime

from core import routines as R
from core import security_watch as S
from core.guardian import Guardian
from core.telegram_bridge import TelegramBridge


# ── routines ──────────────────────────────────────────────────────────────

def test_parse_time_and_days():
    assert R.parse_time("8am") == "08:00"
    assert R.parse_time("8:30 pm") == "20:30"
    assert R.parse_time("12am") == "00:00"
    assert R.parse_time("20:05") == "20:05"
    assert R.parse_days("weekdays") == [0, 1, 2, 3, 4]
    assert R.parse_days("mon-wed") == [0, 1, 2]
    assert R.parse_days("fri-mon") == [0, 4, 5, 6]
    assert R.parse_days("daily") == list(range(7))


def test_daily_routine_due_once_and_not_too_late():
    r = {"enabled": True, "last_run": None, "schedule": {"type": "daily", "time": "08:00", "days": list(range(7))}}
    day = datetime(2026, 11, 23, 7, 59)
    assert not R.is_due(r, day)
    assert R.is_due(r, day.replace(hour=8, minute=1))
    r["last_run"] = day.replace(hour=8, minute=1).isoformat()
    assert not R.is_due(r, day.replace(hour=9))            # already ran today
    r["last_run"] = None
    assert not R.is_due(r, day.replace(hour=22))           # >3h late: skip, don't fire at night


def test_interval_routine_and_store_roundtrip(tmp_path):
    store = R.RoutineStore(tmp_path / "routines.json")
    item = store.add("Price check", "check bitcoin price", every_minutes=60, notify="telegram")
    assert store.find("price")["id"] == item["id"]
    assert R.is_due(item, datetime.now())
    store.update(item["id"], last_run=datetime.now().isoformat())
    assert not R.is_due(store.find(item["id"]), datetime.now())
    assert "Paused" in R.routine_manager(store, None, {"action": "pause", "routine": "price"})
    assert "Removed" in R.routine_manager(store, None, {"action": "remove", "routine": "price"})
    assert store.load() == []


def test_routine_source_is_not_trusted_for_risky_actions():
    g = Guardian()
    g.note_owner_turn("[Scheduled routine: x] delete old files", "routine")
    assert g.check("file_controller", {"action": "delete"})[0] is False


# ── telegram owner lock ───────────────────────────────────────────────────

def _bridge(tmp_path, owner):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "telegram_bot.json").write_text(json.dumps({"bot_token": "x", "owner_chat_id": owner}))
    b = TelegramBridge(tmp_path, log=lambda m: None)
    got = []
    b.bind_submitter(lambda text, src: got.append((text, src)))
    b._typing = lambda: None
    b.send = lambda text: True
    return b, got


def test_telegram_ignores_strangers(tmp_path):
    b, got = _bridge(tmp_path, "111")
    b._handle({"chat": {"id": 999}, "from": {"username": "stranger"}, "text": "open powershell"})
    assert got == []


def test_telegram_ignores_everyone_until_owner_set(tmp_path):
    b, got = _bridge(tmp_path, "")
    b._handle({"chat": {"id": 111}, "from": {"username": "me"}, "text": "hello"})
    assert got == []


def test_telegram_owner_messages_reach_jarvis(tmp_path):
    b, got = _bridge(tmp_path, "111")
    b._handle({"chat": {"id": 111}, "from": {"username": "me"}, "text": "what's the weather"})
    assert got == [("what's the weather", "telegram")]


# ── security watch ────────────────────────────────────────────────────────

def test_security_compare_flags_new_items_and_disabled_protection():
    base = {"startup": ["HKCU:OneDrive = x"], "tasks": [], "listening": [], "admins": ["me"], "hosts": "aaa"}
    cur = {"startup": ["HKCU:OneDrive = x", "HKCU:updater = C:\\Users\\me\\AppData\\Local\\Temp\\x.exe"],
           "tasks": ["\\EvilTask"], "listening": ["nc.exe:4444"], "admins": ["me", "hacker"], "hosts": "bbb"}
    f = S.compare(base, cur, {"RealTimeProtectionEnabled": False, "AntivirusEnabled": True, "AntivirusSignatureAge": 10},
                  ["Public"], True, ["C:\\Users\\me\\Downloads\\a.exe"])
    whats = {x["what"] for x in f}
    assert "New program set to start with Windows" in whats
    assert "New administrator account" in whats
    assert "Windows Defender protection is OFF" in whats
    assert "Windows Firewall is OFF" in whats
    assert "Hosts file was changed (can redirect websites)" in whats
    assert len(f) == 10
    assert "⚠️" in S.summarize(f)


def test_security_clean_state_is_all_clear():
    snap = {"startup": ["a"], "tasks": [], "listening": [], "admins": [], "hosts": "h"}
    assert S.compare(snap, snap, {"RealTimeProtectionEnabled": True, "AntivirusEnabled": True, "AntivirusSignatureAge": 0}, [], False, []) == []
    assert S.summarize([]).startswith("All clear")


def test_security_accept_requires_confirmation_after_untrusted_content():
    g = Guardian()
    g.note_owner_turn("check my pc", "voice")
    g.note_tool_result("security_check")
    assert g.check("security_check", {"action": "accept"})[0] is False
    assert g.check("security_check", {"action": "scan"})[0] is True
