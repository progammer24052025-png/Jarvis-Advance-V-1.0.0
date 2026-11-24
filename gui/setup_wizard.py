"""First-run setup wizard: eight short pages, nothing saved until Finish.

  1 Welcome · 2 About you · 3 AI key · 4 Voice & audio · 5 Look · 6 Reach Jarvis remotely
  7 Behaviour & privacy · 8 Done

All saving and checking lives in core/setup_store.py; this file is only the screens.
"""

from __future__ import annotations

import threading

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QPainter
from PyQt6.QtWidgets import (QCheckBox, QComboBox, QGridLayout, QHBoxLayout, QLineEdit, QPushButton,
                             QStackedWidget, QVBoxLayout, QWidget)

from core import setup_store
from core.brand import about_line

from . import kit
from .overlays import GEMINI_VOICES, PRESETS, HueWheel, _LevelMeter, list_audio_devices

_COMBO = ("QComboBox { background: rgba(255,255,255,10); color: #e8ecf3; border: 1px solid rgba(255,255,255,30);"
          " border-radius: 8px; padding: 6px 10px; min-height: 24px; }"
          "QComboBox QAbstractItemView { background: #10141d; color: #e8ecf3; selection-background-color: #2a3242; }")
_CHECK = ("QCheckBox { color: #e8ecf3; spacing: 10px; font-size: 10pt; background: transparent; }"
          "QCheckBox::indicator { width: 18px; height: 18px; }")

TITLES = ["Welcome", "About you", "AI key", "Voice & audio", "Look", "Reach Jarvis remotely",
          "Behaviour & privacy", "All set"]


class SetupWizard(QWidget):
    """Covers the main window. `finished(result)` after saving; `cancelled()` if closed without saving."""
    finished = pyqtSignal(dict)
    cancelled = pyqtSignal()
    _bg_result = pyqtSignal(str, object)          # from worker threads → UI thread

    def __init__(self, parent: QWidget, *, first_run: bool = True):
        super().__init__(parent)
        self.first_run = first_run
        self.a = setup_store.current_answers()
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setGeometry(parent.rect())
        parent.installEventFilter(self)
        self._bg_result.connect(self._on_bg_result)

        outer = QVBoxLayout(self)
        outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.card = kit.HoloPanel("Jarvis setup", "")
        self.card.setFixedWidth(720)
        outer.addWidget(self.card, 0, Qt.AlignmentFlag.AlignCenter)

        self.steps = kit.label("", 8.5, kit.MUTED)
        self.card.body.addWidget(self.steps)
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("background: transparent;")
        self.stack.setMinimumHeight(430)
        self.card.body.addWidget(self.stack)
        self.err = kit.label("", 9, kit.BAD)
        self.card.body.addWidget(self.err)

        nav = QHBoxLayout()
        self.btn_cancel = kit.HudButton("Quit" if first_run else "Cancel", "muted")
        self.btn_cancel.clicked.connect(self._cancel)
        self.btn_back = kit.HudButton("Back", "muted")
        self.btn_back.clicked.connect(self._back)
        self.btn_next = kit.HudButton("Next", "accent")
        self.btn_next.clicked.connect(self._next)
        nav.addWidget(self.btn_cancel)
        nav.addStretch(1)
        nav.addWidget(self.btn_back)
        nav.addWidget(self.btn_next)
        self.card.body.addLayout(nav)

        for build in (self._p_welcome, self._p_about, self._p_key, self._p_audio, self._p_look,
                      self._p_remote, self._p_behaviour, self._p_done):
            page = QWidget()
            page.setStyleSheet("background: transparent;")
            lay = QVBoxLayout(page)
            lay.setContentsMargins(6, 6, 6, 6)
            lay.setSpacing(8)
            build(lay)
            lay.addStretch(1)
            self.stack.addWidget(page)
        self._go(0)

    # ── frame ────────────────────────────────────────────────────────────
    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor(3, 5, 8, 242))

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if ev.type() == QEvent.Type.Resize and obj is self.parentWidget():
            self.setGeometry(obj.rect())
        return False

    def _go(self, i: int):
        self.stack.setCurrentIndex(i)
        self.err.setText("")
        self.steps.setText(f"STEP {i + 1} OF {len(TITLES)}  ·  {TITLES[i].upper()}")
        self.card.set_tag(TITLES[i])
        self.btn_back.setVisible(0 < i)
        self.btn_next.setText("FINISH" if i == len(TITLES) - 1 else ("LET'S BEGIN" if i == 0 else "NEXT"))
        if i == len(TITLES) - 1:
            self._fill_summary()

    def _collect(self):
        a = self.a
        a["owner_name"] = self.in_name.text().strip()
        a["address_as"] = self.cb_address.currentData()
        a["languages"] = [n for n, cb in self.lang_boxes if cb.isChecked()] + \
                         [x.strip() for x in self.in_lang_other.text().split(",") if x.strip()]
        a["city"] = self.in_city.text().strip()
        a["gemini_key"] = self.in_key.text().strip()
        a["openrouter_key"] = self.in_or.text().strip()
        a["groq_key"] = self.in_groq.text().strip()
        a["anthropic_key"] = self.in_claude.text().strip()
        a["input_device"] = self.cb_mic.currentData() or ""
        a["output_device"] = self.cb_spk.currentData() or ""
        a["voice_name"] = self.cb_voice.currentData()
        a["push_to_talk"] = self.ck_ptt.isChecked()
        a["assistant_name"] = self.in_assistant.text().strip()
        a["telegram_token"] = self.in_tg.text().strip()
        a["start_with_windows"] = self.ck_start.isChecked()
        a["security_watch"] = self.ck_sec.isChecked()
        a["clipboard_helper"] = self.ck_clip.isChecked()
        a["lock_screen_content"] = self.ck_lock.isChecked()
        a["quiet_enabled"] = self.ck_quiet.isChecked()
        a["quiet_start"] = self.in_q_start.text().strip() or "23:00"
        a["quiet_end"] = self.in_q_end.text().strip() or "07:00"

    _PAGE_FIELDS = {1: ("owner_name", "address_as"), 2: ("gemini_key",), 4: ("app_theme", "assistant_name"),
                    5: ("telegram_token", "telegram_chat_id"), 6: ("quiet_hours",)}

    def _next(self):
        self._collect()
        i = self.stack.currentIndex()
        errs = setup_store.validate(self.a)
        page_errs = [errs[f] for f in self._PAGE_FIELDS.get(i, ()) if f in errs]
        if page_errs:
            self.err.setText(page_errs[0])
            return
        if i == 5 and self.a.get("telegram_token") and not self.a.get("telegram_chat_id"):
            self.err.setText("Press “Find me” and confirm it's you, or clear the token to skip Telegram for now.")
            return
        if i < len(TITLES) - 1:
            self._go(i + 1)
            return
        self._finish()

    def _back(self):
        self._go(max(0, self.stack.currentIndex() - 1))

    def _cancel(self):
        self._stop_tests()
        self.parentWidget().removeEventFilter(self)
        self.cancelled.emit()
        self.deleteLater()

    def _finish(self):
        try:
            result = setup_store.save(self.a)
        except Exception as exc:
            self.err.setText(f"Couldn't save: {exc}")
            return
        self._stop_tests()
        self.parentWidget().removeEventFilter(self)
        self.finished.emit(result)
        self.deleteLater()

    # ── worker results ───────────────────────────────────────────────────
    def _run_bg(self, tag: str, fn):
        threading.Thread(target=lambda: self._bg_result.emit(tag, fn()), daemon=True).start()

    def _on_bg_result(self, tag: str, value):
        if tag == "key":
            ok, msg = value
            self.key_note.setText(("✓ " if ok else "✗ ") + msg)
            self.key_note.setStyleSheet(f"color: {kit.GOOD if ok else kit.BAD}; background: transparent;")
            self.btn_test_key.setEnabled(True)
        elif tag == "tg":
            found, msg = value
            self.btn_find.setEnabled(True)
            self.tg_note.setText(msg)
            self._tg_found = found
            self.tg_confirm.setVisible(bool(found))
        elif tag == "mic":
            self.mic_note.setText(value)
            self.btn_mic.setEnabled(True)
            self._mic_t.stop()

    # ── 1 Welcome ────────────────────────────────────────────────────────
    def _p_welcome(self, lay):
        lay.addWidget(kit.label("Hello. Let's set up your Jarvis.", 17, kit.TEXT))
        lay.addWidget(kit.label(about_line(), 9, kit.accent_hex()))
        lay.addSpacing(8)
        for line in ("It takes about two minutes. You can change any of this later in Customise.",
                     "Jarvis works for one person: you. It obeys only you, on this PC and your own Telegram.",
                     "Your keys are encrypted to your Windows account. Your data stays on this PC: "
                     "nothing is sent to the developer or anyone else.",
                     "Nothing is saved until you press Finish on the last page."):
            lay.addWidget(kit.label("•  " + line, 10, kit.TEXT))

    # ── 2 About you ──────────────────────────────────────────────────────
    def _p_about(self, lay):
        lay.addWidget(kit.label("Your name", 9, kit.MUTED))
        self.in_name = kit.HudInput("e.g. Alex Mercer")
        self.in_name.setText(self.a["owner_name"])
        lay.addWidget(self.in_name)
        lay.addWidget(kit.label("How should Jarvis address you?", 9, kit.MUTED))
        self.cb_address = QComboBox()
        self.cb_address.setStyleSheet(_COMBO)
        for label, val in (("Sir", "sir"), ("Ma'am", "ma'am"), ("By my first name", "name"), ("Boss", "boss")):
            self.cb_address.addItem(label, val)
        self.cb_address.setCurrentIndex(max(0, self.cb_address.findData(self.a["address_as"])))
        lay.addWidget(self.cb_address)
        lay.addWidget(kit.label("Languages you speak (Jarvis will reply in the one you use)", 9, kit.MUTED))
        row = QHBoxLayout()
        self.lang_boxes = []
        for n in ("English", "Hindi", "Gujarati", "Marathi", "Tamil"):
            cb = QCheckBox(n)
            cb.setStyleSheet(_CHECK)
            cb.setChecked(n in self.a["languages"])
            row.addWidget(cb)
            self.lang_boxes.append((n, cb))
        row.addStretch(1)
        lay.addLayout(row)
        self.in_lang_other = kit.HudInput("Other languages, comma separated (optional)")
        self.in_lang_other.setText(", ".join(x for x in self.a["languages"]
                                             if x not in ("English", "Hindi", "Gujarati", "Marathi", "Tamil")))
        lay.addWidget(self.in_lang_other)
        lay.addWidget(kit.label("Your city (for weather and local time, optional)", 9, kit.MUTED))
        self.in_city = kit.HudInput("e.g. Ahmedabad")
        self.in_city.setText(self.a["city"])
        lay.addWidget(self.in_city)

    # ── 3 AI key ─────────────────────────────────────────────────────────
    def _secret_input(self, placeholder: str, value: str) -> QLineEdit:
        w = kit.HudInput(placeholder)
        w.setEchoMode(QLineEdit.EchoMode.Password)
        w.setText(value or "")
        return w

    def _p_key(self, lay):
        lay.addWidget(kit.label("Jarvis's voice and brain run on Google Gemini. The key is free.", 10, kit.TEXT))
        link = kit.HudButton("Get a free key (opens Google AI Studio)", "muted")
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://aistudio.google.com/apikey")))
        lay.addWidget(link)
        lay.addWidget(kit.label("Gemini API key", 9, kit.MUTED))
        row = QHBoxLayout()
        self.in_key = self._secret_input("AIza…", self.a["gemini_key"])
        row.addWidget(self.in_key, 1)
        show = kit.HudButton("Show", "muted")
        show.setCheckable(True)
        show.toggled.connect(lambda on: self.in_key.setEchoMode(QLineEdit.EchoMode.Normal if on else QLineEdit.EchoMode.Password))
        row.addWidget(show)
        self.btn_test_key = kit.HudButton("Test", "good")
        self.btn_test_key.clicked.connect(self._test_key)
        row.addWidget(self.btn_test_key)
        lay.addLayout(row)
        self.key_note = kit.label("", 9, kit.MUTED)
        lay.addWidget(self.key_note)
        lay.addSpacing(6)
        lay.addWidget(kit.label("Optional backups if Gemini is busy (leave empty if you don't have them)", 9, kit.MUTED))
        self.in_or = self._secret_input("OpenRouter key (sk-or-…)", self.a["openrouter_key"])
        self.in_groq = self._secret_input("Groq key (gsk_…)", self.a["groq_key"])
        self.in_claude = self._secret_input("Claude key (sk-ant-…), used by Skill Forge for best results", self.a["anthropic_key"])
        for w in (self.in_or, self.in_groq, self.in_claude):
            lay.addWidget(w)

    def _test_key(self):
        self.btn_test_key.setEnabled(False)
        self.key_note.setText("Checking with Google…")
        key = self.in_key.text().strip()
        self._run_bg("key", lambda: setup_store.test_gemini_key(key))

    # ── 4 Voice & audio ──────────────────────────────────────────────────
    def _p_audio(self, lay):
        try:
            ins, outs = list_audio_devices()
        except Exception:
            ins, outs = [], []
        self.cb_mic, self.cb_spk = QComboBox(), QComboBox()
        for cb, items, cur in ((self.cb_mic, ins, self.a["input_device"]), (self.cb_spk, outs, self.a["output_device"])):
            cb.setStyleSheet(_COMBO)
            cb.addItem("System default", "")
            for _idx, name in items:
                cb.addItem(name, name)
            i = cb.findData(cur)
            cb.setCurrentIndex(i if i >= 0 else 0)
        lay.addWidget(kit.label("Microphone", 9, kit.MUTED))
        lay.addWidget(self.cb_mic)
        mrow = QHBoxLayout()
        self.btn_mic = kit.HudButton("Test mic (3 s)", "good")
        self.btn_mic.clicked.connect(self._test_mic)
        self.meter = _LevelMeter()
        mrow.addWidget(self.btn_mic)
        mrow.addWidget(self.meter, 1)
        lay.addLayout(mrow)
        self.mic_note = kit.label("Press Test and say something: the bar should move.", 8.5, kit.MUTED)
        lay.addWidget(self.mic_note)
        lay.addWidget(kit.label("Speakers", 9, kit.MUTED))
        srow = QHBoxLayout()
        srow.addWidget(self.cb_spk, 1)
        tone = kit.HudButton("Play tone", "muted")
        tone.clicked.connect(self._test_speaker)
        srow.addWidget(tone)
        lay.addLayout(srow)
        lay.addWidget(kit.label("Jarvis's voice", 9, kit.MUTED))
        self.cb_voice = QComboBox()
        self.cb_voice.setStyleSheet(_COMBO)
        for name, desc in GEMINI_VOICES:
            self.cb_voice.addItem(f"{name} · {desc}", name)
        self.cb_voice.setCurrentIndex(max(0, self.cb_voice.findData(self.a["voice_name"])))
        lay.addWidget(self.cb_voice)
        self.ck_ptt = QCheckBox("Push-to-talk: only listen while I hold Ctrl+Space")
        self.ck_ptt.setStyleSheet(_CHECK)
        self.ck_ptt.setChecked(bool(self.a["push_to_talk"]))
        lay.addWidget(self.ck_ptt)
        self._peak = 0.0
        self._mic_t = QTimer(self)
        self._mic_t.timeout.connect(self._tick_meter)

    def _tick_meter(self):
        self.meter.level = self._peak
        self._peak *= 0.85
        self.meter.update()

    @staticmethod
    def _dev_index(name, kind):
        if not name:
            return None
        import sounddevice as sd
        for i, d in enumerate(sd.query_devices()):
            if d["name"] == name and d[f"max_{kind}_channels"] > 0:
                return i
        return None

    def _test_mic(self):
        dev_name = self.cb_mic.currentData()
        self.btn_mic.setEnabled(False)
        self.mic_note.setText("Listening… say something.")
        self._mic_t.start(40)

        def run():
            import numpy as np
            import sounddevice as sd
            heard = 0.0
            try:
                def cb(indata, frames, t, status):
                    nonlocal heard
                    lvl = float(np.sqrt(np.mean(np.square(indata.astype(np.float32))))) / 3000.0
                    self._peak = max(self._peak, min(1.0, lvl))
                    heard = max(heard, lvl)
                with sd.InputStream(device=self._dev_index(dev_name, "input"), channels=1, samplerate=16000,
                                    dtype="int16", callback=cb):
                    sd.sleep(3000)
                return "✓ I can hear you." if heard > 0.05 else "I heard almost nothing. Check the mic or pick another one."
            except Exception as exc:
                return f"That microphone didn't work: {str(exc)[:80]}"
        self._run_bg("mic", run)

    def _test_speaker(self):
        dev_name = self.cb_spk.currentData()

        def run():
            try:
                import numpy as np
                import sounddevice as sd
                t = np.linspace(0, 0.6, int(24000 * 0.6), False)
                tone = (0.2 * np.sin(2 * np.pi * 660 * t) * np.minimum(1, (0.6 - t) * 8)).astype(np.float32)
                sd.play(tone, 24000, device=self._dev_index(dev_name, "output"))
            except Exception:
                pass
        threading.Thread(target=run, daemon=True).start()

    def _stop_tests(self):
        try:
            self._mic_t.stop()
        except Exception:
            pass

    # ── 5 Look ───────────────────────────────────────────────────────────
    def _p_look(self, lay):
        lay.addWidget(kit.label("Pick Jarvis's colour (the orb and the screen use it)", 9, kit.MUTED))
        row = QHBoxLayout()
        self.wheel = HueWheel(self.a["app_theme"])
        self.wheel.picked.connect(self._pick_colour)
        row.addWidget(self.wheel)
        grid = QGridLayout()
        for i, hexc in enumerate(PRESETS):
            b = QPushButton()
            b.setFixedSize(34, 34)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"QPushButton {{ background: {hexc}; border: 2px solid rgba(255,255,255,40); border-radius: 17px; }}")
            b.clicked.connect(lambda _=False, h=hexc: self._pick_colour(h))
            grid.addWidget(b, i // 4, i % 4)
        col = QVBoxLayout()
        col.addLayout(grid)
        self.colour_note = kit.label("", 9, kit.TEXT)
        col.addWidget(self.colour_note)
        col.addStretch(1)
        row.addLayout(col, 1)
        lay.addLayout(row)
        lay.addWidget(kit.label("What should the assistant be called?", 9, kit.MUTED))
        self.in_assistant = kit.HudInput("Jarvis")
        self.in_assistant.setText(self.a["assistant_name"])
        lay.addWidget(self.in_assistant)
        self._pick_colour(self.a["app_theme"])

    def _pick_colour(self, hexc: str):
        self.a["app_theme"] = hexc
        self.colour_note.setText(f"Chosen: {hexc}")
        try:
            kit.set_accent(hexc)          # live preview
        except Exception:
            pass

    # ── 6 Remote ─────────────────────────────────────────────────────────
    def _p_remote(self, lay):
        lay.addWidget(kit.label("Optional: talk to Jarvis from your phone with Telegram, and get its alerts there.", 10, kit.TEXT))
        for line in ("1. In Telegram, open @BotFather, send /newbot and follow the steps.",
                     "2. Paste the token it gives you below.",
                     "3. Send any message to your new bot, then press “Find me”."):
            lay.addWidget(kit.label(line, 9, kit.MUTED))
        self.in_tg = self._secret_input("Bot token, e.g. 123456789:AA…", self.a["telegram_token"])
        lay.addWidget(self.in_tg)
        row = QHBoxLayout()
        self.btn_find = kit.HudButton("Find me", "good")
        self.btn_find.clicked.connect(self._find_me)
        row.addWidget(self.btn_find)
        row.addStretch(1)
        lay.addLayout(row)
        self.tg_note = kit.label(("✓ Linked to chat " + self.a["telegram_chat_id"]) if self.a["telegram_chat_id"] else "", 9, kit.TEXT)
        lay.addWidget(self.tg_note)
        self.tg_confirm = QWidget()
        crow = QHBoxLayout(self.tg_confirm)
        crow.setContentsMargins(0, 0, 0, 0)
        yes = kit.HudButton("Yes, that's me", "good")
        no = kit.HudButton("No", "bad")
        yes.clicked.connect(self._tg_yes)
        no.clicked.connect(self._tg_no)
        crow.addWidget(yes)
        crow.addWidget(no)
        crow.addStretch(1)
        self.tg_confirm.setVisible(False)
        lay.addWidget(self.tg_confirm)
        lay.addWidget(kit.label("Only the chat you confirm here can command Jarvis. "
                                "Discord and the phone app can be added later in Settings.", 8.5, kit.MUTED))
        self._tg_found = None

    def _find_me(self):
        self.btn_find.setEnabled(False)
        self.tg_note.setText("Looking for your message…")
        tok = self.in_tg.text().strip()
        self._run_bg("tg", lambda: setup_store.telegram_find_chat(tok))

    def _tg_yes(self):
        if self._tg_found:
            self.a["telegram_chat_id"] = self._tg_found["chat_id"]
            self.tg_note.setText(f"✓ Linked to {self._tg_found['name']}. Only this chat can command Jarvis.")
        self.tg_confirm.setVisible(False)

    def _tg_no(self):
        self.a["telegram_chat_id"] = ""
        self._tg_found = None
        self.tg_confirm.setVisible(False)
        self.tg_note.setText("Not linked. Send your bot a message from your own Telegram, then press Find me again.")

    # ── 7 Behaviour & privacy ────────────────────────────────────────────
    def _p_behaviour(self, lay):
        def ck(text, on):
            c = QCheckBox(text)
            c.setStyleSheet(_CHECK)
            c.setChecked(bool(on))
            lay.addWidget(c)
            return c
        self.ck_start = ck("Start Jarvis when I sign in to Windows", self.a["start_with_windows"])
        self.ck_sec = ck("Watch this PC for suspicious changes in the background", self.a["security_watch"])
        self.ck_clip = ck("Offer help with text I copy (translate, summarise, explain)", self.a["clipboard_helper"])
        self.ck_lock = ck("Show alert details on the lock screen (off = just “Jarvis has an update”)", self.a["lock_screen_content"])
        self.ck_quiet = ck("Quiet hours: don't speak unless I ask", self.a["quiet_enabled"])
        row = QHBoxLayout()
        self.in_q_start = kit.HudInput("23:00")
        self.in_q_start.setText(self.a["quiet_start"])
        self.in_q_end = kit.HudInput("07:00")
        self.in_q_end.setText(self.a["quiet_end"])
        row.addWidget(kit.label("from", 9, kit.MUTED))
        row.addWidget(self.in_q_start)
        row.addWidget(kit.label("to", 9, kit.MUTED))
        row.addWidget(self.in_q_end)
        row.addStretch(1)
        lay.addLayout(row)
        lay.addWidget(kit.label("Later you can just tell Jarvis things like “don't talk until 8, text me instead”.", 8.5, kit.MUTED))

    # ── 8 Done ───────────────────────────────────────────────────────────
    def _p_done(self, lay):
        lay.addWidget(kit.label("Here's what I'll save:", 12, kit.TEXT))
        self.summary = kit.label("", 10, kit.TEXT)
        lay.addWidget(self.summary)
        lay.addWidget(kit.label("Press Finish and Jarvis will say hello.", 9, kit.MUTED))

    def _fill_summary(self):
        a = self.a
        addr = {"sir": "Sir", "ma'am": "Ma'am", "name": "your first name", "boss": "Boss"}.get(a["address_as"], a["address_as"])
        rows = [
            f"You: {a['owner_name']} (called {addr})" + (f", {a['city']}" if a['city'] else ""),
            "Languages: " + ", ".join(a["languages"] or ["English"]),
            "Gemini key: ✓ set (encrypted)" + ("; backups: " + ", ".join(n for n, k in (("OpenRouter", a["openrouter_key"]),
                                                                                       ("Groq", a["groq_key"]), ("Claude", a["anthropic_key"])) if k)
                                              if any((a["openrouter_key"], a["groq_key"], a["anthropic_key"])) else ""),
            f"Voice: {a['voice_name']}" + (" · push-to-talk" if a["push_to_talk"] else ""),
            f"Mic: {a['input_device'] or 'system default'} · Speakers: {a['output_device'] or 'system default'}",
            f"Name: {a['assistant_name']} · colour {a['app_theme']}",
            "Telegram: " + ("linked" if a["telegram_chat_id"] else "not set up"),
            "Start with Windows: " + ("yes" if a["start_with_windows"] else "no")
            + " · security watch: " + ("on" if a["security_watch"] else "off")
            + " · quiet hours: " + (f"{a['quiet_start']}–{a['quiet_end']}" if a["quiet_enabled"] else "off"),
        ]
        self.summary.setText("\n".join("•  " + r for r in rows))
