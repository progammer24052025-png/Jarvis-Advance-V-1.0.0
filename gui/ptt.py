"""Push-to-talk: the mic listens only while Ctrl+Space is held (works even when Jarvis isn't focused).

Uses Windows' GetAsyncKeyState, polled every 30 ms on the UI thread: read-only key state,
no keyboard hook, nothing recorded, no admin rights needed.
"""

from __future__ import annotations

import sys

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

VK_CONTROL, VK_SPACE = 0x11, 0x20


class Hotkey(QObject):
    """Fires `pressed` once when all the given virtual keys are held together (e.g. Ctrl+Alt+Shift+X).
    Same read-only GetAsyncKeyState polling as push-to-talk: no hook, nothing recorded."""
    pressed = pyqtSignal()

    def __init__(self, keys: tuple[int, ...], parent=None):
        super().__init__(parent)
        self._keys = tuple(keys)
        self._down = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._user32 = None
        if sys.platform == "win32":
            try:
                import ctypes
                self._user32 = ctypes.windll.user32
            except Exception:
                self._user32 = None

    def set_enabled(self, on: bool) -> None:
        if on and self._user32 is not None:
            self._timer.start(60)
        else:
            self._timer.stop()

    def _poll(self):
        down = all(self._user32.GetAsyncKeyState(k) & 0x8000 for k in self._keys)
        if down and not self._down:
            self.pressed.emit()
        self._down = down


class PushToTalk(QObject):
    held_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._held = False
        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll)
        self._user32 = None
        if sys.platform == "win32":
            try:
                import ctypes
                self._user32 = ctypes.windll.user32
            except Exception:
                self._user32 = None

    @property
    def available(self) -> bool:
        return self._user32 is not None

    def set_enabled(self, on: bool) -> None:
        if on and self.available:
            self._timer.start(30)
        else:
            self._timer.stop()
            if self._held:
                self._held = False
                self.held_changed.emit(False)

    def _poll(self):
        down = bool(self._user32.GetAsyncKeyState(VK_CONTROL) & 0x8000) and bool(self._user32.GetAsyncKeyState(VK_SPACE) & 0x8000)
        if down != self._held:
            self._held = down
            self.held_changed.emit(down)
