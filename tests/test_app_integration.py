"""Desktop-app plumbing: single instance messages, notification links, toast XML."""

import threading
import time
import uuid

from core import notify, uri_handler
from core.brand import CREDIT, DEVELOPER_NAME


def _qt_app():
    from PyQt6.QtCore import QCoreApplication
    return QCoreApplication.instance() or QCoreApplication([])


def test_second_launch_message_reaches_running_instance():
    from PyQt6.QtCore import QCoreApplication
    from core.app_integration import SingleInstance

    app = _qt_app()
    pipe = f"JarvisTest.{uuid.uuid4().hex[:8]}"
    first = SingleInstance(pipe)
    assert first.acquire() is True and first.is_primary
    got, woke = [], []
    first.message.connect(got.append)
    first.activated.connect(lambda: woke.append(1))

    def second_launch(payload: bytes):
        time.sleep(0.2)
        with open(rf"\\.\pipe\{pipe}", "wb", buffering=0) as p:   # exactly what main.py's pre-check does
            p.write(payload)

    for payload in (b"uri:jarvis://open?panel=security", b"activate"):
        t = threading.Thread(target=second_launch, args=(payload,))
        t.start()
        deadline = time.time() + 5
        while time.time() < deadline and len(woke) < (1 if payload != b"activate" else 2):
            QCoreApplication.processEvents()
            time.sleep(0.02)
        t.join()
    assert got == ["uri:jarvis://open?panel=security"]   # 'activate' carries no message
    assert len(woke) == 2


def test_another_instance_is_detected():
    from core.app_integration import SingleInstance
    _qt_app()
    pipe = f"JarvisTest.{uuid.uuid4().hex[:8]}"
    a = SingleInstance(pipe)
    assert a.acquire() is True
    b = SingleInstance(pipe)
    assert b.acquire() is False


def test_links_only_show_things():
    assert uri_handler.parse("uri:https://evil.example/x") == {}
    assert uri_handler.parse("uri:jarvis://open?panel=report&path=C:/Windows/win.ini")["panel"] == "report"
    assert uri_handler._safe_report("C:/Windows/win.ini") is None
    assert uri_handler._safe_report("config/api_keys.json") is None

    shown = []

    class FakeUI:
        def show_main(self):
            shown.append("window")

        def show_content(self, title, text):
            shown.append((title, text))

    uri_handler.remember("security", "Jarvis security alert", "New startup program: x.exe")
    uri_handler.handle("uri:jarvis://open?panel=security", FakeUI())
    assert shown == ["window", ("Jarvis security alert", "New startup program: x.exe")]


def test_toast_xml_is_escaped_and_reminders_get_windows_snooze():
    xml = notify._toast_xml('Alert <b>&"', "body & <script>", [("Open", "jarvis://open?a=1&b=2")], "jarvis://open")
    assert "<script>" not in xml and "&lt;script&gt;" in xml
    assert "jarvis://open?a=1&amp;b=2" in xml
    rem = notify._toast_xml("⏰ Reminder", "call mom", None, "jarvis://open", reminder=True)
    assert 'scenario="reminder"' in rem and 'arguments="snooze"' in rem and 'arguments="dismiss"' in rem


def test_alert_skips_when_owner_is_looking_and_in_quiet_hours(monkeypatch):
    sent = []
    monkeypatch.setattr(notify, "notify_async", lambda *a, **k: sent.append((a, k)))
    monkeypatch.setattr(notify, "_jarvis_in_front", lambda ui: True)
    assert notify.alert(None, "routine", "Routine", "done") is False          # looking at Jarvis
    assert notify.alert(None, "security", "Alert", "x", urgent=True) is True  # urgent still goes
    monkeypatch.setattr(notify, "_jarvis_in_front", lambda ui: False)
    import core.quiet as quiet
    monkeypatch.setattr(quiet, "is_quiet", lambda now=None: True)
    assert notify.alert(None, "report", "Report", "x") is False               # quiet hours
    assert len(sent) == 1


def test_developer_credit_is_fixed():
    # compared by fingerprint, so the name itself only lives in core/brand.py
    import hashlib
    assert hashlib.sha256(DEVELOPER_NAME.encode()).hexdigest() == "9ba18bd5bffbe0dc9e444b6db41f548235f7a803c4f192562325c56b5b89ac2d"
    assert CREDIT == f"Developed by {DEVELOPER_NAME}"
