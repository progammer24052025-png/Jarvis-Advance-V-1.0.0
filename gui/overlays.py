"""Control panels that open over the HUD: customise, audio devices, memory, confirm, clipboard."""

from __future__ import annotations

import colorsys
import math
import threading

from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QConicalGradient, QFont, QPainter, QPen, QRadialGradient
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QScrollArea, QVBoxLayout,
                             QWidget)

from . import kit

GEMINI_VOICES = [
    ("Charon", "deep, calm (default)"), ("Fenrir", "warm, energetic"), ("Orus", "firm, steady"),
    ("Puck", "bright, upbeat"), ("Kore", "clear, composed"), ("Aoede", "light, breezy"),
    ("Leda", "youthful"), ("Zephyr", "airy, friendly"),
]
PRESETS = ["#ffaa00", "#00d4ff", "#ff2a1f", "#3ddc97", "#b388ff", "#ff6ec7", "#ffd400", "#ffffff"]

_COMBO_CSS = ("QComboBox { background: rgba(255,255,255,10); color: #e8ecf3; border: 1px solid rgba(255,255,255,30);"
              " border-radius: 8px; padding: 6px 10px; min-height: 22px; }"
              "QComboBox QAbstractItemView { background: #10141d; color: #e8ecf3; selection-background-color: #2a3242; }")


class _Overlay(QWidget):
    """Dims the window and shows a glass card in the middle. Esc or × closes it."""
    closed = pyqtSignal()

    def __init__(self, parent: QWidget, title: str, width: int = 560):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("background: rgba(0,0,0,150);")
        self.setGeometry(parent.rect())
        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card = kit.HoloPanel(title, "Esc to close")
        self.card.setFixedWidth(width)
        self.card.setStyleSheet("background: transparent;")
        outer.addWidget(self.card, 0, Qt.AlignmentFlag.AlignCenter)
        self.body = self.card.body
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def open(self):
        self.setGeometry(self.parentWidget().rect())
        self.raise_()
        self.show()
        self.setFocus()

    def keyPressEvent(self, e):
        if e.key() == Qt.Key.Key_Escape:
            self.close_overlay()
        else:
            super().keyPressEvent(e)

    def mousePressEvent(self, e):
        if not self.card.geometry().contains(e.position().toPoint()):
            self.close_overlay()

    def close_overlay(self):
        self.hide()
        self.closed.emit()

    def footer(self, *buttons):
        row = QHBoxLayout()
        row.addStretch(1)
        for b in buttons:
            row.addWidget(b)
        self.body.addLayout(row)


# ── colour wheel ─────────────────────────────────────────────────────────
class HueWheel(QWidget):
    picked = pyqtSignal(str)

    def __init__(self, hex_color: str, parent=None):
        super().__init__(parent)
        self.setFixedSize(190, 190)
        self.color = QColor(hex_color)

    def set_color(self, hex_color: str):
        self.color = QColor(hex_color)
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(self.width() / 2, self.height() / 2)
        g = QConicalGradient(c, 0)
        for i in range(13):
            g.setColorAt(i / 12, QColor.fromHsvF((i / 12) % 1.0, 0.9, 1.0))
        p.setPen(QPen(QBrush(g), 22))
        p.drawEllipse(c, 80, 80)
        # selection marker
        h = self.color.hsvHueF()
        h = 0 if h < 0 else h
        ang = h * 2 * math.pi
        mx, my = c.x() + math.cos(ang) * 80, c.y() - math.sin(ang) * 80
        p.setPen(QPen(QColor("white"), 3))
        p.setBrush(self.color)
        p.drawEllipse(QPointF(mx, my), 10, 10)
        rg = QRadialGradient(c, 52)
        rg.setColorAt(0, self.color)
        rg.setColorAt(1, kit.alpha(self.color, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(rg))
        p.drawEllipse(c, 52, 52)

    def _pick(self, pos):
        c = QPointF(self.width() / 2, self.height() / 2)
        dx, dy = pos.x() - c.x(), c.y() - pos.y()
        if math.hypot(dx, dy) < 50:
            return
        hue = (math.atan2(dy, dx) / (2 * math.pi)) % 1.0
        r, g, b = colorsys.hsv_to_rgb(hue, 0.9, 1.0)
        self.color = QColor(int(r * 255), int(g * 255), int(b * 255))
        self.update()
        self.picked.emit(self.color.name())

    def mousePressEvent(self, e):
        self._pick(e.position())

    def mouseMoveEvent(self, e):
        self._pick(e.position())


class CustomizeOverlay(_Overlay):
    """Colour (live), names, voice and push-to-talk."""
    preview_color = pyqtSignal(str)
    applied = pyqtSignal(dict)

    def __init__(self, parent, current: dict):
        super().__init__(parent, "Customise", 640)
        self._orig_color = current.get("color", kit.accent_hex())
        top = QHBoxLayout()
        top.setSpacing(18)
        self.wheel = HueWheel(self._orig_color)
        self.wheel.picked.connect(self._on_color)
        top.addWidget(self.wheel)

        right = QVBoxLayout()
        right.addWidget(kit.label("Colour", 8, kit.MUTED, QFont.Weight.DemiBold))
        sw = QGridLayout()
        for i, hx in enumerate(PRESETS):
            b = kit.HudButton("", "muted")
            b.setFixedSize(34, 26)
            b.setStyleSheet(f"QPushButton {{ background: {hx}; border: 1px solid rgba(255,255,255,60); border-radius: 6px; }}")
            b.clicked.connect(lambda _=False, h=hx: (self.wheel.set_color(h), self._on_color(h)))
            sw.addWidget(b, i // 4, i % 4)
        right.addLayout(sw)
        self.hex_in = kit.HudInput("#ffaa00")
        self.hex_in.setText(self._orig_color)
        self.hex_in.editingFinished.connect(lambda: self._on_color(self.hex_in.text().strip(), from_text=True))
        right.addWidget(self.hex_in)
        right.addStretch(1)
        top.addLayout(right, 1)
        self.body.addLayout(top)

        form = QGridLayout()
        form.setVerticalSpacing(8)
        form.addWidget(kit.label("Assistant name", 8.5, kit.MUTED), 0, 0)
        self.asst = kit.HudInput("Jarvis")
        self.asst.setText(current.get("assistant", "Jarvis"))
        form.addWidget(self.asst, 0, 1)
        form.addWidget(kit.label("Your name", 8.5, kit.MUTED), 1, 0)
        self.owner = kit.HudInput("Your name")
        self.owner.setText(current.get("owner", ""))
        form.addWidget(self.owner, 1, 1)
        form.addWidget(kit.label("Voice", 8.5, kit.MUTED), 2, 0)
        self.voice = QComboBox()
        self.voice.setStyleSheet(_COMBO_CSS)
        for name, desc in GEMINI_VOICES:
            self.voice.addItem(f"{name}  ·  {desc}", name)
        idx = max(0, self.voice.findData(current.get("voice", "Charon")))
        self.voice.setCurrentIndex(idx)
        form.addWidget(self.voice, 2, 1)
        self.ptt = QCheckBox("Push-to-talk: mic only listens while I hold Ctrl+Space")
        self.ptt.setStyleSheet("QCheckBox { color: #c9d0dc; } QCheckBox::indicator { width: 16px; height: 16px; }")
        self.ptt.setChecked(bool(current.get("push_to_talk")))
        form.addWidget(self.ptt, 3, 0, 1, 2)
        self.body.addLayout(form)
        self.body.addWidget(kit.label("Colour changes show instantly. A new voice reconnects Jarvis for a moment.", 8, kit.MUTED))

        uninstall_row = QHBoxLayout()
        self.uninstall_btn = kit.HudButton("Uninstall Jarvis…", "bad")
        self.uninstall_btn.setToolTip("Opens the uninstaller: you choose what goes and see the full list first.")
        self.uninstall_btn.clicked.connect(self._open_uninstaller)
        uninstall_row.addWidget(self.uninstall_btn)
        uninstall_row.addStretch(1)
        self.body.addLayout(uninstall_row)

        cancel = kit.HudButton("Cancel", "muted")
        cancel.clicked.connect(self._cancel)
        save = kit.HudButton("Save && apply", "accent")
        save.clicked.connect(self._save)
        self.footer(cancel, save)
        self._color = self._orig_color

    def _open_uninstaller(self):
        from core.uninstall import launch_window
        if launch_window():
            self._cancel()
        else:
            self.uninstall_btn.setText("Uninstaller not found: use Settings → Apps")
            self.uninstall_btn.setEnabled(False)

    def _on_color(self, hx: str, from_text: bool = False):
        c = QColor(hx)
        if not c.isValid():
            return
        self._color = c.name()
        if not from_text:
            self.hex_in.setText(self._color)
        else:
            self.wheel.set_color(self._color)
        self.preview_color.emit(self._color)

    def _cancel(self):
        self.preview_color.emit(self._orig_color)
        self.close_overlay()

    def _save(self):
        self.applied.emit({"color": self._color, "assistant": self.asst.text().strip() or "Jarvis",
                           "owner": self.owner.text().strip(), "voice": self.voice.currentData(),
                           "push_to_talk": self.ptt.isChecked()})
        self._orig_color = self._color
        self.close_overlay()


# ── audio devices ────────────────────────────────────────────────────────
def list_audio_devices() -> tuple[list[tuple[int, str]], list[tuple[int, str]]]:
    """Return (inputs, outputs) as (index, name), preferring the WASAPI list Windows itself shows."""
    import sounddevice as sd
    devices = sd.query_devices()
    apis = sd.query_hostapis()
    wasapi = next((i for i, a in enumerate(apis) if "WASAPI" in a["name"]), None)
    pref = wasapi if wasapi is not None else next((i for i, a in enumerate(apis) if a["name"] == "MME"), 0)
    ins, outs, seen_i, seen_o = [], [], set(), set()
    for idx, d in enumerate(devices):
        if d["hostapi"] != pref:
            continue
        name = d["name"]
        if d["max_input_channels"] > 0 and name not in seen_i:
            ins.append((idx, name)); seen_i.add(name)
        if d["max_output_channels"] > 0 and name not in seen_o:
            outs.append((idx, name)); seen_o.add(name)
    return ins, outs


class _LevelMeter(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.level = 0.0
        self.setFixedHeight(14)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect()).adjusted(0, 2, 0, -2)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255, 18))
        p.drawRoundedRect(r, 5, 5)
        w = r.width() * min(1.0, self.level)
        col = QColor(kit.GOOD) if self.level < 0.8 else QColor(kit.WARN)
        p.setBrush(col)
        p.drawRoundedRect(QRectF(r.left(), r.top(), w, r.height()), 5, 5)


class AudioOverlay(_Overlay):
    applied = pyqtSignal(dict)

    def __init__(self, parent, current: dict):
        super().__init__(parent, "Audio devices", 600)
        try:
            self._ins, self._outs = list_audio_devices()
        except Exception as exc:
            self._ins, self._outs = [], []
            self.body.addWidget(kit.label(f"Couldn't list audio devices: {exc}", 9, kit.BAD))
        self.inp = QComboBox()
        self.out = QComboBox()
        for cb, items, key in ((self.inp, self._ins, "input_device"), (self.out, self._outs, "output_device")):
            cb.setStyleSheet(_COMBO_CSS)
            cb.addItem("System default", None)
            for idx, name in items:
                cb.addItem(name, name)
            cur = current.get(key)
            if cur:
                i = cb.findData(cur)
                if i >= 0:
                    cb.setCurrentIndex(i)
        self.body.addWidget(kit.label("Microphone", 8.5, kit.MUTED))
        self.body.addWidget(self.inp)
        self.meter = _LevelMeter()
        mrow = QHBoxLayout()
        self.test_btn = kit.HudButton("Test mic (3 s)", "good")
        self.test_btn.clicked.connect(self._test_mic)
        mrow.addWidget(self.test_btn)
        mrow.addWidget(self.meter, 1)
        self.body.addLayout(mrow)
        self.mic_note = kit.label("Press Test and speak: the bar should move.", 8, kit.MUTED)
        self.body.addWidget(self.mic_note)
        self.body.addWidget(kit.label("Speakers", 8.5, kit.MUTED))
        self.body.addWidget(self.out)
        beep = kit.HudButton("Play test tone", "muted")
        beep.clicked.connect(self._test_speaker)
        self.body.addWidget(beep)
        self.body.addWidget(kit.label("Saving reconnects Jarvis's audio for a moment.", 8, kit.MUTED))
        cancel = kit.HudButton("Cancel", "muted")
        cancel.clicked.connect(self.close_overlay)
        save = kit.HudButton("Save", "accent")
        save.clicked.connect(lambda: (self.applied.emit({"input_device": self.inp.currentData(),
                                                        "output_device": self.out.currentData()}),
                                      self.close_overlay()))
        self.footer(cancel, save)
        self._peak = 0.0
        self._ui_t = QTimer(self)
        self._ui_t.timeout.connect(self._refresh_meter)

    @staticmethod
    def _resolve(name, kind):
        if not name:
            return None
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if d["name"] == name and d[f"max_{kind}_channels"] > 0:
                return i
        return None

    def _refresh_meter(self):
        self.meter.level = self._peak
        self._peak *= 0.85
        self.meter.update()

    def _test_mic(self):
        import numpy as np
        import sounddevice as sd
        dev = self._resolve(self.inp.currentData(), "input")
        self.test_btn.setEnabled(False)
        self.mic_note.setText("Listening… say something.")
        heard = {"max": 0.0}

        def run():
            try:
                def cb(indata, frames, t, status):
                    lvl = float(np.sqrt(np.mean(np.square(indata.astype(np.float32))))) / 3000.0
                    self._peak = max(self._peak, min(1.0, lvl))
                    heard["max"] = max(heard["max"], lvl)
                with sd.InputStream(device=dev, channels=1, samplerate=16000, dtype="int16", callback=cb):
                    sd.sleep(3000)
                msg = "✓ I can hear you." if heard["max"] > 0.05 else "I heard almost nothing. Check the mic or pick another one."
            except Exception as exc:
                msg = f"That microphone didn't work: {str(exc)[:80]}"
            QTimer.singleShot(0, lambda: (self.mic_note.setText(msg), self.test_btn.setEnabled(True), self._ui_t.stop()))

        self._ui_t.start(40)
        threading.Thread(target=run, daemon=True).start()

    def _test_speaker(self):
        import numpy as np
        import sounddevice as sd
        dev = self._resolve(self.out.currentData(), "output")
        t = np.linspace(0, 0.6, int(24000 * 0.6), False)
        tone = (0.2 * np.sin(2 * np.pi * 660 * t) * np.minimum(1, (0.6 - t) * 8)).astype(np.float32)
        threading.Thread(target=lambda: sd.play(tone, 24000, device=dev), daemon=True).start()


# ── memory viewer ────────────────────────────────────────────────────────
class MemoryOverlay(_Overlay):
    def __init__(self, parent):
        super().__init__(parent, "Memory · what Jarvis knows about you", 680)
        self.body.addWidget(kit.label("Everything Jarvis has saved about you. Delete anything you don't want it to keep.", 8.5, kit.MUTED))
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setMinimumHeight(360)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.list = QWidget()
        self.list.setStyleSheet("background: transparent;")
        self.list_lay = QVBoxLayout(self.list)
        self.list_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll.setWidget(self.list)
        self.body.addWidget(self.scroll)
        close = kit.HudButton("Done", "accent")
        close.clicked.connect(self.close_overlay)
        self.footer(close)

    def open(self):
        self.reload()
        super().open()

    def reload(self):
        from memory.memory_manager import load_memory
        while self.list_lay.count():
            it = self.list_lay.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        mem = load_memory() or {}
        count = 0
        for cat, entries in mem.items():
            if not isinstance(entries, dict) or not entries:
                continue
            self.list_lay.addWidget(kit.label(cat.upper(), 8, kit.accent_hex(), QFont.Weight.Bold))
            for key, val in entries.items():
                value = val.get("value") if isinstance(val, dict) else val
                when = val.get("updated", "") if isinstance(val, dict) else ""
                row = QHBoxLayout()
                txt = kit.label(f"<b>{key.replace('_', ' ')}</b>: {value}  <span style='color:{kit.MUTED}'>{when}</span>", 9, kit.TEXT)
                txt.setTextFormat(Qt.TextFormat.RichText)
                row.addWidget(txt, 1)
                d = kit.HudButton("Delete", "bad")
                d.setMinimumHeight(28)
                d.clicked.connect(lambda _=False, c=cat, k=key: self._delete(c, k))
                row.addWidget(d)
                holder = QWidget()
                holder.setLayout(row)
                self.list_lay.addWidget(holder)
                count += 1
        if not count:
            self.list_lay.addWidget(kit.label("Nothing saved yet.", 9, kit.MUTED))
        self.card.set_tag(f"{count} facts")

    def _delete(self, cat: str, key: str):
        from memory.memory_manager import load_memory, save_memory
        mem = load_memory() or {}
        if isinstance(mem.get(cat), dict) and key in mem[cat]:
            del mem[cat][key]
            save_memory(mem)
        self.reload()


# ── confirm banner ───────────────────────────────────────────────────────
class ConfirmBanner(QWidget):
    """Top-of-window banner for a Guardian safety hold: your click counts as your confirmation."""
    decided = pyqtSignal(bool)

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("ConfirmBanner")
        self.setStyleSheet("#ConfirmBanner { background: rgba(40,6,6,235); border: 1px solid #ff4d4d; border-radius: 12px; }")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(16, 10, 12, 10)
        self.text = kit.label("", 10, "#ffd9d4", QFont.Weight.DemiBold)
        self.text.setStyleSheet("color: #ffd9d4; background: transparent; border: none;")
        lay.addWidget(self.text, 1)
        no = kit.HudButton("Cancel", "muted")
        yes = kit.HudButton("Confirm", "bad")
        no.clicked.connect(lambda: self._decide(False))
        yes.clicked.connect(lambda: self._decide(True))
        lay.addWidget(no)
        lay.addWidget(yes)
        self.hide()
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.timeout.connect(self.hide)

    def ask(self, message: str):
        self.text.setText("🛡  " + message)
        pw = self.parentWidget().width()
        w = min(860, pw - 40)
        self.setGeometry((pw - w) // 2, 70, w, 64)
        self.raise_()
        self.show()
        self._timeout.start(120_000)  # the Guardian forgets a hold after 2 minutes

    def _decide(self, ok: bool):
        self.hide()
        self.decided.emit(ok)


# ── clipboard panel ──────────────────────────────────────────────────────
class ClipboardPanel(QWidget):
    """Appears quietly when you copy text. Nothing happens unless you click."""
    action = pyqtSignal(str, str)   # verb, text

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("ClipboardPanel")
        self.setStyleSheet("#ClipboardPanel { background: rgb(12,15,22); border: 1px solid rgba(255,255,255,40); border-radius: 12px; }")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        head = QHBoxLayout()
        head.addWidget(kit.label("CLIPBOARD", 8, kit.accent_hex(), QFont.Weight.Bold), 1)
        x = kit.HudButton("×", "muted")
        x.setFixedSize(28, 24)
        x.clicked.connect(self.hide)
        head.addWidget(x)
        lay.addLayout(head)
        self.preview = kit.label("", 8.5, "#c9d0dc")
        self.preview.setStyleSheet("color: #c9d0dc; background: transparent; border: none;")
        lay.addWidget(self.preview)
        row = QHBoxLayout()
        for verb in ("Translate", "Summarise", "Explain", "Fix"):
            b = kit.HudButton(verb, "accent")
            b.setMinimumHeight(28)
            b.clicked.connect(lambda _=False, v=verb: (self.action.emit(v.lower(), self._text), self.hide()))
            row.addWidget(b)
        lay.addLayout(row)
        self._text = ""
        self._hide_t = QTimer(self)
        self._hide_t.setSingleShot(True)
        self._hide_t.timeout.connect(self.hide)
        self.hide()

    def offer(self, text: str):
        text = (text or "").strip()
        if len(text) < 4:
            return
        self._text = text[:6000]
        snippet = text.replace("\n", " ")
        self.preview.setText(snippet[:140] + ("…" if len(snippet) > 140 else ""))
        pw, ph = self.parentWidget().width(), self.parentWidget().height()
        self.setGeometry(pw - 420, ph - 380, 400, 140)  # above the command box, not over it
        self.raise_()
        self.show()
        self._hide_t.start(12_000)
