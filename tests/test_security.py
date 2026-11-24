"""Security regression tests: owner-only access to Jarvis."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from core.guardian import Guardian
from jarvis_connect.gateway.protocol import ProtocolTypes, build_message
from jarvis_connect.gateway.server import JarvisGateway, JarvisGatewayConfig


def _gateway(tmp_path: Path) -> JarvisGateway:
    cfg = JarvisGatewayConfig(host="127.0.0.1", port=0, advertise=False,
                              registry_path=tmp_path / "devices.json", config_path=tmp_path / "cfg.json")
    return JarvisGateway(tmp_path, cfg)


# ── Jarvis Connect gateway ────────────────────────────────────────────────

def test_unauthenticated_chat_message_is_rejected(tmp_path):
    gw = _gateway(tmp_path)
    received = []
    gw.on_chat_message = received.append
    with TestClient(gw.app, client=("192.168.1.66", 5555)) as c:
        with c.websocket_connect("/ws") as ws:
            ws.send_json(build_message(ProtocolTypes.CHAT_MESSAGE, {"text": "open powershell"}))
            reply = ws.receive_json()
    assert reply["type"] == ProtocolTypes.ERROR
    assert "authenticated" in reply["payload"]["error"].lower()
    assert received == []


def test_authenticated_device_chat_is_delivered(tmp_path):
    gw = _gateway(tmp_path)
    received = []
    gw.on_chat_message = received.append
    record, secret = gw.device_manager.create_from_pairing(
        name="Phone", platform="android", os_version="14", agent_version="1", ip="", capabilities=[], permissions=[])
    with TestClient(gw.app, client=("192.168.1.10", 5555)) as c:
        with c.websocket_connect("/ws") as ws:
            ws.send_json(build_message(ProtocolTypes.AUTHENTICATE, {"device_id": record.device_id, "device_secret": secret}))
            assert ws.receive_json()["type"] == ProtocolTypes.DEVICE_ONLINE
            ws.receive_json()  # capabilities
            ws.send_json(build_message(ProtocolTypes.CHAT_MESSAGE, {"text": "hello jarvis"}))
            ws.send_json(build_message(ProtocolTypes.PING, {}))
            ws.receive_json()  # pong (ensures chat was processed first)
    assert received == ["hello jarvis"]


def test_wrong_device_secret_cannot_authenticate(tmp_path):
    gw = _gateway(tmp_path)
    record, _ = gw.device_manager.create_from_pairing(
        name="Phone", platform="android", os_version="14", agent_version="1", ip="", capabilities=[], permissions=[])
    with TestClient(gw.app, client=("192.168.1.66", 5555)) as c:
        with c.websocket_connect("/ws") as ws:
            ws.send_json(build_message(ProtocolTypes.AUTHENTICATE, {"device_id": record.device_id, "device_secret": "guess"}))
            assert ws.receive_json()["type"] == ProtocolTypes.ERROR


@pytest.mark.parametrize("method,path", [
    ("get", "/gateway/pair"), ("get", "/gateway/devices"), ("get", "/gateway/logs"),
    ("get", "/gateway/pending"), ("post", "/gateway/pending/x/approve"), ("post", "/gateway/devices/x/revoke"),
])
def test_admin_http_routes_refuse_lan_clients(tmp_path, method, path):
    gw = _gateway(tmp_path)
    with TestClient(gw.app, client=("192.168.1.66", 5555)) as c:
        r = getattr(c, method)(path)
    assert r.status_code == 403


def test_admin_http_routes_work_from_localhost(tmp_path):
    gw = _gateway(tmp_path)
    with TestClient(gw.app, client=("127.0.0.1", 5555)) as c:
        assert c.get("/gateway/devices").status_code == 200


def test_pairing_code_bruteforce_is_rate_limited(tmp_path):
    gw = _gateway(tmp_path)
    with TestClient(gw.app, client=("192.168.1.66", 5555)) as c:
        with c.websocket_connect("/ws") as ws:
            errors = []
            for code in range(6):
                ws.send_json(build_message(ProtocolTypes.PAIR_REQUEST, {"pairing_code": f"{code:06d}"}))
                errors.append(ws.receive_json()["payload"].get("error", ""))
    assert "too many" in errors[-1].lower()


# ── Guardian ──────────────────────────────────────────────────────────────

def test_guardian_allows_high_risk_in_clean_owner_context(tmp_path):
    g = Guardian(tmp_path / "audit.log")
    g.note_owner_turn("open powershell and list files", "voice")
    assert g.check("computer_control", {"action": "type"})[0] is True


def test_guardian_holds_high_risk_after_untrusted_content(tmp_path):
    g = Guardian(tmp_path / "audit.log")
    g.note_owner_turn("check my instagram", "voice")
    g.note_tool_result("check_instagram_messages")
    allowed, msg = g.check("send_message", {"platform": "Instagram"})
    assert allowed is False and "SAFETY HOLD" in msg
    # The model retrying on its own does nothing: no new owner turn.
    assert g.check("send_message", {"platform": "Instagram"})[0] is False


def test_guardian_releases_after_owner_confirms(tmp_path):
    g = Guardian(tmp_path / "audit.log")
    g.note_owner_turn("search the web for x", "voice")
    g.note_tool_result("web_search")
    assert g.check("file_controller", {"action": "delete", "path": "a.txt"})[0] is False
    g.note_owner_turn("yes confirm", "voice")
    assert g.check("file_controller", {"action": "delete", "path": "a.txt"})[0] is True
    assert (tmp_path / "audit.log").exists()


def test_guardian_owner_can_decline(tmp_path):
    g = Guardian()
    g.note_owner_turn("read my screen", "voice")
    g.note_tool_result("screen_process")
    assert g.check("agent_task", {})[0] is False
    g.note_owner_turn("no, cancel that", "voice")
    allowed, msg = g.check("agent_task", {})
    assert allowed is False and "declined" in msg


def test_guardian_smart_home_only_holds_security_devices():
    g = Guardian()
    g.note_owner_turn("search something", "voice")
    g.note_tool_result("web_search")
    assert g.check("smart_home_control", {"command": "turn on the TV"})[0] is True
    assert g.check("smart_home_control", {"command": "unlock the front door"})[0] is False


def test_guardian_low_risk_tools_never_blocked(tmp_path):
    g = Guardian()
    g.note_tool_result("web_search")
    assert g.check("weather_report", {"city": "Delhi"})[0] is True
    assert g.check("file_controller", {"action": "read"})[0] is True


# ── Discord owner allowlist ───────────────────────────────────────────────

def test_discord_owner_id_loader(tmp_path, monkeypatch):
    import discord_bot
    (tmp_path / "config").mkdir()
    monkeypatch.setattr(discord_bot, "_data_dir", lambda: tmp_path)
    assert discord_bot._load_owner_user_id() is None
    (tmp_path / "config" / "discord_bot.json").write_text(json.dumps({"owner_user_id": "123456789012345678"}))
    assert discord_bot._load_owner_user_id() == 123456789012345678


# ── Dashboard login lockout / token expiry ────────────────────────────────

def test_dashboard_login_lockout_and_token_expiry(monkeypatch):
    from dashboard import server as ds
    lim = ds._LoginLimiter(max_failures=5, base_lock_secs=900)
    for _ in range(5):
        lim.fail("1.2.3.4")
    assert lim.locked("1.2.3.4") and not lim.locked("5.6.7.8")

    toks = ds._ExpiringTokens(ttl_secs=10)
    toks.add("abc")
    assert "abc" in toks
    real = ds.time.time
    monkeypatch.setattr(ds.time, "time", lambda: real() + 11)
    assert "abc" not in toks
