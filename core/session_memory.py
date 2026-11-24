"""'Last time we talked about…': a short summary of each conversation, mentioned once next time.

- Every spoken/typed exchange is appended to a rolling transcript (last 60 turns).
- A summary is written every 15 turns and when Jarvis shuts down.
- At the next start-up the summary goes into Jarvis's context once, then it's marked used,
  so it's mentioned naturally a single time and never repeated.
"""

from __future__ import annotations

from core.paths import DATA_DIR  # owner data lives apart from the program
import json
import threading
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
TRANSCRIPT = DATA_DIR / "config" / "session_transcript.json"
SUMMARY = DATA_DIR / "config" / "session_summary.json"
_lock = threading.Lock()
_turns_since_summary = 0


def _read(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def record_turn(user_text: str, jarvis_text: str) -> None:
    global _turns_since_summary
    user_text, jarvis_text = (user_text or "").strip(), (jarvis_text or "").strip()
    if not user_text and not jarvis_text:
        return
    with _lock:
        turns = _read(TRANSCRIPT, [])
        turns.append({"at": datetime.now().isoformat(timespec="seconds"), "you": user_text[:600], "jarvis": jarvis_text[:600]})
        _write(TRANSCRIPT, turns[-60:])
        _turns_since_summary += 1
        due = _turns_since_summary >= 15
    if due:
        threading.Thread(target=summarize_now, daemon=True, name="session-summary").start()


def summarize_now(timeout_hint: float = 20.0) -> str | None:
    """Summarise today's recent conversation and save it for next time."""
    global _turns_since_summary
    turns = _read(TRANSCRIPT, [])
    recent = [t for t in turns if t.get("at", "") >= (datetime.now() - timedelta(hours=12)).isoformat()]
    if len(recent) < 3:
        return None
    convo = "\n".join(f"Owner: {t['you']}\nJarvis: {t['jarvis']}" for t in recent[-30:])
    try:
        from llm_client import client
        text = client.chat(
            "Summarise this conversation between the owner and their assistant Jarvis in 2-3 short sentences, "
            "written as a note to Jarvis for next time: what they talked about, anything unfinished or promised, "
            "and the owner's mood if it was clear. No greeting, no bullet points.\n\n" + convo,
            system="You write brief, warm, factual handover notes.", max_tokens=300, temperature=0.3,
        ).strip()
    except Exception as exc:
        print(f"[SessionMemory] summary failed: {exc}")
        return None
    if not text:
        return None
    _write(SUMMARY, {"at": datetime.now().isoformat(timespec="seconds"), "summary": text, "consumed": False})
    with _lock:
        _turns_since_summary = 0
    return text


def startup_context() -> str:
    """Return last session's note once (then mark it used). Empty if none or older than a week."""
    data = _read(SUMMARY, {})
    if not data or data.get("consumed"):
        return ""
    try:
        when = datetime.fromisoformat(data["at"])
    except Exception:
        return ""
    if datetime.now() - when > timedelta(days=7):
        return ""
    data["consumed"] = True
    _write(SUMMARY, data)
    ago = "earlier today" if when.date() == datetime.now().date() else when.strftime("on %A")
    return (f"[LAST TIME YOU TALKED ({ago})]\n{data['summary']}\n"
            "Mention this naturally once if it fits (e.g. ask how something went). Don't recite it.\n")
