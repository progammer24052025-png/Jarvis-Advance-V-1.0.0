"""Jarvis's icons, drawn from Orbi (gui/pet.py) so every icon always matches the pet.

  render(size)            the pet on a transparent square (big sizes: full detail; ≤ 48 px: simple face)
  app_icon()              multi-size QIcon for windows, taskbar and tray (built in memory, no files needed)
  tray_icon(state)        the tray icon with a small badge: busy, muted or alert
  tile(size, round=...)   the pet on a dark rounded square / circle (Android launcher, social image)
  write_ico(path, sizes)  a real multi-size Windows .ico (PNG entries), used by tools/make_icons.py
"""

from __future__ import annotations

import struct
from pathlib import Path

from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QIcon, QImage, QPainter, QPainterPath, QPen, QPixmap

from .pet import INK, draw_pet

DEFAULT_ACCENT = "#ffaa00"
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)
TRAY_STATES = ("idle", "busy", "muted", "alert")
_cache: dict = {}


def _blank(size: int) -> QImage:
    img = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor(0, 0, 0, 0))
    return img


def render(size: int, *, mood: str = "idle", accent: str = DEFAULT_ACCENT) -> QImage:
    """Orbi filling a size×size transparent square, eyes open, looking at you."""
    img = _blank(size)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    if size <= 48:                       # simple face: body + big eyes, as large as possible
        draw_pet(p, size / 2, size / 2 + size * 0.02, size * 1.14, mood=mood, t=0.6, look=(0, 0.15),
                 accent=accent, motion=0, icon=True, simple=True)
    else:                                # full pet with antenna, cheeks, hands (no glow: it'd be cut off)
        draw_pet(p, size / 2, size * 0.56, size * 0.80, mood=mood, t=0.6, look=(0, 0.15), accent=accent,
                 motion=0, icon=True, simple=False)
    p.end()
    return img


def tile(size: int, *, round_shape: bool = False, accent: str = DEFAULT_ACCENT, bg: str = "#0b0f17") -> QImage:
    """The pet on a dark tile: Android launcher icons, social preview."""
    img = _blank(size)
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(QColor(bg))
    r = QRectF(0, 0, size, size)
    if round_shape:
        p.drawEllipse(r)
    else:
        p.drawRoundedRect(r, size * 0.22, size * 0.22)
    inner = render(int(size * 0.86), accent=accent)
    p.drawImage(QPointF(size * 0.07, size * 0.07), inner)
    p.end()
    return img


def _badge(p: QPainter, size: int, state: str, accent: str) -> None:
    r = size * 0.23
    c = QPointF(size - r - 0.5, size - r - 0.5)
    p.setPen(QPen(QColor("#0b0f17"), max(1.0, size * 0.06)))
    if state == "muted":
        p.setBrush(QColor("#ff4d6d"))
        p.drawEllipse(c, r, r)
        p.setPen(QPen(QColor("#ffffff"), max(1.2, size * 0.07), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        d = r * 0.5
        p.drawLine(c + QPointF(-d, -d), c + QPointF(d, d))
    elif state == "alert":
        p.setBrush(QColor("#ffd23f"))
        p.drawEllipse(c, r, r)
        p.setPen(QPen(INK, max(1.2, size * 0.075), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        p.drawLine(c + QPointF(0, -r * 0.5), c + QPointF(0, r * 0.12))
        p.drawPoint(c + QPointF(0, r * 0.5))
    elif state == "busy":
        p.setBrush(QColor("#0b0f17"))
        p.drawEllipse(c, r, r)
        p.setPen(QPen(QColor(accent).lighter(130), max(1.2, size * 0.07), Qt.PenStyle.SolidLine,
                      Qt.PenCapStyle.RoundCap))
        p.setBrush(Qt.BrushStyle.NoBrush)
        rr = r * 0.55
        p.drawArc(QRectF(c.x() - rr, c.y() - rr, 2 * rr, 2 * rr), 90 * 16, -270 * 16)


def tray_image(size: int, state: str = "idle", accent: str = DEFAULT_ACCENT) -> QImage:
    img = render(size, accent=accent)
    if state in ("busy", "muted", "alert"):
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        _badge(p, size, state, accent)
        p.end()
    return img


def app_icon(accent: str = DEFAULT_ACCENT) -> QIcon:
    key = ("app", accent)
    if key not in _cache:
        icon = QIcon()
        for s in ICO_SIZES:
            icon.addPixmap(QPixmap.fromImage(render(s, accent=accent)))
        _cache[key] = icon
    return _cache[key]


def tray_state(jarvis_state: str) -> str:
    s = (jarvis_state or "").strip().lower()
    if s == "muted":
        return "muted"
    if s in ("error", "danger"):
        return "alert"
    if s in ("thinking", "processing", "executing", "working", "running"):
        return "busy"
    return "idle"


def tray_icon(state: str = "idle", accent: str = DEFAULT_ACCENT) -> QIcon:
    key = ("tray", state, accent)
    if key not in _cache:
        icon = QIcon()
        for s in (16, 20, 24, 32, 40, 48, 64):
            icon.addPixmap(QPixmap.fromImage(tray_image(s, state, accent)))
        _cache[key] = icon
    return _cache[key]


def png_bytes(img: QImage) -> bytes:
    ba = QByteArray()
    buf = QBuffer(ba)
    buf.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buf, "PNG")
    buf.close()
    return bytes(ba)


def write_ico(path: Path, sizes=ICO_SIZES, accent: str = DEFAULT_ACCENT) -> Path:
    """A Windows .ico with one PNG image per size (Windows Vista+ format)."""
    pngs = [(s, png_bytes(render(s, accent=accent))) for s in sizes]
    header = struct.pack("<HHH", 0, 1, len(pngs))
    offset = 6 + 16 * len(pngs)
    entries, blobs = b"", b""
    for s, data in pngs:
        dim = 0 if s >= 256 else s
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        blobs += data
        offset += len(data)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(header + entries + blobs)
    return path


def social_image(width: int = 1280, height: int = 640, accent: str = DEFAULT_ACCENT) -> QImage:
    """GitHub social preview: Orbi + the name on Jarvis's dark background."""
    img = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    img.fill(QColor("#0b0f17"))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    draw_pet(p, width * 0.30, height * 0.54, height * 0.62, mood="idle", t=0.6, look=(0.4, 0.1), accent=accent,
             motion=0)
    f = QFont("Segoe UI", 1)
    f.setPixelSize(int(height * 0.16))
    f.setBold(True)
    p.setFont(f)
    p.setPen(QColor("#e8ecf3"))
    p.drawText(QRectF(width * 0.52, height * 0.28, width * 0.46, height * 0.2), Qt.AlignmentFlag.AlignLeft
               | Qt.AlignmentFlag.AlignVCenter, "Jarvis")
    f.setPixelSize(int(height * 0.055))
    f.setBold(False)
    p.setFont(f)
    p.setPen(QColor("#8a93a6"))
    p.drawText(QRectF(width * 0.52, height * 0.50, width * 0.45, height * 0.22),
               int(Qt.TextFlag.TextWordWrap) | int(Qt.AlignmentFlag.AlignLeft),
               "A personal AI assistant for Windows, with Orbi, your desktop pet")
    p.end()
    return img
