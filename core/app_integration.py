"""Make Jarvis behave like a proper Windows desktop app.

  • single instance: a second launch wakes the running window instead of starting a rival
  • AppUserModelID: correct taskbar grouping, icon and notification identity
  • start with Windows: a per-user registry Run entry (no admin needed)

Everything here is best-effort and no-ops off Windows.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from core.brand import APP_ID, APP_NAME

BASE_DIR = Path(__file__).resolve().parent.parent
_PIPE = "JarvisSingleInstance.v1"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "Jarvis"


def set_app_user_model_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
    except Exception:
        pass


class SingleInstance(QObject):
    """Holds the one-instance lock. `activated` fires when a second launch pings us;
    `message` carries what it sent (e.g. "uri:jarvis://open?panel=security")."""
    activated = pyqtSignal()
    message = pyqtSignal(str)

    def __init__(self, pipe_name: str = _PIPE):
        super().__init__()
        self._pipe = pipe_name
        self._server: QLocalServer | None = None
        self.is_primary = False

    def acquire(self) -> bool:
        """True if we are the first/only instance. False if another Jarvis is already running."""
        probe = QLocalSocket()
        probe.connectToServer(self._pipe)
        if probe.waitForConnected(300):
            probe.write(b"activate")
            probe.flush()
            probe.waitForBytesWritten(300)
            probe.disconnectFromServer()
            return False
        # stale socket from a crashed instance: remove and claim it
        QLocalServer.removeServer(self._pipe)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        if not self._server.listen(self._pipe):
            return True  # can't listen, but let this instance run anyway
        self.is_primary = True
        return True

    def _on_connection(self):
        """A second launch connected. Collect what it sends as it arrives, and act when it
        hangs up. (Blocking waits inside this handler drop the pipe on Windows.)"""
        while self._server is not None and self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            if conn is None:
                break
            buf = bytearray()

            def _read(c=conn, b=buf):
                try:
                    b.extend(c.readAll().data())
                except Exception:
                    pass

            def _done(c=conn, b=buf):
                _read()
                payload = bytes(b).decode("utf-8", "replace").strip()[:2000]
                self.activated.emit()
                if payload and payload != "activate":
                    self.message.emit(payload)
                c.deleteLater()

            conn.readyRead.connect(_read)
            conn.disconnected.connect(_done)
            if conn.bytesAvailable():
                _read()


# ── start with Windows (per-user Run key) ──
def _launch_command() -> str | None:
    if sys.platform != "win32":
        return None
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --startup'
    # dev: prefer pythonw.exe so there's no console window
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = str(pyw) if pyw.exists() else sys.executable
    return f'"{exe}" "{BASE_DIR / "main.py"}" --startup'


def get_start_with_windows() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, RUN_VALUE)
        return True
    except Exception:
        return False


def set_start_with_windows(enabled: bool) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                cmd = _launch_command()
                if not cmd:
                    return False
                winreg.SetValueEx(k, RUN_VALUE, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, RUN_VALUE)
                except FileNotFoundError:
                    pass
        return True
    except Exception as exc:
        print(f"[AppIntegration] start-with-Windows failed: {exc}")
        return False
