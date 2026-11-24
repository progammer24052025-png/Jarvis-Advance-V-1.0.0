"""The command-center dashboard: glass panels laid out around the living orb.

The centre column is left transparent on purpose: the WebGL orb behind the
window shows through it, so the orb stays the centrepiece.
"""

from __future__ import annotations

from core.secrets import TELEGRAM_FILE, protect  # keys/tokens are encrypted at rest (DPAPI)
from core.paths import DATA_DIR  # owner data lives apart from the program
import json
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (QGridLayout, QHBoxLayout, QLabel, QSizePolicy, QTextBrowser, QVBoxLayout,
                             QWidget)

from . import kit

BASE_DIR = Path(__file__).resolve().parent.parent
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _read_json(path: Path, default):
    try:
        return json.loads(protect(path).read_text(encoding="utf-8-sig")) if path.name.endswith(("api_keys.json", "telegram_bot.json", "discord_bot.json")) else json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return default


class _Metrics:
    """CPU / memory / disk / GPU / network sampling (GPU via nvidia-smi when present)."""

    def __init__(self):
        self.gpu: float | None = None
        self._gpu_ok = True
        self._last_net = None
        self._last_t = None
        threading.Thread(target=self._gpu_loop, daemon=True, name="hud-gpu").start()

    def _gpu_loop(self):
        while self._gpu_ok:
            try:
                out = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu", "--format=csv,noheader,nounits"],
                                     capture_output=True, text=True, timeout=4, creationflags=_NOWIN).stdout
                self.gpu = float(out.strip().splitlines()[0])
            except Exception:
                self._gpu_ok = False
                self.gpu = None
                return
            time.sleep(4)

    def sample(self) -> dict:
        import psutil
        now = time.time()
        net = psutil.net_io_counters()
        down = up = 0.0
        if self._last_net is not None:
            dt = max(0.5, now - self._last_t)
            down = (net.bytes_recv - self._last_net.bytes_recv) / dt
            up = (net.bytes_sent - self._last_net.bytes_sent) / dt
        self._last_net, self._last_t = net, now
        try:
            disk = psutil.disk_usage(str(Path.home().anchor or "C:\\")).percent
        except Exception:
            disk = None
        batt = None
        try:
            b = psutil.sensors_battery()
            batt = b.percent if b else None
        except Exception:
            pass
        return {"cpu": psutil.cpu_percent(interval=None), "mem": psutil.virtual_memory().percent,
                "disk": disk, "gpu": self.gpu, "battery": batt, "down": down, "up": up,
                "procs": len(psutil.pids())}


class CommandCenterPage(QWidget):
    command_submitted = pyqtSignal(str)
    mic_clicked = pyqtSignal()
    interrupt_clicked = pyqtSignal()
    attach_clicked = pyqtSignal()
    nav_requested = pyqtSignal(str)   # chat | settings | customise | audio | memory | fullscreen

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setStyleSheet("background: transparent;")
        self._started = time.time()
        self._muted = False
        self._speaking = False
        self._metrics = _Metrics()

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 10, 16, 8)
        root.setSpacing(10)
        root.addWidget(self._build_header())

        body = QHBoxLayout()
        body.setSpacing(14)
        body.addWidget(self._build_left(), 0)
        body.addLayout(self._build_center(), 1)
        body.addWidget(self._build_right(), 0)
        root.addLayout(body, 1)
        root.addWidget(self._build_footer())

        self._clock_t = QTimer(self)
        self._clock_t.timeout.connect(self._tick_clock)
        self._clock_t.start(1000)
        self._tick_clock()
        self._metric_t = QTimer(self)
        self._metric_t.timeout.connect(self._tick_metrics)
        self._metric_t.start(2000)
        self._ops_t = QTimer(self)
        self._ops_t.timeout.connect(self._tick_ops)
        self._ops_t.start(5000)
        QTimer.singleShot(300, self._tick_metrics)
        QTimer.singleShot(600, self._tick_ops)

    # ── header ───────────────────────────────────────────────────────────
    def _build_header(self) -> QWidget:
        w = QWidget()
        w.setFixedHeight(52)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 0, 4, 0)
        lay.setSpacing(12)
        lay.addWidget(kit.ArcLogo())
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        self._title = kit.label("J.A.R.V.I.S", 14, kit.TEXT, QFont.Weight.Bold)
        self._title.setFont(kit.font(14, QFont.Weight.Bold, spacing=4))
        self._subtitle = kit.label("PERSONAL AI · OWNER-ONLY", 7.5, kit.MUTED, QFont.Weight.DemiBold)
        self._subtitle.setFont(kit.font(7.5, QFont.Weight.DemiBold, spacing=2))
        title_col.addWidget(self._title)
        title_col.addWidget(self._subtitle)
        lay.addLayout(title_col)
        self.state_chip = kit.StateChip()
        lay.addWidget(self.state_chip)
        lay.addStretch(1)
        for key, text in (("chat", "Chat"), ("customise", "Customise"), ("audio", "Audio"),
                          ("memory", "Memory"), ("settings", "Settings")):
            b = kit.HudButton(text, "muted")
            b.setMinimumHeight(30)
            b.clicked.connect(lambda _=False, k=key: self.nav_requested.emit(k))
            lay.addWidget(b)
        clock_col = QVBoxLayout()
        clock_col.setSpacing(0)
        self._clock = kit.label("00:00:00", 18, kit.TEXT, QFont.Weight.Light, mono=True)
        self._clock.setAlignment(Qt.AlignmentFlag.AlignRight)
        self._date = kit.label("", 7.5, kit.MUTED, QFont.Weight.DemiBold)
        self._date.setFont(kit.font(7.5, QFont.Weight.DemiBold, spacing=1.6))
        self._date.setAlignment(Qt.AlignmentFlag.AlignRight)
        clock_col.addWidget(self._clock)
        clock_col.addWidget(self._date)
        lay.addSpacing(8)
        lay.addLayout(clock_col)
        fs = kit.HudButton("⛶", "muted")
        fs.setFixedWidth(40)
        fs.setToolTip("Fullscreen")
        fs.clicked.connect(lambda: self.nav_requested.emit("fullscreen"))
        lay.addWidget(fs)
        return w

    # ── left column ──────────────────────────────────────────────────────
    def _build_left(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(272)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)

        sysp = kit.HoloPanel("Systems", "Live")
        grid = QGridLayout()
        grid.setSpacing(4)
        self.g_cpu, self.g_mem, self.g_disk, self.g_gpu = (kit.RingGauge("CPU"), kit.RingGauge("Memory"),
                                                           kit.RingGauge("Disk"), kit.RingGauge("GPU"))
        for i, g in enumerate((self.g_cpu, self.g_mem, self.g_disk, self.g_gpu)):
            g.setMinimumSize(100, 92)
            grid.addWidget(g, i // 2, i % 2)
        sysp.body.addLayout(grid)
        lay.addWidget(sysp)

        self.net_panel = kit.HoloPanel("Network", "--")
        self.spark = kit.Sparkline()
        self.net_panel.body.addWidget(self.spark)
        lay.addWidget(self.net_panel)

        st = kit.HoloPanel("Status", "")
        self.r_uptime = kit.DataRow("Uptime", "--", "good")
        self.r_procs = kit.DataRow("Processes", "--")
        self.r_engine = kit.DataRow("Engine", "Gemini Live", "accent")
        self.r_voice = kit.DataRow("Voice", "--")
        self.r_guard = kit.DataRow("Guardian", "Armed", "good")
        self.r_tele = kit.DataRow("Telegram", "--")
        self.r_sec = kit.DataRow("Security", "--")
        for r in (self.r_uptime, self.r_procs, self.r_engine, self.r_voice, self.r_guard, self.r_tele, self.r_sec):
            st.body.addWidget(r)
        lay.addWidget(st)

        self.ops = kit.HoloPanel("Operations", "Routines · Skills")
        self.ops_text = QLabel("")
        self.ops_text.setWordWrap(True)
        self.ops_text.setTextFormat(Qt.TextFormat.RichText)
        self.ops_text.setFont(kit.font(8.5))
        self.ops_text.setStyleSheet("color: #c9d0dc; background: transparent;")
        self.ops_text.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        self.ops.body.addWidget(self.ops_text, 1)
        lay.addWidget(self.ops, 1)
        return w

    # ── centre: the orb shows through ────────────────────────────────────
    def _build_center(self):
        col = QVBoxLayout()
        col.setSpacing(6)
        spacer = QWidget()
        spacer.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        spacer.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        col.addWidget(spacer, 1)

        self.state_line = kit.label("Listening", 11, kit.TEXT, QFont.Weight.Light)
        self.state_line.setFont(kit.font(11, QFont.Weight.Light, spacing=3))
        self.state_line.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        col.addWidget(self.state_line)
        self.wave = kit.Waveform()
        col.addWidget(self.wave)

        self.content = kit.HoloPanel("Content", "")
        self.content.setMaximumHeight(230)
        head = QHBoxLayout()
        self.content_title = kit.label("", 10, kit.TEXT, QFont.Weight.DemiBold)
        head.addWidget(self.content_title, 1)
        close = kit.HudButton("×", "muted")
        close.setFixedSize(30, 26)
        close.clicked.connect(self.content.hide)
        head.addWidget(close)
        self.content.body.addLayout(head)
        self.content_body = QTextBrowser()
        self.content_body.setOpenExternalLinks(True)
        self.content_body.setStyleSheet("QTextBrowser { background: transparent; color: #d7dde8; border: none; }")
        self.content_body.setFont(kit.font(9))
        self.content.body.addWidget(self.content_body)
        self.content.hide()
        col.addWidget(self.content)
        return col

    # ── right column ─────────────────────────────────────────────────────
    def _build_right(self) -> QWidget:
        w = QWidget()
        w.setFixedWidth(360)
        lay = QVBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(10)
        comm = kit.HoloPanel("Communications", "Log")
        self.log = kit.TranscriptLog()
        comm.body.addWidget(self.log)
        lay.addWidget(comm, 1)

        cmd = kit.HoloPanel("Command", "Input")
        self.input = kit.HudInput("Type to Jarvis…  (Enter to send)")
        self.input.returnPressed.connect(self._submit)
        cmd.body.addWidget(self.input)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_mic = kit.HudButton("Mic live", "good")
        self.btn_mic.clicked.connect(self.mic_clicked.emit)
        self.btn_stop = kit.HudButton("Interrupt", "bad")
        self.btn_stop.setToolTip("Stop Jarvis talking right now")
        self.btn_stop.clicked.connect(self.interrupt_clicked.emit)
        attach = kit.HudButton("Attach", "muted")
        attach.clicked.connect(self.attach_clicked.emit)
        send = kit.HudButton("Send", "accent")
        send.clicked.connect(self._submit)
        for b in (self.btn_mic, self.btn_stop, attach, send):
            row.addWidget(b)
        cmd.body.addLayout(row)
        self.ptt_hint = kit.label("", 7.5, kit.MUTED)
        cmd.body.addWidget(self.ptt_hint)
        lay.addWidget(cmd)
        return w

    # ── footer ───────────────────────────────────────────────────────────
    def _build_footer(self) -> QWidget:
        self.footer = kit.label("", 7.5, kit.MUTED, QFont.Weight.DemiBold)
        self.footer.setFont(kit.font(7.5, QFont.Weight.DemiBold, spacing=1.6))
        self.footer.setFixedHeight(16)
        return self.footer

    # ── public API used by MainWindow ────────────────────────────────────
    def set_state(self, state: str) -> None:
        state = (state or "IDLE").upper()
        self.state_chip.set_state(state)
        self._speaking = state == "SPEAKING"
        words = {"LISTENING": "Listening", "THINKING": "Thinking", "EXECUTING": "Working on it",
                 "SPEAKING": "Speaking", "MUTED": "Microphone muted", "IDLE": "Standing by"}
        self.state_line.setText(words.get(state, state.title()))
        self._refresh_footer()

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)
        self.btn_mic.setText("MIC MUTED" if muted else "MIC LIVE")
        self.btn_mic.set_tone("muted" if muted else "good")
        self._refresh_footer()

    def set_names(self, assistant: str, owner: str) -> None:
        name = (assistant or "Jarvis").strip()
        self._title.setText(".".join(name.upper()) if len(name) <= 7 else name.upper())
        self._subtitle.setText(f"PERSONAL AI · FOR {owner.upper()}" if owner else "PERSONAL AI · OWNER-ONLY")

    def push_voice(self, level: float) -> None:
        self.wave.push(level, speaking=True)

    def push_mic(self, level: float) -> None:
        if not self._speaking:
            self.wave.push(level, speaking=False)

    def append_log(self, text: str) -> None:
        self.log.append_line(text)

    def show_content(self, title: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        self.content_title.setText(title or "Result")
        self.content_body.setPlainText(text[:6000])
        self.content.show()

    def set_ptt_hint(self, enabled: bool) -> None:
        self.ptt_hint.setText("Push-to-talk on: hold Ctrl+Space to speak." if enabled else "")

    # ── timers ───────────────────────────────────────────────────────────
    def _submit(self):
        text = self.input.text().strip()
        if text:
            self.input.clear()
            self.command_submitted.emit(text)

    def _tick_clock(self):
        now = datetime.now()
        self._clock.setText(now.strftime("%H:%M:%S"))
        self._date.setText(now.strftime("%A %d %B %Y").upper())
        up = int(time.time() - self._started)
        self.r_uptime.set_value(f"{up // 3600:02d}:{(up % 3600) // 60:02d}:{up % 60:02d}")

    def _tick_metrics(self):
        try:
            m = self._metrics.sample()
        except Exception:
            return
        self.g_cpu.set_value(m["cpu"])
        self.g_mem.set_value(m["mem"])
        self.g_disk.set_value(m["disk"])
        if m["gpu"] is not None:
            self.g_gpu.label = "GPU"
            self.g_gpu.set_value(m["gpu"])
        elif m["battery"] is not None:
            self.g_gpu.label = "BATTERY"
            self.g_gpu.set_value(m["battery"])
        else:
            self.g_gpu.label = "GPU"
            self.g_gpu.set_value(None)
        self.spark.push(m["down"], m["up"])
        self.net_panel.set_tag(f"↓ {kit.human_rate(m['down'])}  ↑ {kit.human_rate(m['up'])}")
        self.r_procs.set_value(str(m["procs"]))

    def _tick_ops(self):
        settings = _read_json(DATA_DIR / "config" / "app_settings.json", {})
        self.r_voice.set_value(settings.get("voice_name") or "Charon")
        # Guardian
        try:
            from core.guardian import get_guardian
            gs = get_guardian(DATA_DIR).owner_state()
            self.r_guard.set_value("Watching outside text" if gs["tainted"] else "Armed", "warn" if gs["tainted"] else "good")
        except Exception:
            self.r_guard.set_value("--")
        tg = _read_json(TELEGRAM_FILE, {})
        if tg.get("bot_token") and str(tg.get("owner_chat_id") or "").strip():
            self.r_tele.set_value("Linked", "good")
        elif tg.get("bot_token"):
            self.r_tele.set_value("Needs owner id", "warn")
        else:
            self.r_tele.set_value("Off", "text")
        sec = _read_json(DATA_DIR / "config" / "security_status.json", {})
        if sec:
            n = int(sec.get("findings", 0))
            self.r_sec.set_value(f"{'Clear' if n == 0 else f'{n} alert(s)'} · {sec.get('at', '')}", "good" if n == 0 else "bad")
        else:
            self.r_sec.set_value("First scan pending", "text")

        lines = []
        a = kit.accent_hex()
        for r in _read_json(DATA_DIR / "config" / "routines.json", [])[:6]:
            s = r.get("schedule", {})
            when = f"every {s.get('minutes')}m" if s.get("type") == "interval" else f"{s.get('time', '')}"
            dot = kit.GOOD if r.get("enabled", True) else kit.MUTED
            lines.append(f'<div><span style="color:{dot}">●</span> {r.get("name", "")[:30]} '
                         f'<span style="color:{kit.MUTED}">· {when}</span></div>')
        skills_dir = DATA_DIR / "skills"
        if skills_dir.exists():
            for d in sorted(skills_dir.iterdir()):
                m = _read_json(d / "manifest.json", None) if d.is_dir() else None
                if not m:
                    continue
                st = m.get("status", "")
                col = {"approved": kit.GOOD, "ready_for_review": kit.WARN, "failed": kit.BAD}.get(st, kit.MUTED)
                label = {"approved": "skill", "ready_for_review": "awaiting your approval", "failed": "build failed"}.get(st, st)
                lines.append(f'<div><span style="color:{col}">◆</span> {m.get("name", d.name)[:30]} '
                             f'<span style="color:{kit.MUTED}">· {label}</span></div>')
        self.ops_text.setText("".join(lines) or f'<span style="color:{kit.MUTED}">No routines or skills yet. '
                                                f'Ask Jarvis: “every morning at 8 give me a briefing”.</span>')
        self.ops.set_tag(f"{len(lines)} active" if lines else "")
        self._refresh_footer()

    def _refresh_footer(self):
        mic = "MIC · MUTED" if self._muted else "MIC · LIVE"
        chip = ""
        try:
            from core import attention, directives
            chip = directives.status_chip()
            held = attention.held_count()
            if chip and held:
                chip += f" · {held} held"
        except Exception:
            chip = ""
        self.footer.setStyleSheet(f"color: {kit.WARN if chip else kit.MUTED}; background: transparent;")
        self.footer.setText((chip.upper() + "    ·    " if chip else "") + f"{mic}    ·    ENGINE · GEMINI LIVE    ·    GUARDIAN · {self.r_guard.value.upper()}    ·    "
                            f"TELEGRAM · {self.r_tele.value.upper()}    ·    SECURITY · {self.r_sec.value.split('·')[0].strip().upper()}")
