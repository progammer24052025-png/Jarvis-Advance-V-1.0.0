"""Theme, type and the painted building blocks of the command center.

Every widget reads the accent colour at paint time, so a live re-theme is just
`set_accent(hex)` followed by a repaint.
"""

from __future__ import annotations

import math
import time
from collections import deque

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QLinearGradient, QPainter,
                         QPainterPath, QPen, QRadialGradient, QTextCursor)
from PyQt6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QSizePolicy,
                             QTextEdit, QVBoxLayout, QWidget)

# ── theme ────────────────────────────────────────────────────────────────
_ACCENT = "#ffaa00"
GOOD = "#3ddc97"
WARN = "#ffb020"
BAD = "#ff4d4d"
TEXT = "#e8ecf3"
MUTED = "#8a93a6"
GLASS = QColor(10, 13, 20, 168)
GLASS_EDGE = QColor(255, 255, 255, 22)
_listeners: list = []


def accent() -> QColor:
    return QColor(_ACCENT)


def accent_hex() -> str:
    return _ACCENT


def set_accent(hex_color: str) -> None:
    """Re-theme every command-center widget live."""
    global _ACCENT
    c = QColor(hex_color)
    if not c.isValid():
        return
    _ACCENT = c.name()
    for fn in list(_listeners):
        try:
            fn()
        except Exception:
            pass


def on_theme_change(fn) -> None:
    _listeners.append(fn)


def mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor(int(a.red() + (b.red() - a.red()) * t), int(a.green() + (b.green() - a.green()) * t),
                  int(a.blue() + (b.blue() - a.blue()) * t), int(a.alpha() + (b.alpha() - a.alpha()) * t))


def alpha(c: QColor, a: int) -> QColor:
    c = QColor(c)
    c.setAlpha(max(0, min(255, a)))
    return c


# ── type ─────────────────────────────────────────────────────────────────
_DISPLAY = ["Bahnschrift", "Segoe UI Variable Display", "Segoe UI"]
_MONO = ["Cascadia Mono", "Consolas", "Courier New"]
_families: set[str] | None = None


def _pick(names: list[str]) -> str:
    global _families
    if _families is None:
        try:
            _families = set(QFontDatabase.families())
        except Exception:
            _families = set()
    for n in names:
        if n in _families:
            return n
    return names[-1]


def font(size: float, weight=QFont.Weight.Normal, *, mono: bool = False, spacing: float = 0.0) -> QFont:
    f = QFont(_pick(_MONO if mono else _DISPLAY))
    f.setPointSizeF(size)
    f.setWeight(weight)
    if spacing:
        f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
    return f


# ── glass panel ──────────────────────────────────────────────────────────
class HoloPanel(QWidget):
    """A glass panel with corner brackets, a title and a small tag on the right."""

    def __init__(self, title: str, tag: str = "", parent=None):
        super().__init__(parent)
        self.title = title.upper()
        self.tag = tag.upper()
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 30, 12, 10)
        lay.setSpacing(6)
        self.body = lay
        on_theme_change(self.update)

    def set_tag(self, tag: str) -> None:
        self.tag = (tag or "").upper()
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        path = QPainterPath()
        path.addRoundedRect(r, 10, 10)
        g = QLinearGradient(0, 0, 0, r.height())
        g.setColorAt(0, QColor(16, 20, 30, 182))
        g.setColorAt(1, QColor(8, 10, 16, 168))
        p.fillPath(path, QBrush(g))
        p.setPen(QPen(GLASS_EDGE, 1))
        p.drawPath(path)
        a = accent()
        # corner brackets
        p.setPen(QPen(alpha(a, 200), 1.6))
        L = 12
        for (x, y, dx, dy) in ((r.left(), r.top(), 1, 1), (r.right(), r.top(), -1, 1),
                               (r.left(), r.bottom(), 1, -1), (r.right(), r.bottom(), -1, -1)):
            p.drawLine(QPointF(x, y + dy * 2), QPointF(x, y + dy * L))
            p.drawLine(QPointF(x + dx * 2, y), QPointF(x + dx * L, y))
        # header
        p.setFont(font(8.5, QFont.Weight.DemiBold, spacing=1.6))
        p.setPen(alpha(a, 235))
        p.drawText(QRectF(14, 8, r.width() - 28, 16), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.title)
        if self.tag:
            p.setPen(QColor(MUTED))
            p.drawText(QRectF(14, 8, r.width() - 28, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.tag)
        p.setPen(QPen(alpha(a, 50), 1))
        p.drawLine(QPointF(14, 26), QPointF(r.width() - 14, 26))


# ── ring gauge ───────────────────────────────────────────────────────────
class RingGauge(QWidget):
    def __init__(self, label: str, parent=None):
        super().__init__(parent)
        self.label = label.upper()
        self.value = 0.0
        self._shown = 0.0
        self.text = "--"
        self.setMinimumSize(74, 74)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self._t = QTimer(self)
        self._t.timeout.connect(self._ease)
        self._t.start(33)
        on_theme_change(self.update)

    def set_value(self, pct: float | None, text: str | None = None) -> None:
        self.value = 0.0 if pct is None else max(0.0, min(100.0, float(pct)))
        self.text = text if text is not None else ("N/A" if pct is None else f"{pct:.0f}%")

    def _ease(self):
        d = self.value - self._shown
        if abs(d) > 0.05:
            self._shown += d * 0.18
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        s = min(self.width(), self.height()) - 8
        r = QRectF((self.width() - s) / 2, 4, s, s)
        a = accent()
        hot = self._shown >= 85
        col = QColor(BAD) if hot else (QColor(WARN) if self._shown >= 70 else a)
        p.setPen(QPen(alpha(a, 35), 5, cap=Qt.PenCapStyle.FlatCap))
        p.drawArc(r, 225 * 16, -270 * 16)
        p.setPen(QPen(col, 5, cap=Qt.PenCapStyle.RoundCap))
        p.drawArc(r, 225 * 16, int(-270 * 16 * self._shown / 100.0))
        # ticks
        p.setPen(QPen(alpha(a, 70), 1))
        cx, cy, rad = r.center().x(), r.center().y(), s / 2 + 3
        for i in range(11):
            ang = math.radians(225 - 27 * i)
            p.drawLine(QPointF(cx + math.cos(ang) * rad, cy - math.sin(ang) * rad),
                       QPointF(cx + math.cos(ang) * (rad + 3), cy - math.sin(ang) * (rad + 3)))
        p.setPen(QColor(TEXT))
        p.setFont(font(max(9, s / 6.2), QFont.Weight.Medium, mono=True))
        p.drawText(r.adjusted(0, -6, 0, -6), Qt.AlignmentFlag.AlignCenter, self.text)
        p.setPen(QColor(MUTED))
        p.setFont(font(7, QFont.Weight.DemiBold, spacing=1.2))
        p.drawText(r.adjusted(0, s * 0.30, 0, s * 0.30), Qt.AlignmentFlag.AlignCenter, self.label)


# ── sparkline ────────────────────────────────────────────────────────────
class Sparkline(QWidget):
    def __init__(self, samples: int = 60, parent=None):
        super().__init__(parent)
        self.up: deque = deque([0.0] * samples, maxlen=samples)
        self.down: deque = deque([0.0] * samples, maxlen=samples)
        self.setMinimumHeight(54)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        on_theme_change(self.update)

    def push(self, down_bps: float, up_bps: float) -> None:
        self.down.append(max(0.0, down_bps))
        self.up.append(max(0.0, up_bps))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        peak = max(max(self.down), max(self.up), 50_000.0)
        a = accent()
        p.setPen(QPen(alpha(a, 25), 1))
        for i in range(1, 4):
            p.drawLine(0, int(h * i / 4), w, int(h * i / 4))

        def line(data, col, fill):
            n = len(data)
            pts = [QPointF(w * i / (n - 1), h - 3 - (h - 6) * (v / peak)) for i, v in enumerate(data)]
            path = QPainterPath(pts[0])
            for pt in pts[1:]:
                path.lineTo(pt)
            if fill:
                area = QPainterPath(path)
                area.lineTo(w, h)
                area.lineTo(0, h)
                area.closeSubpath()
                g = QLinearGradient(0, 0, 0, h)
                g.setColorAt(0, alpha(col, 90))
                g.setColorAt(1, alpha(col, 0))
                p.fillPath(area, QBrush(g))
            p.setPen(QPen(col, 1.4))
            p.drawPath(path)

        line(self.down, a, True)
        line(self.up, QColor(GOOD), False)


def human_rate(bps: float) -> str:
    for unit in ("B/s", "KB/s", "MB/s", "GB/s"):
        if bps < 1024 or unit == "GB/s":
            return f"{bps:.0f} {unit}" if unit == "B/s" else f"{bps:.1f} {unit}"
        bps /= 1024
    return "--"


# ── data row ─────────────────────────────────────────────────────────────
class DataRow(QWidget):
    def __init__(self, label: str, value: str = "--", tone: str = "text", parent=None):
        super().__init__(parent)
        self.label, self.value, self.tone = label.upper(), value, tone
        self.setFixedHeight(22)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        on_theme_change(self.update)

    def set_value(self, value: str, tone: str | None = None) -> None:
        self.value = value
        if tone:
            self.tone = tone
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        p.setFont(font(8, QFont.Weight.DemiBold, spacing=1.1))
        p.setPen(QColor(MUTED))
        p.drawText(r, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.label)
        col = {"good": QColor(GOOD), "warn": QColor(WARN), "bad": QColor(BAD), "accent": accent()}.get(self.tone, QColor(TEXT))
        p.setPen(col)
        p.setFont(font(8.5, QFont.Weight.Medium, mono=True))
        p.drawText(r, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self.value)
        p.setPen(QPen(QColor(255, 255, 255, 14), 1))
        p.drawLine(QPointF(0, r.height() - 1), QPointF(r.width(), r.height() - 1))


# ── state chip ───────────────────────────────────────────────────────────
STATE_STYLE = {
    "LISTENING": ("LISTENING", GOOD), "THINKING": ("THINKING", None), "EXECUTING": ("WORKING", None),
    "SPEAKING": ("SPEAKING", None), "MUTED": ("MIC MUTED", MUTED), "IDLE": ("STANDBY", MUTED),
    "DANGER": ("SAFETY HOLD", BAD), "ERROR": ("ERROR", BAD),
}


class StateChip(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.state = "LISTENING"
        self.setFixedHeight(28)
        self.setMinimumWidth(150)
        self._phase = 0.0
        t = QTimer(self)
        t.timeout.connect(self._tick)
        t.start(50)
        on_theme_change(self.update)

    def set_state(self, state: str) -> None:
        self.state = (state or "IDLE").upper()
        self.update()

    def _tick(self):
        self._phase = (self._phase + 0.12) % (2 * math.pi)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        label, col = STATE_STYLE.get(self.state, (self.state, None))
        c = QColor(col) if col else accent()
        r = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(r, r.height() / 2, r.height() / 2)
        p.fillPath(path, alpha(c, 28))
        p.setPen(QPen(alpha(c, 150), 1))
        p.drawPath(path)
        pulse = 0.55 + 0.45 * math.sin(self._phase)
        p.setBrush(alpha(c, int(120 + 135 * pulse)))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(QPointF(r.left() + 14, r.center().y()), 4, 4)
        p.setPen(c)
        p.setFont(font(8.5, QFont.Weight.Bold, spacing=1.8))
        p.drawText(r.adjusted(26, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, label)


# ── arc logo ─────────────────────────────────────────────────────────────
class ArcLogo(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(34, 34)
        self._a = 0.0
        t = QTimer(self)
        t.timeout.connect(lambda: (setattr(self, "_a", (self._a + 2) % 360), self.update()))
        t.start(40)
        on_theme_change(self.update)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        a = accent()
        r = QRectF(3, 3, 28, 28)
        p.setPen(QPen(alpha(a, 70), 1.2))
        p.drawEllipse(r)
        p.setPen(QPen(a, 2.2, cap=Qt.PenCapStyle.RoundCap))
        p.drawArc(r, int(self._a * 16), 110 * 16)
        p.drawArc(r, int((self._a + 180) * 16), 110 * 16)
        g = QRadialGradient(r.center(), 7)
        g.setColorAt(0, alpha(a, 255))
        g.setColorAt(1, alpha(a, 0))
        p.setBrush(QBrush(g))
        p.setPen(Qt.PenStyle.NoPen)
        p.drawEllipse(r.center(), 7, 7)


# ── waveform ─────────────────────────────────────────────────────────────
class Waveform(QWidget):
    """Bars that follow the live voice level: your mic while listening, Jarvis while speaking."""

    def __init__(self, bars: int = 48, parent=None):
        super().__init__(parent)
        self.levels: deque = deque([0.0] * bars, maxlen=bars)
        self.speaking = False
        self.setFixedHeight(44)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._target = 0.0
        self._cur = 0.0
        t = QTimer(self)
        t.timeout.connect(self._tick)
        t.start(40)
        on_theme_change(self.update)

    def push(self, level: float, speaking: bool) -> None:
        self._target = max(0.0, min(1.0, float(level)))
        self.speaking = speaking

    def _tick(self):
        self._cur += (self._target - self._cur) * 0.35
        self._target *= 0.86  # decays if no new level arrives
        jitter = 0.04 * math.sin(time.time() * 7)
        self.levels.append(max(0.0, self._cur + (jitter if self._cur > 0.05 else 0)))
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        n = len(self.levels)
        w, h = self.width(), self.height()
        bw = w / n
        col = accent() if self.speaking else QColor(GOOD)
        for i, v in enumerate(self.levels):
            dist = abs(i - n / 2) / (n / 2)
            amp = max(1.5, v * (h - 6) * (1 - dist * 0.55))
            x = i * bw + bw * 0.25
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(alpha(col, int(70 + 150 * (1 - dist))))
            p.drawRoundedRect(QRectF(x, (h - amp) / 2, bw * 0.5, amp), 1.5, 1.5)


# ── transcript log ───────────────────────────────────────────────────────
class TranscriptLog(QTextEdit):
    """Chat-style log: who said it, when, colour-coded. System lines are quieter."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setStyleSheet(
            "QTextEdit { background: transparent; color: #dfe4ee; border: none; }"
            "QScrollBar:vertical { background: transparent; width: 6px; }"
            "QScrollBar::handle:vertical { background: rgba(255,255,255,40); border-radius: 3px; }"
            "QScrollBar::add-line, QScrollBar::sub-line { height: 0; }")
        self.setFont(font(9.5))
        self._count = 0

    def append_line(self, raw: str) -> None:
        import html as _h
        raw = (raw or "").strip()
        if not raw:
            return
        who, text = "", raw
        low = raw.lower()
        for prefix, label in (("you:", "YOU"), ("jarvis:", "JARVIS"), ("sys:", "SYS"), ("err:", "ERROR")):
            if low.startswith(prefix):
                who, text = label, raw.split(":", 1)[1].strip()
                break
        stamp = time.strftime("%H:%M")
        a = accent_hex()
        color = {"YOU": GOOD, "JARVIS": a, "ERROR": BAD}.get(who, MUTED)
        body_col = "#e8ecf3" if who in ("YOU", "JARVIS") else "#9aa3b5"
        size = "9.5pt" if who in ("YOU", "JARVIS") else "8.5pt"
        label = f'<span style="color:{color};font-weight:700;letter-spacing:1px">{who or "·"}</span>'
        html_line = (f'<div style="margin:2px 0 6px 0"><span style="color:#5d6678;font-family:Consolas">{stamp}</span>'
                     f'&nbsp;&nbsp;{label}&nbsp;&nbsp;<span style="color:{body_col};font-size:{size}">{_h.escape(text)}</span></div>')
        self.append(html_line)
        self._count += 1
        if self._count > 400:  # keep it light
            cur = self.textCursor()
            cur.movePosition(QTextCursor.MoveOperation.Start)
            cur.movePosition(QTextCursor.MoveOperation.Down, QTextCursor.MoveMode.KeepAnchor, 80)
            cur.removeSelectedText()
            self._count -= 80
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    # compatibility with the old LogWidget API
    def append_log(self, text: str) -> None:
        self.append_line(text)


# ── buttons / inputs ─────────────────────────────────────────────────────
def hud_button_style(tone: str = "accent", filled: bool = False) -> str:
    c = {"good": GOOD, "bad": BAD, "warn": WARN, "muted": MUTED}.get(tone, accent_hex())
    bg = f"rgba({QColor(c).red()},{QColor(c).green()},{QColor(c).blue()},{60 if filled else 18})"
    return (f"QPushButton {{ background: {bg}; color: {c}; border: 1px solid {c}; border-radius: 8px;"
            f" padding: 6px 12px; font-family: '{_pick(_DISPLAY)}'; font-size: 9pt; font-weight: 700; letter-spacing: 1px; }}"
            f"QPushButton:hover {{ background: rgba({QColor(c).red()},{QColor(c).green()},{QColor(c).blue()},90); }}"
            f"QPushButton:disabled {{ color: #555c6a; border-color: #333a46; }}")


class HudButton(QPushButton):
    def __init__(self, text: str, tone: str = "accent", parent=None):
        super().__init__(text.upper(), parent)
        self.tone = tone
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(34)
        self._restyle()
        on_theme_change(self._restyle)

    def set_tone(self, tone: str) -> None:
        self.tone = tone
        self._restyle()

    def _restyle(self):
        self.setStyleSheet(hud_button_style(self.tone))


class HudInput(QLineEdit):
    def __init__(self, placeholder: str = "", parent=None):
        super().__init__(parent)
        self.setPlaceholderText(placeholder)
        self.setMinimumHeight(36)
        self._restyle()
        on_theme_change(self._restyle)

    def _restyle(self):
        self.setStyleSheet(
            f"QLineEdit {{ background: rgba(255,255,255,10); color: {TEXT}; border: 1px solid rgba(255,255,255,30);"
            f" border-radius: 8px; padding: 6px 10px; font-family: '{_pick(_DISPLAY)}'; font-size: 10pt; }}"
            f"QLineEdit:focus {{ border: 1px solid {accent_hex()}; }}")


def label(text: str, size: float = 9, color: str = TEXT, weight=QFont.Weight.Normal, mono=False) -> QLabel:
    lb = QLabel(text)
    lb.setFont(font(size, weight, mono=mono))
    lb.setStyleSheet(f"color: {color}; background: transparent;")
    lb.setWordWrap(True)
    return lb
