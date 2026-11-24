"""Step 4: encrypted keys and the first-run setup wizard."""

import json
import os

from core import secrets, setup_store

GOOD_KEY = "AIza" + "B" * 35
TG_TOKEN = "123456789:AA" + "x" * 33


def _isolate(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir(exist_ok=True)
    keys, tg = secrets.protect(cfg / "api_keys.json"), secrets.protect(cfg / "telegram_bot.json")
    monkeypatch.setattr(setup_store, "KEYS_FILE", keys)
    monkeypatch.setattr(setup_store, "TELEGRAM_FILE", tg)
    monkeypatch.setattr(setup_store, "SETTINGS_FILE", cfg / "app_settings.json")
    monkeypatch.setattr(setup_store, "IDENTITY_FILE", cfg / "identity.json")
    monkeypatch.setattr(setup_store, "ensure_data_dirs", lambda: tmp_path)
    import core.app_integration as ai
    monkeypatch.setattr(ai, "set_start_with_windows", lambda on: True)
    monkeypatch.setattr(ai, "get_start_with_windows", lambda: False)
    import core.notify as nt
    monkeypatch.setattr(nt, "register", lambda: None)
    return cfg, keys, tg


# ── encrypted key files ─────────────────────────────────────────────────────
def test_keys_are_encrypted_on_disk_and_read_back_normally(tmp_path):
    p = secrets.protect(tmp_path / "api_keys.json")
    p.write_text(json.dumps({"gemini_api_key": GOOD_KEY}), encoding="utf-8")
    raw = (tmp_path / "api_keys.json").read_text(encoding="utf-8")
    if secrets.dpapi_available():
        assert GOOD_KEY not in raw and secrets.MARK in raw
    assert json.loads(p.read_text(encoding="utf-8"))["gemini_api_key"] == GOOD_KEY
    with p.open("r", encoding="utf-8") as f:                      # the way most old code reads it
        assert json.load(f)["gemini_api_key"] == GOOD_KEY
    with p.open("w", encoding="utf-8") as f:                      # …and writes it
        json.dump({"gemini_api_key": "changed"}, f)
    assert json.loads(p.read_text())["gemini_api_key"] == "changed"
    assert "changed" not in (tmp_path / "api_keys.json").read_text(encoding="utf-8") or not secrets.dpapi_available()


def test_old_plain_file_is_encrypted_the_first_time_it_is_read(tmp_path):
    old = os.environ.get("JARVIS_SECRETS_MIGRATE")
    os.environ["JARVIS_SECRETS_MIGRATE"] = "1"
    try:
        _check_migration(tmp_path)
    finally:
        os.environ["JARVIS_SECRETS_MIGRATE"] = old or "0"


def _check_migration(tmp_path):
    plain = tmp_path / "telegram_bot.json"
    plain.write_text(json.dumps({"bot_token": TG_TOKEN, "owner_chat_id": "42"}), encoding="utf-8")
    p = secrets.protect(plain)
    assert json.loads(p.read_text())["owner_chat_id"] == "42"
    if secrets.dpapi_available():
        assert TG_TOKEN not in plain.read_text(encoding="utf-8")
        assert json.loads(p.read_text())["bot_token"] == TG_TOKEN


def test_other_files_next_to_a_secret_stay_plain(tmp_path):
    secrets.protect(tmp_path / "api_keys.json")
    other = secrets.SecretPath(tmp_path) / "app_settings.json"
    other.write_text('{"a": 1}', encoding="utf-8")
    assert (tmp_path / "app_settings.json").read_text(encoding="utf-8") == '{"a": 1}'


def test_main_code_reads_keys_through_the_encrypted_file():
    import main
    import ui
    from core import secrets as s
    assert isinstance(main.API_CONFIG_PATH, s.SecretPath) and main.API_CONFIG_PATH._is_secret()
    assert isinstance(ui.API_FILE, s.SecretPath) and ui.API_FILE._is_secret()


# ── setup answers ───────────────────────────────────────────────────────────
def test_validation_messages_are_plain():
    errs = setup_store.validate({**setup_store.DEFAULTS, "owner_name": "", "gemini_key": "abc",
                                 "telegram_token": "nope", "quiet_enabled": True, "quiet_start": "25:99"})
    assert "your name" in errs["owner_name"]
    assert "AIza" in errs["gemini_key"]
    assert "bot token" in errs["telegram_token"]
    assert "quiet_hours" in errs
    assert setup_store.validate({**setup_store.DEFAULTS, "owner_name": "Alex", "gemini_key": GOOD_KEY}) == {}


def test_fresh_install_needs_setup_and_save_writes_everything(tmp_path, monkeypatch):
    cfg, keys, tg = _isolate(tmp_path, monkeypatch)
    assert setup_store.needs_setup()
    result = setup_store.save({"owner_name": "Alex Mercer", "address_as": "sir", "languages": ["English", "Gujarati"],
                               "city": "Surat", "gemini_key": GOOD_KEY, "groq_key": "gsk_" + "y" * 30,
                               "telegram_token": TG_TOKEN, "telegram_chat_id": "987654",
                               "voice_name": "Puck", "app_theme": "#00d4ff", "assistant_name": "Friday",
                               "quiet_enabled": True, "quiet_start": "11 pm", "quiet_end": "7",
                               "security_watch": False, "clipboard_helper": False})
    assert result["preferred"] == "Alex Sir" and result["assistant"] == "Friday"
    assert not setup_store.needs_setup()
    ident = json.loads((cfg / "identity.json").read_text(encoding="utf-8"))
    assert ident["owner"]["name"] == "Alex Mercer" and ident["owner"]["languages"] == ["English", "Gujarati"]
    assert ident["assistant"]["name"] == "Friday"
    assert json.loads(keys.read_text())["gemini_api_key"] == GOOD_KEY
    assert json.loads(keys.read_text())["groq_api_key"].startswith("gsk_")
    assert json.loads(tg.read_text())["owner_chat_id"] == "987654"
    if secrets.dpapi_available():
        assert GOOD_KEY not in (cfg / "api_keys.json").read_text(encoding="utf-8")
        assert TG_TOKEN not in (cfg / "telegram_bot.json").read_text(encoding="utf-8")
    st = json.loads((cfg / "app_settings.json").read_text(encoding="utf-8"))
    assert st["voice_name"] == "Puck" and st["app_theme"] == "#00d4ff"
    assert st["quiet_hours"] == {"enabled": True, "start": "23:00", "end": "07:00"}
    assert st["security_watch_enabled"] is False and st["clipboard_helper_enabled"] is False
    assert st["setup_version"] == setup_store.SETUP_VERSION
    again = setup_store.current_answers()                            # re-running setup pre-fills
    assert again["owner_name"] == "Alex Mercer" and again["gemini_key"] == GOOD_KEY and again["voice_name"] == "Puck"


def test_nothing_is_saved_when_answers_are_invalid(tmp_path, monkeypatch):
    cfg, keys, tg = _isolate(tmp_path, monkeypatch)
    try:
        setup_store.save({"owner_name": "", "gemini_key": GOOD_KEY})
    except ValueError as exc:
        assert "name" in str(exc)
    else:
        raise AssertionError("saved without a name")
    assert not (cfg / "identity.json").exists() and not (cfg / "api_keys.json").exists()


# ── the wizard itself (offscreen) ───────────────────────────────────────────
def test_wizard_walks_all_pages_and_saves_on_finish(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    cfg, keys, tg = _isolate(tmp_path, monkeypatch)
    from PyQt6.QtWidgets import QApplication, QWidget
    app = QApplication.instance() or QApplication([])
    import gui.setup_wizard as sw
    monkeypatch.setattr(sw, "list_audio_devices", lambda: ([(0, "Mic A")], [(1, "Speaker B")]))
    host = QWidget()
    host.resize(1200, 900)
    wz = sw.SetupWizard(host, first_run=True)
    done = []
    wz.finished.connect(done.append)
    assert wz.stack.count() == 8
    wz._next()                                               # welcome → about you
    wz._next()                                               # no name yet: stays and explains
    assert wz.stack.currentIndex() == 1 and "name" in wz.err.text().lower()
    wz.in_name.setText("Alex Mercer")
    wz._next()
    wz.in_key.setText("not-a-key")
    wz._next()
    assert wz.stack.currentIndex() == 2 and "AIza" in wz.err.text()
    wz.in_key.setText(GOOD_KEY)
    wz._next()                                               # → audio
    wz.cb_mic.setCurrentIndex(1)
    wz._next()                                               # → look
    wz._pick_colour("#3ddc97")
    wz._next()                                               # → remote
    wz.in_tg.setText(TG_TOKEN)
    wz._next()                                               # token without a confirmed chat: blocked
    assert wz.stack.currentIndex() == 5
    wz._tg_found = {"chat_id": "555111", "name": "Alex"}
    wz._tg_yes()
    wz._next()                                               # → behaviour
    wz._next()                                               # → done
    assert "Alex Mercer" in wz.summary.text() and "Telegram: linked" in wz.summary.text()
    assert not (cfg / "identity.json").exists()              # still nothing saved
    wz._next()                                               # Finish
    app.processEvents()
    assert done and done[0]["preferred"] == "Alex Sir"
    st = json.loads((cfg / "app_settings.json").read_text(encoding="utf-8"))
    assert st["input_device"] == "Mic A" and st["app_theme"] == "#3ddc97"
    assert json.loads(tg.read_text())["owner_chat_id"] == "555111"
