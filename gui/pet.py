"""Orbi, the Jarvis pet: a cute desktop companion that shows what Jarvis is doing.

Replaces the old floating orb launcher with the same signals and menu (single click → command bar,
double click → control panel, right click → menu, drag to move), so the rest of Jarvis is unchanged.

  • drawn entirely in code (no images): sharp at any size, follows Jarvis's accent colour
  • moods follow Jarvis's real state: listening (only while you talk), thinking, working, speaking (mouth
    moves with Jarvis's voice), done, muted, error, safety hold, and the owner's rules: silent, focus,
    quiet hours / paused
  • light on the PC: ~20 fps when calm, 30 fps when busy, stopped while hidden or the PC is locked
  • `draw_pet()` is also used to make Jarvis's icons (tools/make_icons.py)

Design: "Orbi Classic", chosen from the preview in experiments/pet_preview.
"""

from __future__ import annotations

import json
import math
import time

from PyQt6.QtCore import QObject, QPoint, QPointF, QRect, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QAction, QColor, QCursor, QFont, QFontMetrics, QPainter, QPainterPath, QPen,
                         QRadialGradient)
from PyQt6.QtWidgets import QApplication, QMenu, QWidget

INK = QColor("#1b1e2e")
BLUSH = QColor(255, 120, 150)
MOODS = ("idle", "listening", "thinking", "working", "speaking", "done", "muted", "silent", "focus", "sleep",
         "safety", "error", "message")
SIZES = {"small": 56, "medium": 72, "large": 96}


def _a(c, alpha: float) -> QColor:
    c = QColor(c)
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


def _blink(t: float) -> float:
    """1 = open, 0 = closed: a quick blink every ~3.7 s, a double blink every third time."""
    ph = t % 3.7
    for start in (0.0, 0.32 if int(t / 3.7) % 3 == 0 else -9):
        d = ph - start
        if 0 <= d < 0.16:
            return abs(d - 0.08) / 0.08
    return 1.0


# ── drawing ──────────────────────────────────────────────────────────────────
def draw_pet(p: QPainter, cx: float, cy: float, size: float, *, mood: str = "idle", t: float = 0.0,
             look: tuple[float, float] = (0.0, 0.0), accent: str = "#ffaa00", voice: float | None = None,
             motion: float = 1.0, icon: bool = False, simple: bool | None = None) -> None:
    """Draw Orbi centred at (cx, cy); `size` ≈ body width in px. `voice` (0–1) drives the mouth when speaking.
    `motion` scales how much it bobs and hops (1 lively, 0.5 calm, 0.25 when Windows animations are off).
    `icon` drops the soft glow and shadow (they'd be cut off at an icon's edges); `simple` forces the
    small-size face (body + eyes) on or off."""
    tiny = size < 40 if simple is None else simple   # icon sizes: body + eyes only
    own_bubble = mood != "wave"            # "wave" = message mood while a real speech bubble is showing
    if mood == "wave":
        mood = "message"
    p.save()
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.translate(cx, cy)
    p.scale(size, size)
    p.setPen(Qt.PenStyle.NoPen)
    acc = QColor(accent)
    light, dark = acc.lighter(145), acc.darker(150)

    if mood == "sleep":
        bob, squash = 0.035 + math.sin(t * 1.3) * 0.008, 0.03 * math.sin(t * 1.3)
    elif mood == "done":
        hop = abs(math.sin(t * 5.5))
        bob, squash = -hop * 0.09, 0.05 * (1 - hop) - 0.02
    elif mood == "focus":
        bob, squash = math.sin(t * 0.9) * 0.006, 0.0
    elif mood == "error":
        bob, squash = math.sin(t * 2.2) * 0.015, 0.0
        p.rotate(math.sin(t * 7) * 4 * motion)
    else:
        bob, squash = math.sin(t * 2.3) * 0.022, 0.018 * math.sin(t * 4.6)
    bob, squash = bob * motion, squash * motion
    if tiny:
        bob, squash = 0.0, 0.0

    if not tiny and not icon:                              # soft shadow
        sw = 0.27 - bob * 1.2
        g = QRadialGradient(QPointF(0, 0.47), sw)
        g.setColorAt(0, _a("#000000", 85))
        g.setColorAt(1, _a("#000000", 0))
        p.setBrush(g)
        p.drawEllipse(QPointF(0, 0.47), sw, 0.055)

    p.translate(0, bob)
    if mood == "focus":
        p.setOpacity(0.72)
    rx, ry = 0.40 * (1 + squash), 0.37 * (1 - squash)

    if not tiny:
        if not icon:                                       # glow
            glow = QRadialGradient(QPointF(0, 0), 0.62)
            glow.setColorAt(0.5, _a(acc, 120 if mood == "listening" else 70))
            glow.setColorAt(1, _a(acc, 0))
            p.setBrush(glow)
            p.drawEllipse(QPointF(0, 0), 0.62, 0.62)
        _antenna(p, mood, t, ry, light, dark, acc)

    body = QRadialGradient(QPointF(-0.13, -0.16), 0.62)    # body
    body.setColorAt(0.0, light.lighter(115))
    body.setColorAt(0.45, acc)
    body.setColorAt(1.0, dark)
    p.setBrush(body)
    p.drawEllipse(QPointF(0, 0), rx, ry)
    if not tiny:                                           # rim light + gloss
        p.setPen(QPen(_a("#ffffff", 40), 0.012))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawEllipse(QPointF(0, 0), rx - 0.006, ry - 0.006)
        p.setPen(Qt.PenStyle.NoPen)
        p.save()
        p.translate(-0.17, -0.20)
        p.rotate(-28)
        p.setBrush(_a("#ffffff", 120))
        p.drawEllipse(QPointF(0, 0), 0.10, 0.05)
        p.restore()
        p.setBrush(_a("#ffffff", 150))
        p.drawEllipse(QPointF(-0.05, -0.27), 0.022, 0.022)

    if mood == "silent" and not tiny:                      # headphones
        p.setPen(QPen(QColor("#2a2f45"), 0.05, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(-rx - 0.02, -ry - 0.08, 2 * rx + 0.04, 2 * ry), 20 * 16, 140 * 16)
        p.setPen(Qt.PenStyle.NoPen)
        for s in (-1, 1):
            p.setBrush(QColor("#2a2f45"))
            p.drawRoundedRect(QRectF(s * (rx - 0.02) - 0.06, -0.13, 0.12, 0.2), 0.05, 0.05)
            p.setBrush(_a(light, 230))
            p.drawRoundedRect(QRectF(s * (rx - 0.02) - 0.035, -0.09, 0.07, 0.12), 0.03, 0.03)

    _face(p, mood, t, look, light, tiny, voice)
    if not tiny:
        _hands(p, mood, t, acc, light)
        if own_bubble or mood != "message":
            _overlays(p, mood, t, acc, light)
    p.restore()


def _antenna(p, mood, t, ry, light, dark, acc):
    droop = mood in ("sleep", "focus")
    sway = math.sin(t * 2.0) * 0.02
    tip = QPointF(0.09 + sway, -ry - 0.06) if droop else QPointF(sway, -ry - 0.15)
    p.setPen(QPen(dark, 0.022, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)
    path = QPainterPath(QPointF(0, -ry + 0.02))
    path.quadTo(QPointF(0.0, -ry - 0.08), tip)
    p.drawPath(path)
    p.setPen(Qt.PenStyle.NoPen)
    pulse = 0.5 + 0.5 * math.sin(t * (6 if mood in ("listening", "thinking", "working") else 2.5))
    bright = {"listening": 1.0, "thinking": 0.8, "working": 0.8, "sleep": 0.15, "focus": 0.25}.get(mood, 0.55)
    halo = QRadialGradient(tip, 0.09)
    halo.setColorAt(0, _a(light, 200 * bright * (0.6 + 0.4 * pulse)))
    halo.setColorAt(1, _a(light, 0))
    p.setBrush(halo)
    p.drawEllipse(tip, 0.09, 0.09)
    bulb = QRadialGradient(tip - QPointF(0.01, 0.01), 0.045)
    bulb.setColorAt(0, QColor("#ffffff"))
    bulb.setColorAt(1, light if bright > 0.4 else acc.darker(120))
    p.setBrush(bulb)
    p.drawEllipse(tip, 0.04, 0.04)


def _line(p, col, w):
    p.setPen(QPen(col, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    p.setBrush(Qt.BrushStyle.NoBrush)


def _face(p, mood, t, look, light, tiny, voice):
    eye_y, ex = -0.02, (0.135 if not tiny else 0.15)
    lx, ly = max(-1.0, min(1.0, look[0])), max(-1.0, min(1.0, look[1]))
    if mood == "thinking":
        lx, ly = 0.7, -0.9
    if mood in ("focus", "sleep"):
        lx, ly = 0.0, 0.0
    shift = QPointF(lx * 0.035, ly * 0.025)
    blink = _blink(t) if mood not in ("done", "focus", "sleep", "error") else 1.0
    style = {"done": "happy", "focus": "calm", "sleep": "calm", "error": "swirl", "working": "squint",
             "listening": "wide", "safety": "wide", "message": "wink"}.get(mood, "open")

    for s in (-1, 1):
        c = QPointF(s * ex, eye_y) + shift
        if style == "happy" or (style == "wink" and s == 1):
            _line(p, INK, 0.035)
            path = QPainterPath(c + QPointF(-0.05, 0.02))
            path.quadTo(c + QPointF(0, -0.06), c + QPointF(0.05, 0.02))
            p.drawPath(path)
            p.setPen(Qt.PenStyle.NoPen)
            continue
        if style == "calm":
            _line(p, INK, 0.03)
            path = QPainterPath(c + QPointF(-0.05, -0.005))
            path.quadTo(c + QPointF(0, 0.045), c + QPointF(0.05, -0.005))
            p.drawPath(path)
            p.setPen(Qt.PenStyle.NoPen)
            continue
        if style == "swirl":
            _line(p, INK, 0.015)
            path = QPainterPath()
            for k in range(44):
                a = t * 6 * s + k * 0.3
                r = 0.008 + k * 0.0016
                pt = c + QPointF(math.cos(a) * r, math.sin(a) * r)
                path.moveTo(pt) if k == 0 else path.lineTo(pt)
            p.drawPath(path)
            p.setPen(Qt.PenStyle.NoPen)
            continue
        w, h = 0.068, 0.088
        if style == "wide":
            w, h = 0.078, 0.10
        if style == "squint":
            h = 0.05
        if tiny:
            w, h = 0.08, 0.11
        h *= max(0.08, blink)
        p.setBrush(INK)
        p.drawEllipse(c, w, h)
        if tiny:
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(c + QPointF(0.03, -0.04), 0.03, 0.03)
        elif blink > 0.5:                                   # glossy sparkles
            p.setBrush(QColor("#ffffff"))
            p.drawEllipse(c + QPointF(0.024, -0.034 * (h / 0.088)), 0.026, 0.026)
            p.drawEllipse(c + QPointF(-0.022, 0.03 * (h / 0.088)), 0.012, 0.012)
    if tiny:
        return

    blush = 200 if mood in ("done", "message", "speaking") else 140
    for s in (-1, 1):                                       # cheeks
        g = QRadialGradient(QPointF(s * 0.235, 0.065), 0.07)
        g.setColorAt(0, _a(BLUSH, blush))
        g.setColorAt(1, _a(BLUSH, 0))
        p.setBrush(g)
        p.drawEllipse(QPointF(s * 0.235, 0.065), 0.07, 0.045)

    my = 0.09                                               # mouth
    if mood == "speaking":
        lvl = voice if voice is not None else abs(math.sin(t * 9.0) * math.sin(t * 3.7 + 1))
        o = 0.016 + 0.032 * max(0.0, min(1.0, lvl))
        p.setBrush(QColor("#5b1f33"))
        p.drawEllipse(QPointF(0, my + 0.01), 0.04, o)
        p.setBrush(QColor("#ff7a9a"))
        p.drawEllipse(QPointF(0, my + 0.01 + o * 0.5), 0.022, o * 0.45)
    elif mood in ("safety", "sleep", "listening"):
        r = 0.016 if mood != "sleep" else 0.012 + 0.004 * math.sin(t * 1.3)
        p.setBrush(QColor("#5b1f33"))
        p.drawEllipse(QPointF(0, my + 0.01), r, r * 1.2)
    elif mood == "error":
        _line(p, INK, 0.016)
        path = QPainterPath(QPointF(-0.045, my + 0.01))
        for k in range(1, 7):
            path.lineTo(QPointF(-0.045 + k * 0.015, my + (0.0 if k % 2 else 0.018)))
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
    elif mood != "muted":
        big = mood in ("done", "message")
        _line(p, INK, 0.018)
        wide = 0.055 if big else 0.038
        path = QPainterPath(QPointF(-wide, my))
        path.quadTo(QPointF(0, my + (0.05 if big else 0.032)), QPointF(wide, my))
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)


def _hand(p, c, acc, light):
    g = QRadialGradient(c - QPointF(0.015, 0.015), 0.07)
    g.setColorAt(0, light.lighter(110))
    g.setColorAt(1, acc.darker(115))
    p.setBrush(g)
    p.setPen(QPen(_a(acc.darker(170), 110), 0.009))
    p.drawEllipse(c, 0.058, 0.052)
    p.setPen(Qt.PenStyle.NoPen)


def _hands(p, mood, t, acc, light):
    f = math.sin(t * 2.3 + 0.8) * 0.015
    L, R = QPointF(-0.43, 0.13 + f), QPointF(0.43, 0.13 - f)
    if mood == "muted":
        L, R = QPointF(-0.045, 0.11), QPointF(0.045, 0.115)
    elif mood == "safety":
        R = QPointF(0.40, -0.33 + 0.01 * math.sin(t * 8))
    elif mood == "message":
        R = QPointF(0.42 + 0.04 * math.sin(t * 11), -0.2)
    elif mood == "working":
        L, R = QPointF(-0.12, 0.30), QPointF(0.12, 0.30)
        rc = QPointF(0, 0.31)                                # the little progress ring it holds
        p.setPen(QPen(_a("#ffffff", 70), 0.022))
        p.setBrush(_a("#0b0f17", 120))
        p.drawEllipse(rc, 0.085, 0.085)
        p.setPen(QPen(light.lighter(140), 0.026, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawArc(QRectF(rc.x() - 0.085, rc.y() - 0.085, 0.17, 0.17), int(-t * 360 * 16) % (360 * 16), 250 * 16)
        p.setPen(Qt.PenStyle.NoPen)
    elif mood == "done":
        L, R = QPointF(-0.45, -0.12), QPointF(0.45, -0.12)
    elif mood == "thinking":
        R = QPointF(0.30, 0.27)
    elif mood == "sleep":
        L, R = QPointF(-0.33, 0.27), QPointF(0.33, 0.27)
    elif mood == "speaking":
        R = QPointF(0.44, 0.08 - abs(math.sin(t * 4)) * 0.06)
    _hand(p, L, acc, light)
    _hand(p, R, acc, light)


def _star(p, c, r, col):
    path = QPainterPath(c + QPointF(0, -r))
    for k in range(1, 9):
        a = -math.pi / 2 + k * math.pi / 4
        rr = r if k % 2 == 0 else r * 0.32
        path.lineTo(c + QPointF(math.cos(a) * rr, math.sin(a) * rr))
    p.setBrush(col)
    p.drawPath(path)


def _bubble(p, c, w, h, fill, tail_to):
    path = QPainterPath()
    path.addRoundedRect(QRectF(c.x() - w / 2, c.y() - h / 2, w, h), h / 2.4, h / 2.4)
    tail = QPainterPath(QPointF(c.x() - w * 0.22, c.y() + h * 0.35))
    tail.lineTo(tail_to)
    tail.lineTo(QPointF(c.x() - w * 0.02, c.y() + h * 0.4))
    tail.closeSubpath()
    shape = path.united(tail)
    p.setBrush(_a("#000000", 50))
    p.drawPath(shape.translated(0.008, 0.012))
    p.setBrush(fill)
    p.drawPath(shape)


def _text(p, c, txt, px, col):
    p.save()
    p.translate(c)
    p.scale(0.01, 0.01)
    f = QFont("Segoe UI", 1)
    f.setPixelSize(max(1, int(px * 100)))
    f.setBold(True)
    p.setFont(f)
    p.setPen(col)
    p.drawText(QRectF(-100, -60, 200, 120), Qt.AlignmentFlag.AlignCenter, txt)
    p.restore()
    p.setPen(Qt.PenStyle.NoPen)


def _overlays(p, mood, t, acc, light):
    if mood == "listening":
        for k in range(3):
            ph = (t * 1.2 + k / 3) % 1
            _line(p, _a(light, 200 * (1 - ph)), 0.02)
            r = 0.47 + ph * 0.16
            for s in (-1, 1):
                p.drawArc(QRectF(-r, -r, 2 * r, 2 * r), (150 if s < 0 else -30) * 16, 60 * 16)
        p.setPen(Qt.PenStyle.NoPen)
    elif mood == "thinking":
        p.setBrush(_a("#ffffff", 235))
        p.drawEllipse(QPointF(0.30, -0.40), 0.025, 0.025)
        p.drawEllipse(QPointF(0.38, -0.50), 0.038, 0.038)
        _bubble(p, QPointF(0.56, -0.66), 0.34, 0.17, _a("#ffffff", 240), QPointF(0.45, -0.58))
        for k in range(3):
            p.setBrush(INK)
            p.drawEllipse(QPointF(0.48 + k * 0.08, -0.66 - 0.025 * max(0.0, math.sin(t * 6 - k * 0.9))), 0.022, 0.022)
    elif mood == "done":
        for k in range(5):
            a = k * 2 * math.pi / 5 + t * 0.8
            r = 0.55 + 0.04 * math.sin(t * 4 + k)
            tw = 0.5 + 0.5 * math.sin(t * 7 + k * 1.3)
            _star(p, QPointF(math.cos(a) * r, math.sin(a) * r * 0.9 - 0.05), 0.035 + 0.03 * tw,
                  _a("#fff3b0", 160 + 90 * tw))
    elif mood == "muted":
        c = QPointF(0.36, -0.33)
        p.setBrush(QColor("#ff4d6d"))
        p.drawEllipse(c, 0.085, 0.085)
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(QRectF(c.x() - 0.018, c.y() - 0.05, 0.036, 0.06), 0.018, 0.018)
        _line(p, QColor("#ffffff"), 0.012)
        p.drawLine(c + QPointF(0, 0.015), c + QPointF(0, 0.045))
        _line(p, QColor("#ffffff"), 0.016)
        p.drawLine(c + QPointF(-0.05, -0.05), c + QPointF(0.05, 0.05))
        p.setPen(Qt.PenStyle.NoPen)
    elif mood == "silent":
        c = QPointF(0.42, -0.40 + 0.015 * math.sin(t * 3))
        p.setBrush(QColor("#ffffff"))
        p.drawRoundedRect(QRectF(c.x() - 0.085, c.y() - 0.055, 0.17, 0.11), 0.02, 0.02)
        p.setPen(QPen(acc.darker(130), 0.014, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        p.setBrush(Qt.BrushStyle.NoBrush)
        path = QPainterPath(c + QPointF(-0.07, -0.04))
        path.lineTo(c + QPointF(0, 0.01))
        path.lineTo(c + QPointF(0.07, -0.04))
        p.drawPath(path)
        p.setPen(Qt.PenStyle.NoPen)
    elif mood == "focus":
        for k in range(3):
            a = t * 0.6 + k * 2.1
            p.setBrush(_a(light, 90 + 60 * math.sin(t * 2 + k)))
            p.drawEllipse(QPointF(math.cos(a) * 0.5, -0.1 + math.sin(a) * 0.2), 0.014, 0.014)
    elif mood == "sleep":
        for k in range(3):
            ph = (t * 0.35 + k / 3) % 1
            _text(p, QPointF(0.30 + ph * 0.25, -0.30 - ph * 0.35), "z" if k % 2 else "Z", 0.09 + ph * 0.08,
                  _a("#e8ecf3", 255 * (1 - ph)))
    elif mood == "safety":
        c = QPointF(0.62, -0.62)
        _bubble(p, c, 0.20, 0.20, QColor("#ffd23f"), QPointF(0.50, -0.45))
        _text(p, c, "!", 0.16, INK)
    elif mood == "error":
        g = QPainterPath(QPointF(0.30, -0.27))
        g.quadTo(QPointF(0.26, -0.18), QPointF(0.30, -0.16))
        g.quadTo(QPointF(0.34, -0.18), QPointF(0.30, -0.27))
        p.setBrush(QColor("#8fd3ff"))
        p.drawPath(g)
    elif mood == "message":
        c = QPointF(0.58, -0.62)
        _bubble(p, c, 0.32, 0.18, QColor("#ffffff"), QPointF(0.45, -0.48))
        _text(p, c, "Hi!", 0.11, INK)


# ── what mood to show ───────────────────────────────────────────────────────
WORK_STATES = {"processing", "executing", "working", "running"}
MOOD_LABELS = {"idle": "Ready", "listening": "Listening to you", "thinking": "Thinking…", "working": "Working on it…",
               "speaking": "Speaking", "done": "Done!", "muted": "Mic is muted", "silent": "Texting instead of talking",
               "focus": "Focus mode: staying quiet", "sleep": "Resting (quiet hours)", "safety": "Needs your OK",
               "error": "Something went wrong", "message": "New message", "wave": "New message"}


def pick_mood(state: str, *, safety: bool = False, transient: str | None = None, mic_active: bool = False,
              ambient: str | None = None) -> str:
    """Jarvis's state (+ what's going on around it) → the mood to draw. Highest priority first."""
    s = (state or "idle").strip().lower()
    if safety:
        return "safety"
    if transient:
        return transient
    if s == "muted":
        return "muted"
    if s == "speaking":
        return "speaking"
    if s in WORK_STATES:
        return "working"
    if s == "thinking":
        return "thinking"
    if s in ("error", "danger"):
        return "error"
    if mic_active:
        return "listening"
    return ambient or "idle"


def ambient_mood() -> str | None:
    """The owner's rules right now: paused / quiet hours → sleep, focus mode → focus, no voice → silent."""
    try:
        from core import attention, directives
        if directives.paused():
            return "sleep"
        st = attention.current_state()
        if "quiet_hours" in st.rule_ids and st.voice != "on":
            return "sleep"
        if "focus" in (st.label or "").lower():
            return "focus"
        if st.voice != "on":
            return "silent"
    except Exception:
        pass
    return None


def bubble_allowed(ambient: str | None, importance: str = "normal") -> bool:
    """Speech bubbles follow the owner's rules: focus → only important, quiet hours/paused → only critical."""
    imp = (importance or "normal").lower()
    if ambient == "sleep":
        return imp == "critical"
    if ambient == "focus":
        return imp in ("high", "critical")
    return True


# ── settings (their own file, so nothing else can overwrite them) ───────────
DEFAULTS = {"enabled": True, "size": "medium", "motion": "lively", "name": "Orbi"}


def _pet_file():
    from core.paths import CONFIG_DIR
    return CONFIG_DIR / "pet.json"


def clean_settings(cfg: dict | None) -> dict:
    out = dict(DEFAULTS)
    cfg = cfg if isinstance(cfg, dict) else {}
    out["enabled"] = bool(cfg.get("enabled", True))
    out["size"] = str(cfg.get("size", "medium")).lower() if str(cfg.get("size", "")).lower() in SIZES else "medium"
    out["motion"] = "calm" if str(cfg.get("motion", "")).lower() == "calm" else "lively"
    name = "".join(ch if ch.isprintable() else " " for ch in str(cfg.get("name") or ""))
    out["name"] = " ".join(name.split())[:20].strip() or "Orbi"
    return out


def load_settings() -> dict:
    try:
        return clean_settings(json.loads(_pet_file().read_text(encoding="utf-8")))
    except Exception:
        return dict(DEFAULTS)


def save_settings(changes: dict) -> dict:
    cfg = clean_settings({**load_settings(), **(changes or {})})
    try:
        f = _pet_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except OSError as exc:
        print(f"[Pet] couldn't save settings: {exc}")
    return cfg


def load_size() -> int:
    return SIZES[load_settings()["size"]]


def saved_position():
    try:
        from core.paths import CONFIG_DIR
        pos = (json.loads((CONFIG_DIR / "app_settings.json").read_text(encoding="utf-8")) or {}).get("launcher_pos")
        if isinstance(pos, (list, tuple)) and len(pos) == 2:
            return int(pos[0]), int(pos[1])
    except Exception:
        pass
    return None, None


# ── Windows: reduce motion, full-screen apps ────────────────────────────────
def windows_reduce_motion() -> bool:
    """True when Windows' "Animation effects" is turned off (Settings → Accessibility → Visual effects)."""
    try:
        import ctypes
        val = ctypes.c_int(1)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(val), 0):   # SPI_GETCLIENTAREAANIMATION
            return not bool(val.value)
    except Exception:
        pass
    return False


def fullscreen_busy() -> bool:
    """A full-screen app, game or presentation is in front: the pet steps aside."""
    try:
        import ctypes
        state = ctypes.c_int(0)
        if ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state)) == 0:
            return state.value in (2, 3, 4)   # busy (full screen) · Direct3D full screen · presentation mode
    except Exception:
        pass
    return False


# ── voice: "show the pet" / "hide Orbi" ─────────────────────────────────────
_VERBS = {"show": "show", "bring back": "show", "unhide": "show", "turn on": "show",
          "hide": "hide", "remove": "hide", "turn off": "hide"}
_TAILS = {"go away": "hide", "hide": "hide", "come back": "show", "show yourself": "show", "wake up": "show"}


def pet_phrase(text: str, name: str | None = None) -> str | None:
    """A clear, direct command only: "show the pet", "hide Orbi", "Jarvis, bring back the pet", "Orbi, go away".
    "Show me pictures of a pet" is NOT a command."""
    import re
    t = " ".join(re.sub(r"[^\w\s']", " ", (text or "").lower()).split())
    nouns = ["desktop pet", "floating pet", "pet", "orbi"] + ([re.escape(name.lower())] if name else [])
    noun = "(?:" + "|".join(dict.fromkeys(nouns)) + ")"
    lead = r"^(?:(?:hey |ok )?jarvis )?(?:please |can you |could you )?"
    end = r"(?: please| again| now)?$"
    verbs = "(?P<verb>" + "|".join(_VERBS) + ")"
    m = re.match(lead + verbs + r"(?: the| your| my)? " + noun + r"(?: for me)?" + end, t)
    if m:
        return _VERBS[m.group("verb")]
    tails = "(?P<tail>" + "|".join(_TAILS) + ")"
    m = re.match(lead + r"(?:the |my |your )?" + noun + " " + tails + end, t)
    if m:
        return _TAILS[m.group("tail")]
    return None


# ── thread-safe commands (voice, hotkey and messages come from other threads) ─
class _Bridge(QObject):
    cmd = pyqtSignal(str, object)


_bridge: _Bridge | None = None


def request(action: str, payload=None) -> bool:
    """Ask the pet to do something from any thread: "show", "hide", "toggle", "say", "settings"."""
    if _bridge is None:
        return False
    _bridge.cmd.emit(action, payload)
    return True


# ── hover card and speech bubble ────────────────────────────────────────────
class _Popup(QWidget):
    """A small card next to the pet: "bubble" (white, a message) or "card" (dark, what Jarvis is doing)."""

    def __init__(self):
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.kind, self.title, self.text, self.accent, self.busy = "card", "", "", "#ffaa00", False
        self._spin = QTimer(self)
        self._spin.timeout.connect(self.update)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

    def _fonts(self):
        ft = QFont("Segoe UI", 1)
        ft.setPixelSize(12)
        ft.setBold(True)
        fb = QFont("Segoe UI", 1)
        fb.setPixelSize(13)
        return ft, fb

    def pop(self, kind: str, title: str, text: str, anchor: QWidget, *, accent: str = "#ffaa00",
            busy: bool = False, seconds: float = 0.0):
        self.kind, self.title, self.text, self.accent, self.busy = kind, title, text[:160], accent, busy
        ft, fb = self._fonts()
        width = 240
        tr = QFontMetrics(fb).boundingRect(QRect(0, 0, width - 28 - (26 if busy else 0), 400),
                                           int(Qt.TextFlag.TextWordWrap), self.text)
        h = 14 + (18 if title else 0) + tr.height() + 14 + (10 if kind == "bubble" else 0)
        self.setFixedSize(width, max(46, h))
        scr = anchor.screen() or QApplication.primaryScreen()
        geo = scr.availableGeometry()
        a = anchor.frameGeometry()
        x = a.left() + a.width() // 2 - width + 30
        y = a.top() - self.height() + int(a.height() * 0.22)
        if y < geo.top():                                   # no room above: go beside it
            y = a.top() + 10
            x = a.left() - width - 4 if a.left() - width - 4 > geo.left() else a.right() + 4
        self.move(max(geo.left() + 4, min(x, geo.right() - width - 4)), max(geo.top() + 4, y))
        if busy:
            self._spin.start(40)
        else:
            self._spin.stop()
        self.show()
        self.raise_()
        self.update()
        if seconds:
            self._hide_timer.start(int(seconds * 1000))
        else:
            self._hide_timer.stop()

    def hideEvent(self, e):
        self._spin.stop()
        super().hideEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(2, 2, -2, -12 if self.kind == "bubble" else -2)
        path = QPainterPath()
        path.addRoundedRect(r, 14, 14)
        if self.kind == "bubble":                           # tail pointing down-right to the pet
            tail = QPainterPath(QPointF(r.right() - 58, r.bottom() - 2))
            tail.lineTo(QPointF(r.right() - 30, r.bottom() + 10))
            tail.lineTo(QPointF(r.right() - 38, r.bottom() - 2))
            path = path.united(tail)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(_a("#000000", 40))
            p.drawPath(path.translated(1, 2))
            p.setBrush(QColor("#ffffff"))
            p.drawPath(path)
            fg, sub = QColor("#1b1e2e"), QColor("#5a6172")
        else:
            p.setPen(QPen(_a(self.accent, 150), 1.2))
            p.setBrush(QColor(10, 13, 20, 236))
            p.drawPath(path)
            fg, sub = QColor("#e8ecf3"), QColor(self.accent)
        ft, fb = self._fonts()
        x, y = r.left() + 14, r.top() + 11
        if self.title:
            p.setFont(ft)
            p.setPen(sub)
            p.drawText(QRectF(x, y, r.width() - 28, 16), Qt.AlignmentFlag.AlignLeft, self.title)
            y += 18
        p.setFont(fb)
        p.setPen(fg)
        p.drawText(QRectF(x, y, r.width() - 28 - (26 if self.busy else 0), r.bottom() - y - 8),
                   int(Qt.TextFlag.TextWordWrap) | int(Qt.AlignmentFlag.AlignLeft), self.text)
        if self.busy:                                       # a small spinner while Jarvis works
            c = QPointF(r.right() - 22, r.center().y())
            p.setPen(QPen(_a("#ffffff", 40), 2.6))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(c, 8, 8)
            p.setPen(QPen(QColor(self.accent), 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            p.drawArc(QRectF(c.x() - 8, c.y() - 8, 16, 16), int(-time.monotonic() * 400 * 16) % (360 * 16), 100 * 16)
        p.end()


# ── the desktop pet ─────────────────────────────────────────────────────────
class JarvisPet(QWidget):
    single_clicked = pyqtSignal()
    double_clicked = pyqtSignal()
    action_requested = pyqtSignal(str)
    position_changed = pyqtSignal(int, int)

    MENU = [("New Task", "new_task"), ("Voice Mode", "voice_mode"), ("Screen Analyzer", "screen_analyzer"),
            ("Browser Agent", "browser_agent"), ("Settings", "settings"), ("Open Workspace", "open_workspace")]
    SNAP = 28

    def __init__(self, accent=None, size: int | None = None):
        global _bridge
        super().__init__(None)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool
                            | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setAccessibleName("Jarvis pet")
        self._accent_fn = accent or (lambda: "#ffaa00")
        self.cfg = load_settings()
        self.body = int(size or SIZES[self.cfg["size"]])
        self.setFixedSize(int(self.body * 2.1), int(self.body * 2.0))
        self._reduce = windows_reduce_motion()

        self._state, self._status = "idle", "Ready"
        self._t0 = time.monotonic()
        self._voice, self._voice_at = 0.0, 0.0
        self._mic, self._mic_at = 0.0, 0.0
        self._transient: tuple[str, float] | None = None
        self._safety_probe = None
        self._safety_until = 0.0
        self._ambient: str | None = None
        self._locked = False
        self._hovered = False
        self._auto_hidden = False                           # stepped aside for a full-screen app
        self._cursor_last, self._cursor_still = QPoint(), time.monotonic()
        self.popup = _Popup()

        self._frame = QTimer(self)
        self._frame.timeout.connect(self.update)
        self._slow = QTimer(self)                           # rules, lock screen, full screen: every 2 s
        self._slow.timeout.connect(self._refresh_ambient)
        self._slow.start(2000)
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.timeout.connect(self._show_card)
        self._single_timer = QTimer(self)
        self._single_timer.setSingleShot(True)
        self._single_timer.timeout.connect(self.single_clicked.emit)
        self._dragging, self._drag_button = False, None
        self._drag_offset, self._press_pos = QPoint(0, 0), QPoint(0, 0)
        if _bridge is None:
            _bridge = _Bridge()
        _bridge.cmd.connect(self._on_command)
        self._refresh_ambient()
        self._apply_status()

    # settings ------------------------------------------------------------------
    @property
    def name(self) -> str:
        return self.cfg.get("name") or "Orbi"

    def motion(self) -> float:
        if self._reduce:
            return 0.25
        return 0.5 if self.cfg.get("motion") == "calm" else 1.0

    def apply_settings(self, cfg: dict) -> None:
        old_size = self.body
        self.cfg = clean_settings(cfg)
        self.body = SIZES[self.cfg["size"]]
        if self.body != old_size:                           # grow/shrink around the same bottom-right corner
            br = self.frameGeometry().bottomRight()
            self.setFixedSize(int(self.body * 2.1), int(self.body * 2.0))
            self.move(br.x() - self.width() + 1, br.y() - self.height() + 1)
        if not self.cfg["enabled"]:
            self.popup.hide()
            QWidget.hide(self)
        elif not self.isVisible() and not self._auto_hidden:
            x, y = saved_position()
            self.show_at(x, y)
        self._apply_status()

    def _on_command(self, action: str, payload) -> None:
        if action == "show":
            self.apply_settings(save_settings({"enabled": True}))
        elif action == "hide":
            self.apply_settings(save_settings({"enabled": False}))
        elif action == "toggle":
            self.apply_settings(save_settings({"enabled": not (self.cfg["enabled"] and self.isVisible())}))
        elif action == "settings":
            self.apply_settings(save_settings(payload or {}))
        elif action == "say":
            data = payload if isinstance(payload, dict) else {"text": str(payload or "")}
            self.say(data.get("text", ""), title=data.get("title", ""), importance=data.get("importance", "normal"))

    # wiring ------------------------------------------------------------------
    def attach_window(self, win) -> None:
        """Listen to Jarvis's main window: your mic level, Jarvis's voice level, safety holds."""
        for sig, slot in (("_mic_sig", self.set_mic_level), ("_audio_sig", self.set_audio_level),
                          ("_confirm_sig", lambda _m: self.set_safety(True))):
            try:
                getattr(win, sig).connect(slot)
            except Exception:
                pass
        self._safety_probe = lambda: bool(getattr(win, "_confirm_banner", None)
                                          and win._confirm_banner.isVisible())

    def set_state(self, state: str, detail: str | None = None) -> None:
        new = (state or "idle").strip().lower()
        if self._state in WORK_STATES | {"thinking"} and new in ("listening", "idle"):
            self.flash("done", 1.4)                          # finished something: a happy hop
        if new in ("error", "danger"):
            self.flash("error", 2.5)
        self._state = new
        self._status = (detail or "").strip() or {"listening": "Ready", "thinking": "Thinking…",
                                                  "speaking": "Speaking", "muted": "Muted"}.get(new, "Working…"
                                                  if new in WORK_STATES else "Ready")
        self._apply_status()

    def set_audio_level(self, level: float) -> None:
        self._voice, self._voice_at = max(0.0, min(1.0, float(level))), time.monotonic()

    def set_mic_level(self, level: float) -> None:
        self._mic, self._mic_at = max(0.0, min(1.0, float(level))), time.monotonic()

    def set_safety(self, on: bool) -> None:
        self._safety_until = time.monotonic() + (120 if on else 0)
        self._apply_status()

    def flash(self, mood: str, seconds: float) -> None:
        self._transient = (mood, time.monotonic() + seconds)
        self._kick()

    def say(self, text: str, *, title: str = "", importance: str = "normal") -> bool:
        """A short speech bubble, if the owner's rules allow it right now."""
        text = " ".join((text or "").split())
        if not text or not self.cfg["enabled"] or not self.isVisible() or self._auto_hidden:
            return False
        if not bubble_allowed(self._ambient, importance):
            return False
        if self._locked:
            title, text = "", "Jarvis has an update."       # never message content on the lock screen
        if len(text) > 120:
            text = text[:117].rstrip() + "…"
        self.flash("wave", 4.5)                             # wink + wave; the bubble itself is the popup
        self.popup.pop("bubble", title, text, self, accent=self._accent_fn(), seconds=4.5)
        return True

    def show_at(self, x: int | None = None, y: int | None = None) -> None:
        if not self.cfg.get("enabled", True):
            return                                          # the owner hid the pet
        geo = self._screen_geo(QPoint(int(x), int(y)) if x is not None and y is not None else None)
        if x is None or y is None:
            x, y = geo.right() - self.width() - 12, geo.bottom() - self.height() - 70
        x = max(geo.left(), min(int(x), geo.right() - self.width()))
        y = max(geo.top(), min(int(y), geo.bottom() - self.height()))
        self.move(x, y)
        if self._auto_hidden:
            return
        self.show()
        self.raise_()

    def hide(self) -> None:
        self.popup.hide()
        super().hide()

    def _screen_geo(self, at: QPoint | None = None):
        scr = QApplication.screenAt(at) if at is not None else None
        return (scr or self.screen() or QApplication.primaryScreen()).availableGeometry()

    # mood + frame rate ---------------------------------------------------------
    def current_mood(self) -> str:
        now = time.monotonic()
        safety = now < self._safety_until
        if safety and self._safety_probe is not None and now - (self._safety_until - 120) > 1.5:
            safety = self._safety_probe()                    # banner answered → back to normal
            if not safety:
                self._safety_until = 0.0
        transient = self._transient[0] if self._transient and self._transient[1] > now else None
        mic_active = self._mic > 0.12 and now - self._mic_at < 0.6
        return pick_mood(self._state, safety=safety, transient=transient, mic_active=mic_active,
                         ambient=self._ambient)

    def _kick(self) -> None:
        if not self.isVisible() or self._locked:
            self._frame.stop()
            return
        calm = self.current_mood() in ("idle", "focus", "sleep", "silent") and not self._hovered
        if self._reduce:
            interval = 66 if calm else 50
        elif self.cfg.get("motion") == "calm":
            interval = 66 if calm else 40
        else:
            interval = 50 if calm else 33
        if not self._frame.isActive() or self._frame.interval() != interval:
            self._frame.start(interval)

    def _refresh_ambient(self) -> None:
        self._ambient = ambient_mood()
        try:
            from core.notify import is_session_locked
            self._locked = bool(is_session_locked())
        except Exception:
            self._locked = False
        busy = fullscreen_busy()
        if busy and self.isVisible() and self.cfg["enabled"]:
            self._auto_hidden = True                        # step aside for games, videos, presentations
            self.popup.hide()
            QWidget.hide(self)
        elif not busy and self._auto_hidden:
            self._auto_hidden = False
            if self.cfg["enabled"]:
                self.show()
        self._apply_status()

    def _status_text(self) -> str:
        if self._locked:
            return "Jarvis"                                  # nothing about your tasks on the lock screen
        mood = self.current_mood()
        if mood == "safety":
            return "Needs your OK"
        if mood == "muted":
            return "Mic is muted"
        if mood in ("working", "thinking", "speaking") or self._status not in ("Ready", ""):
            return self._status
        return MOOD_LABELS.get(mood, "Ready")

    def _apply_status(self) -> None:
        text = self._status_text()
        self.setToolTip("")                                 # the hover card replaces the plain tooltip
        self.setAccessibleDescription(f"{self.name}, Jarvis: {text}")
        if self.popup.isVisible() and self.popup.kind == "card":
            self._show_card()
        self._kick()

    def _show_card(self) -> None:
        if not self._hovered or not self.isVisible():
            return
        mood = self.current_mood()
        title = "Jarvis" if self._locked else f"{self.name} · Jarvis"
        self.popup.pop("card", title, self._status_text(), self, accent=self._accent_fn(),
                       busy=mood in ("working", "thinking") and not self._locked)

    def showEvent(self, e):
        self._kick()
        super().showEvent(e)

    def hideEvent(self, e):
        self._frame.stop()
        self.popup.hide()
        super().hideEvent(e)

    def paintEvent(self, _):
        p = QPainter(self)
        c = QPointF(self.width() * 0.42, self.height() * 0.60)
        g = self.mapToGlobal(c.toPoint())
        cur = QCursor.pos()
        now = time.monotonic()
        if cur != self._cursor_last:
            self._cursor_last, self._cursor_still = cur, now
        mood = self.current_mood()
        t = now - self._t0
        if self.cfg.get("motion") == "lively" and not self._reduce and mood == "idle" and now - self._cursor_still > 6:
            look = (math.sin(t * 0.35) * 0.8, math.cos(t * 0.23) * 0.35)   # curious glances when you're away
        else:
            dx, dy = cur.x() - g.x(), cur.y() - g.y()
            d = max(1.0, math.hypot(dx, dy))
            k = min(1.0, d / 300)
            look = (dx / d * k, dy / d * k)
        voice = self._voice if now - self._voice_at < 0.4 else None
        draw_pet(p, c.x(), c.y(), self.body, mood=mood, t=t, look=look, accent=self._accent_fn(), voice=voice,
                 motion=self.motion())
        p.end()
        self._kick()

    # mouse: same behaviour as the old launcher, plus a hop and edge snapping ----
    def _show_menu(self, pos):
        menu = QMenu(self)
        menu.setStyleSheet("QMenu { background: rgba(8,8,8,245); color: #e8ecf3; border: 1px solid "
                           "rgba(255,255,255,40); border-radius: 10px; padding: 6px; }"
                           "QMenu::item { padding: 8px 18px; border-radius: 6px; }"
                           "QMenu::item:selected { background: rgba(255,255,255,0.08); }")
        for i, (label, key) in enumerate(self.MENU):
            act = QAction(label, self)
            act.triggered.connect(lambda _=False, k=key: self.action_requested.emit(k))
            menu.addAction(act)
            if i != len(self.MENU) - 1:
                menu.addSeparator()
        menu.addSeparator()
        hide = QAction(f"Hide {self.name}", self)
        hide.triggered.connect(lambda: self._on_command("hide", None))
        menu.addAction(hide)
        self.popup.hide()
        menu.exec(pos)

    def snapped(self, x: int, y: int) -> tuple[int, int]:
        """Pull the pet onto a screen edge (or the top of the taskbar) when it's dropped close to one."""
        geo = self._screen_geo(QPoint(x + self.width() // 2, y + self.height() // 2))
        if abs(x - geo.left()) < self.SNAP:
            x = geo.left()
        elif abs(geo.right() + 1 - (x + self.width())) < self.SNAP:
            x = geo.right() + 1 - self.width()
        if abs(y - geo.top()) < self.SNAP:
            y = geo.top()
        elif abs(geo.bottom() + 1 - (y + self.height())) < self.SNAP:
            y = geo.bottom() + 1 - self.height()
        return x, y

    def mousePressEvent(self, e):
        if e.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            self._dragging = False
            self._press_pos = e.globalPosition().toPoint()
            self._drag_offset = self._press_pos - self.frameGeometry().topLeft()
            self._single_timer.stop()
            self._drag_button = e.button()
            e.accept()
            return
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._drag_button is not None and e.buttons() & self._drag_button:
            pos = e.globalPosition().toPoint()
            if not self._dragging and (pos - self._press_pos).manhattanLength() > 6:
                self._dragging = True
                self.popup.hide()
            if self._dragging:
                geo = self._screen_geo(pos)                  # works across monitors
                np_ = pos - self._drag_offset
                self.move(max(geo.left(), min(np_.x(), geo.right() - self.width())),
                          max(geo.top(), min(np_.y(), geo.bottom() - self.height())))
            e.accept()
            return
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            if self._dragging:
                x, y = self.snapped(self.x(), self.y())
                self.move(x, y)
                self.position_changed.emit(x, y)
            else:
                self.flash("done", 1.0)                      # a giggle hop
                self._single_timer.start(180)
        elif e.button() == Qt.MouseButton.RightButton and not self._dragging:
            self._show_menu(e.globalPosition().toPoint())
        self._dragging, self._drag_button = False, None
        super().mouseReleaseEvent(e)

    def mouseDoubleClickEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._single_timer.stop()
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(e)

    def enterEvent(self, e):
        self._hovered = True
        self._hover_timer.start(450)
        self._kick()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self._hover_timer.stop()
        if self.popup.kind == "card":
            self.popup.hide()
        self._kick()
        super().leaveEvent(e)
