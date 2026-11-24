"""Background tasks: long work that runs beside the conversation.

  "Research the best budget 3D printers for ten minutes"
  "Tonight at 2, compare these three laptops"

• Every step is saved (config/tasks/<id>/task.json). A crash or restart resumes from the
  last step; a task interrupted 3 times is marked failed instead of retrying for ever.
• It has a budget (minutes). When time runs out it stops searching and writes up what it has:
  stopping is never the same as losing the work.
• No model available (quota) = a 5-minute pause, then it continues.
• You stay in charge: status, stop, "that's enough, write it up", or steer ("also look at prices in euros").
• Reports are saved to reports/ as Markdown and announced when ready.
"""

from __future__ import annotations

from core.paths import DATA_DIR  # owner data lives apart from the program
import json
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable, Optional

from . import research

BASE_DIR = Path(__file__).resolve().parent.parent
TASKS_DIR = DATA_DIR / "config" / "tasks"
REPORTS_DIR = DATA_DIR / "reports"
MAX_INTERRUPTS = 3
PAUSE_SECS = 300


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:50] or "report"


class TaskEngine:
    def __init__(self, notify: Optional[Callable[[str, str, str], None]] = None):
        self.notify = notify or (lambda title, text, path: print(f"[Tasks] {title}: {text}"))
        self._lock = threading.Lock()
        self._flags: dict[str, set] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

    # ── storage ──────────────────────────────────────────────────────────
    def _dir(self, tid: str) -> Path:
        return TASKS_DIR / tid

    def load(self, tid: str) -> dict | None:
        try:
            return json.loads((self._dir(tid) / "task.json").read_text(encoding="utf-8"))
        except Exception:
            return None

    def save(self, task: dict) -> None:
        d = self._dir(task["id"])
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / "task.json.tmp"
        tmp.write_text(json.dumps(task, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(d / "task.json")

    def all(self) -> list[dict]:
        out = []
        if TASKS_DIR.exists():
            for d in sorted(TASKS_DIR.iterdir()):
                t = self.load(d.name)
                if t:
                    out.append(t)
        return out

    def find(self, key: str = "") -> dict | None:
        tasks = self.all()
        key = (key or "").lower().strip()
        if key:
            for t in reversed(tasks):
                if t["id"] == key or key in t["topic"].lower():
                    return t
        active = [t for t in tasks if t["status"] in ("running", "scheduled", "paused")]
        return (active or tasks or [None])[-1]

    # ── commands ─────────────────────────────────────────────────────────
    def start(self, topic: str, minutes: int = 10, at: str | None = None) -> dict:
        topic = (topic or "").strip()
        if len(topic) < 4:
            raise ValueError("Tell me what to research.")
        minutes = max(2, min(60, int(minutes or 10)))
        start_at = None
        if at:
            from core.routines import parse_time
            hh, mm = map(int, parse_time(at).split(":"))
            when = datetime.now().replace(hour=hh, minute=mm, second=0, microsecond=0)
            if when <= datetime.now():
                when += timedelta(days=1)
            start_at = when.isoformat(timespec="seconds")
        task = {"id": uuid.uuid4().hex[:8], "kind": "research", "topic": topic, "minutes": minutes,
                "status": "scheduled" if start_at else "queued", "start_at": start_at,
                "created": datetime.now().isoformat(timespec="seconds"), "started": None, "finished": None,
                "interrupts": 0, "steer": [], "state": {"stage": "plan"}, "report": None, "error": None}
        self.save(task)
        self.ensure_running()
        return task

    def control(self, tid: str, flag: str) -> None:
        with self._lock:
            self._flags.setdefault(tid, set()).add(flag)

    def steer(self, tid: str, text: str) -> None:
        t = self.load(tid)
        if t:
            t.setdefault("steer", []).append(text.strip()[:300])
            self.save(t)

    def status_text(self, t: dict) -> str:
        st = t.get("state", {})
        if t["status"] == "done":
            return f"'{t['topic']}' is finished: report at {t['report']}."
        if t["status"] == "scheduled":
            return f"'{t['topic']}' is scheduled for {t['start_at'][11:16]}."
        if t["status"] in ("failed", "stopped"):
            return f"'{t['topic']}' {t['status']}" + (f": {t['error']}" if t.get("error") else ".")
        stage = st.get("stage", "plan")
        if stage == "investigate":
            prog = f"investigating question {min(st.get('qi', 0) + 1, len(st.get('questions', [])))} of {len(st.get('questions', []))}, " \
                   f"{len(st.get('sources', {}))} pages read, {len(st.get('findings', []))} quotes verified"
        else:
            prog = {"plan": "planning questions", "write": "writing the report", "verify": "fact-checking the report"}.get(stage, stage)
        left = ""
        if t.get("started"):
            rem = int(t["minutes"] * 60 - (time.time() - datetime.fromisoformat(t["started"]).timestamp()))
            left = f", about {max(0, rem) // 60} min left" if rem > 0 else ", wrapping up"
        return f"'{t['topic']}' is {t['status']}: {prog}{left}."

    # ── runner ───────────────────────────────────────────────────────────
    def ensure_running(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="task-engine")
        self._thread.start()

    def resume_after_restart(self) -> None:
        """Tasks that were running when Jarvis stopped: resume, unless interrupted too often."""
        for t in self.all():
            if t["status"] in ("running", "paused"):
                t["interrupts"] = t.get("interrupts", 0) + 1
                if t["interrupts"] > MAX_INTERRUPTS:
                    t.update(status="failed", error="interrupted too many times", finished=datetime.now().isoformat(timespec="seconds"))
                else:
                    t["status"] = "queued"
                self.save(t)
        self.ensure_running()

    def _next(self) -> dict | None:
        now = datetime.now()
        for t in self.all():
            if t["status"] == "queued":
                return t
            if t["status"] == "scheduled" and t.get("start_at") and datetime.fromisoformat(t["start_at"]) <= now:
                return t
            if t["status"] == "paused" and t.get("resume_at") and datetime.fromisoformat(t["resume_at"]) <= now:
                return t
        return None

    def _loop(self) -> None:
        while not self._stop.is_set():
            t = self._next()
            if t is None:
                self._stop.wait(5)
                continue
            self._run(t)

    def _run(self, t: dict) -> None:
        tid = t["id"]
        if not t.get("started"):
            t["started"] = datetime.now().isoformat(timespec="seconds")
        t["status"] = "running"
        self.save(t)
        deadline = datetime.fromisoformat(t["started"]).timestamp() + t["minutes"] * 60
        hard_stop = deadline + 8 * 60  # time for writing + verification
        while True:
            flags = self._flags.get(tid, set())
            if "stop" in flags:
                t.update(status="stopped", finished=datetime.now().isoformat(timespec="seconds"))
                self.save(t)
                self._flags.pop(tid, None)
                return
            fresh = self.load(tid) or t
            t["steer"] = fresh.get("steer", [])
            try:
                done = research.step(t, self._dir(tid), deadline, wrap_up=("finish" in flags or time.time() > hard_stop))
            except research.Paused as exc:
                t.update(status="paused", resume_at=(datetime.now() + timedelta(seconds=PAUSE_SECS)).isoformat(timespec="seconds"),
                         error=f"paused: {exc}")
                self.save(t)
                return
            except Exception as exc:
                t.update(status="failed", error=str(exc)[:200], finished=datetime.now().isoformat(timespec="seconds"))
                self.save(t)
                self.notify("Research failed", f"The research on '{t['topic']}' stopped with an error: {str(exc)[:120]}", "")
                return
            self.save(t)  # checkpoint after every step
            if done:
                break
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path = REPORTS_DIR / f"{datetime.now():%Y-%m-%d_%H%M}_{_slug(t['topic'])}.md"
        path.write_text(t["state"]["report"], encoding="utf-8")
        t.update(status="done", report=str(path), finished=datetime.now().isoformat(timespec="seconds"), error=None)
        self.save(t)
        self._flags.pop(tid, None)
        st = t["state"]
        summary = st["report"].split("\n\n", 3)[2][:400] if st["report"].count("\n\n") >= 3 else ""
        self.notify(f"Research ready: {t['topic']}",
                    f"I finished researching '{t['topic']}': {len(st.get('sources', {}))} pages read in full and "
                    f"{len(st.get('findings', []))} quotes checked against their sources. {summary}", str(path))


def background_task_tool(engine: TaskEngine, params: dict) -> str:
    action = str(params.get("action") or "start").lower()
    if action == "start":
        try:
            t = engine.start(str(params.get("topic") or ""), int(params.get("minutes") or 10), params.get("at"))
        except ValueError as exc:
            return str(exc)
        when = f" at {t['start_at'][11:16]}" if t.get("start_at") else " now"
        return (f"Started{when}: researching '{t['topic']}' for up to {t['minutes']} minutes in the background. "
                "Say it in one sentence and keep the conversation going; I'll announce the report when it's ready.")
    if action == "list":
        tasks = engine.all()[-8:]
        return "\n".join(engine.status_text(t) for t in tasks) if tasks else "No background tasks yet."
    t = engine.find(str(params.get("task") or params.get("topic") or ""))
    if t is None:
        return "There's no background task to act on."
    if action == "status":
        return engine.status_text(t)
    if action == "stop":
        engine.control(t["id"], "stop")
        if t["status"] in ("queued", "scheduled", "paused"):
            t["status"] = "stopped"
            engine.save(t)
        return f"Stopping '{t['topic']}'."
    if action in ("finish", "write_up", "wrap_up"):
        engine.control(t["id"], "finish")
        return f"Wrapping up '{t['topic']}': writing the report from what I have now."
    if action == "steer":
        engine.steer(t["id"], str(params.get("instruction") or ""))
        return f"Noted for '{t['topic']}': {params.get('instruction')}"
    if action in ("open", "show"):
        if t.get("report") and Path(t["report"]).exists():
            return Path(t["report"]).read_text(encoding="utf-8")[:6000]
        return engine.status_text(t)
    return f"Unknown action '{action}'."
