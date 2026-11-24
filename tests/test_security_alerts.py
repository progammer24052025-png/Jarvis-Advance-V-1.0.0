"""Security alerts: trusting sticks, nothing is repeated, and the wording is plain."""

import json

from core import security_watch as sw

CHROME = ('HKCU:GoogleChromeAutoLaunch_9DD5 = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" '
          '--startup-foreground-launch')
EVIL = 'HKCU:Updater = "C:\\Users\\me\\AppData\\Local\\Temp\\upd.exe"'


def _watch(tmp_path, monkeypatch, startup):
    state = {"startup": list(startup)}
    monkeypatch.setattr(sw, "snapshot", lambda: {"taken": "", "startup": list(state["startup"]), "tasks": [],
                                                 "listening": [], "admins": [], "hosts": "h"})
    monkeypatch.setattr(sw, "defender_status", lambda: {})
    monkeypatch.setattr(sw, "firewall_off_profiles", lambda: [])
    monkeypatch.setattr(sw, "rdp_enabled", lambda: False)
    monkeypatch.setattr(sw, "temp_processes", lambda: [])
    (tmp_path / "config").mkdir(exist_ok=True)
    alerts = []
    w = sw.SecurityWatch(tmp_path, alerts.append)
    return w, state, alerts


def _tick(w):
    """One background-loop iteration, without the thread."""
    import time
    findings = w.scan()
    now = time.time()
    fresh = [f for f in findings if now - w._reported.get(sw.finding_key(f), 0) > 7 * 86400]
    for f in fresh:
        w._reported[sw.finding_key(f)] = now
    if fresh:
        w._save_reported()
        w.alert(fresh)


def test_chrome_toggling_its_startup_entry_is_not_reported_again(tmp_path, monkeypatch):
    w, state, alerts = _watch(tmp_path, monkeypatch, [])
    _tick(w)                                     # first scan = baseline, silent
    state["startup"] = [CHROME]
    _tick(w)
    assert len(alerts) == 1
    assert "trusted" in w.accept_current("chrome").lower()
    state["startup"] = []                        # Chrome removes its entry…
    _tick(w)
    state["startup"] = [CHROME]                  # …and adds it back later
    _tick(w)
    assert len(alerts) == 1 and w.last_findings == []


def test_same_finding_is_not_repeated_after_a_restart(tmp_path, monkeypatch):
    w, state, alerts = _watch(tmp_path, monkeypatch, [])
    _tick(w)
    state["startup"] = [EVIL]
    _tick(w)
    assert len(alerts) == 1
    w2 = sw.SecurityWatch(tmp_path, alerts.append)   # Jarvis restarted, not yet trusted
    _tick(w2)
    assert len(alerts) == 1
    assert w2.last_findings                          # still listed in the status until you trust it


def test_trust_one_item_leaves_the_others(tmp_path, monkeypatch):
    w, state, alerts = _watch(tmp_path, monkeypatch, [])
    _tick(w)
    state["startup"] = [CHROME, EVIL]
    _tick(w)
    out = w.accept_current("chrome")
    assert "Google Chrome" in out
    assert [sw.describe(f)["title"] for f in w.last_findings] == ["upd was set to start with Windows"]
    assert "matches" in w.accept_current("spotify")
    assert json.loads((tmp_path / "config" / "security_status.json").read_text())["findings"] == 1


def test_alert_text_is_plain_and_ranks_suspicious_first():
    text = sw.summarize([
        {"severity": "high", "what": "New program set to start with Windows", "detail": CHROME},
        {"severity": "high", "what": "New program set to start with Windows", "detail": EVIL},
        {"severity": "medium", "what": "New scheduled task", "detail": "\\SoftLanding\\S-1-5-21-1\\Tips"},
    ])
    lines = text.splitlines()
    assert lines[0].startswith("Security check: 3 new things")
    assert "upd was set to start with Windows (looks suspicious)" in lines[1]
    assert "Google Chrome was set to start with Windows (probably normal)" in text
    assert "Windows' own tips" in text
    assert "trust them" in lines[-1]
    assert "HKCU:" not in text and "9DD5" not in text      # no registry gibberish
