"""Orbi, the Jarvis pet (gui/pet.py): moods follow Jarvis, drawing works, it stays light, private and polite."""

import os
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_APP = None


def _app():
    """One Qt application for these tests, kept alive (Qt crashes if it's garbage-collected mid-test)."""
    global _APP
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication
    _APP = QApplication.instance() or QApplication([])
    return _APP


def _pet(monkeypatch, tmp_path, **cfg):
    """A pet with its own settings file and no real Windows checks (full screen, lock, rules)."""
    _app()
    from gui import pet as P
    monkeypatch.setattr(P, "_pet_file", lambda: tmp_path / "pet.json")
    monkeypatch.setattr(P, "fullscreen_busy", lambda: False)
    monkeypatch.setattr(P, "ambient_mood", lambda: None)
    monkeypatch.setattr(P, "windows_reduce_motion", lambda: False)
    if cfg:
        P.save_settings(cfg)
    pet = P.JarvisPet(size=72)
    pet._slow.stop()
    return P, pet


# ── Phase 2 ─────────────────────────────────────────────────────────────────
def test_mood_follows_jarvis_with_the_right_priority():
    from gui.pet import pick_mood
    assert pick_mood("listening") == "idle"                        # always-on listening isn't a show
    assert pick_mood("listening", mic_active=True) == "listening"  # …but you talking is
    assert pick_mood("THINKING") == "thinking"
    assert pick_mood("processing") == pick_mood("executing") == "working"
    assert pick_mood("speaking") == "speaking"
    assert pick_mood("muted", mic_active=True) == "muted"
    assert pick_mood("idle", ambient="focus") == "focus"
    assert pick_mood("idle", ambient="sleep") == "sleep"
    assert pick_mood("speaking", ambient="silent") == "speaking"   # rules don't hide real activity
    assert pick_mood("speaking", transient="done") == "done"
    assert pick_mood("speaking", safety=True, transient="done") == "safety"   # a safety hold always shows


def test_every_mood_draws_at_every_size():
    _app()
    from PyQt6.QtGui import QColor, QImage, QPainter
    from gui.pet import MOODS, draw_pet
    for size in (16, 32, 72, 256):
        for mood in MOODS:
            for motion in (1.0, 0.25):
                img = QImage(size * 2, size * 2, QImage.Format.Format_ARGB32)
                img.fill(QColor(0, 0, 0, 0))
                p = QPainter(img)
                draw_pet(p, size, size, size, mood=mood, t=1.3, look=(0.5, -0.2), accent="#22d3ee", voice=0.6,
                         motion=motion)
                p.end()
                assert img.pixelColor(size, size).alpha() > (150 if mood == "focus" else 200), (mood, size)


def test_pet_reacts_to_jarvis_and_stays_light(monkeypatch, tmp_path):
    P, pet = _pet(monkeypatch, tmp_path)
    pet.set_state("thinking")
    assert pet.current_mood() == "thinking"
    pet.set_state("listening")                                       # finished thinking → happy hop
    assert pet.current_mood() == "done"
    pet._transient = None
    pet.set_mic_level(0.6)
    assert pet.current_mood() == "listening"
    pet._mic_at = time.monotonic() - 5
    pet.set_safety(True)
    assert pet.current_mood() == "safety" and "Needs your OK" in pet.accessibleDescription()
    pet.set_safety(False)
    assert not pet._frame.isActive()                                 # hidden: no frames at all
    pet.show()
    pet._kick()
    assert pet._frame.isActive() and pet._frame.interval() == 50     # calm: ~20 fps
    pet.set_state("speaking")
    assert pet._frame.interval() == 33                               # busy: ~30 fps
    pet.apply_settings({**pet.cfg, "motion": "calm"})
    pet.set_state("listening")
    pet._transient = None
    pet._kick()
    assert pet._frame.interval() == 66 and pet.motion() == 0.5       # calm pet: slower, smaller moves
    pet.hide()
    assert not pet._frame.isActive()
    pet.deleteLater()


def test_lock_screen_shows_nothing_about_your_tasks(monkeypatch, tmp_path):
    from core import notify
    P, pet = _pet(monkeypatch, tmp_path)
    pet.show()
    pet.set_state("processing", "Booking flights to Goa")
    assert "Goa" in pet.accessibleDescription()
    monkeypatch.setattr(notify, "is_session_locked", lambda: True)
    pet._refresh_ambient()
    assert "Goa" not in pet.accessibleDescription()
    pet._hovered = True
    pet._show_card()
    assert "Goa" not in pet.popup.text and "Goa" not in pet.popup.title
    assert pet.say("Your OTP is 4821", title="SMS", importance="high")
    assert "4821" not in pet.popup.text and pet.popup.text == "Jarvis has an update."
    pet.hide()
    pet.deleteLater()


def test_ui_uses_the_pet_but_keeps_the_old_orb_as_fallback():
    text = (ROOT / "ui.py").read_text(encoding="utf-8-sig")
    assert "class _LegacyOrbLauncher(QWidget):" in text
    assert "class FloatingLauncher(_JarvisPet):" in text and "FloatingLauncher = _LegacyOrbLauncher" in text
    assert text.count("self._launcher.attach_window(self._win)") == 2


# ── Phase 3 ─────────────────────────────────────────────────────────────────
def test_voice_phrases_are_clear_commands_only():
    from gui.pet import pet_phrase
    yes = {"show the pet": "show", "Hide Orbi!": "hide", "Jarvis, bring back the pet please": "show",
           "Orbi, go away": "hide", "please show your pet again": "show", "turn off the desktop pet": "hide",
           "hide Bubbles": "hide", "bubbles come back": "show"}
    for said, want in yes.items():
        assert pet_phrase(said, "Bubbles") == want, said
    for said in ("show me pictures of a pet", "I want a pet", "hide the window", "my pet is hungry",
                 "show the pet store near me", "what is orbi", ""):
        assert pet_phrase(said, "Bubbles") is None, said


def test_bubbles_follow_the_owners_rules():
    from gui.pet import bubble_allowed
    assert bubble_allowed(None, "normal") and bubble_allowed("silent", "low")
    assert not bubble_allowed("focus", "normal") and bubble_allowed("focus", "high")
    assert not bubble_allowed("sleep", "high") and bubble_allowed("sleep", "critical")


def test_settings_are_cleaned_and_kept_in_their_own_file(monkeypatch, tmp_path):
    from gui import pet as P
    monkeypatch.setattr(P, "_pet_file", lambda: tmp_path / "pet.json")
    assert P.load_settings() == P.DEFAULTS
    cfg = P.save_settings({"size": "HUGE", "motion": "calm", "name": "  Bubbles\x07  the   Great Pet of Doom!!  ",
                           "enabled": 0})
    assert cfg["size"] == "medium" and cfg["motion"] == "calm" and cfg["enabled"] is False
    assert cfg["name"] == "Bubbles the Great Pe" and len(cfg["name"]) <= 20
    assert P.load_settings() == cfg
    assert P.save_settings({"name": ""})["name"] == "Orbi"


def test_settings_apply_live_size_name_and_hide(monkeypatch, tmp_path):
    P, pet = _pet(monkeypatch, tmp_path)
    pet.show_at(100, 100)
    br = pet.frameGeometry().bottomRight()
    pet.apply_settings({"size": "large", "name": "Bubbles", "enabled": True})
    assert pet.body == P.SIZES["large"] and pet.frameGeometry().bottomRight() == br   # grows from its corner
    assert "Bubbles" in pet.accessibleDescription()
    pet.apply_settings({"enabled": False})
    assert not pet.isVisible()
    pet.show_at(100, 100)                                            # Jarvis can't bring back a hidden pet
    assert not pet.isVisible()
    pet.deleteLater()


def test_commands_from_other_threads_reach_the_pet(monkeypatch, tmp_path):
    app = _app()
    P, pet = _pet(monkeypatch, tmp_path)
    pet.show_at(50, 50)
    threading.Thread(target=P.request, args=("hide",)).start()
    deadline = time.time() + 3
    while pet.isVisible() and time.time() < deadline:
        app.processEvents()
        time.sleep(0.01)
    assert not pet.isVisible() and P.load_settings()["enabled"] is False
    P.request("toggle")
    app.processEvents()
    assert pet.isVisible() and P.load_settings()["enabled"] is True
    pet.hide()
    pet.deleteLater()


def test_bubbles_respect_focus_and_show_short_text(monkeypatch, tmp_path):
    P, pet = _pet(monkeypatch, tmp_path)
    pet.show_at(400, 400)
    pet._ambient = "focus"
    assert not pet.say("The football score is 2-1")                 # focus: not important enough
    assert pet.say("Your build failed", importance="high")
    assert pet.current_mood() == "wave" and pet.popup.isVisible() and pet.popup.kind == "bubble"
    pet._ambient = None
    assert pet.say("x " * 200)
    assert len(pet.popup.text) <= 120 and pet.popup.text.endswith("…")
    pet.hide()
    assert not pet.popup.isVisible()
    pet.deleteLater()


def test_steps_aside_for_full_screen_apps(monkeypatch, tmp_path):
    P, pet = _pet(monkeypatch, tmp_path)
    pet.show_at(300, 300)
    monkeypatch.setattr(P, "fullscreen_busy", lambda: True)
    pet._refresh_ambient()
    assert not pet.isVisible() and pet._auto_hidden
    assert not pet.say("hello")                                      # no bubbles over your game
    monkeypatch.setattr(P, "fullscreen_busy", lambda: False)
    pet._refresh_ambient()
    assert pet.isVisible() and not pet._auto_hidden
    pet.hide()
    pet.deleteLater()


def test_snaps_to_screen_edges(monkeypatch, tmp_path):
    P, pet = _pet(monkeypatch, tmp_path)
    geo = pet._screen_geo()
    assert pet.snapped(geo.left() + 10, geo.top() + 200)[0] == geo.left()
    x, y = pet.snapped(geo.right() - pet.width() - 5, geo.bottom() - pet.height() - 12)
    assert x == geo.right() + 1 - pet.width() and y == geo.bottom() + 1 - pet.height()
    assert pet.snapped(geo.left() + 200, geo.top() + 200) == (geo.left() + 200, geo.top() + 200)   # middle: stays
    pet.deleteLater()


def test_jarvis_wires_voice_hotkey_bubbles_customise_and_tray():
    main = (ROOT / "main.py").read_text(encoding="utf-8-sig")
    assert main.count("_pet_command(") == 3                          # definition + typed + spoken
    assert "Hotkey((0x11, 0x12, 0x50))" in main and '_pet.request("toggle")' in main
    assert '_pet.request("say"' in main and "ev.kind not in attention.ASKED_KINDS" in main
    ui = (ROOT / "ui.py").read_text(encoding="utf-8-sig")
    assert '"pet": pet_cfg' in ui and '_pet.request("settings", data["pet"])' in ui
    assert ui.count('menu.addAction("Show Pet")') == 2
    ov = (ROOT / "gui" / "overlays.py").read_text(encoding="utf-8-sig")
    assert '"pet": {"enabled": self.pet_on.isChecked()' in ov
