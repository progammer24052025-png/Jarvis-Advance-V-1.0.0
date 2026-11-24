"""Phase 3: research verification, background tasks, quiet hours, undo, session memory."""

import json
import time
from datetime import datetime
from pathlib import Path

from core import quiet, research, session_memory, tasks, undo


# ── research: quotes are checked in code ──────────────────────────────────

PAGE = ("Bambu Lab A1 Mini\n\nThe A1 Mini costs $249 and prints at up to 500 mm/s. "
        "Reviewers praised its “automatic calibration” — a feature rare at this price.")


def test_quote_verification_accepts_exact_and_normalised_quotes():
    assert research.quote_in_page("The A1 Mini costs $249 and prints at up to 500 mm/s.", PAGE)
    assert research.quote_in_page('reviewers praised its "automatic calibration" - a feature rare', PAGE)


def test_quote_verification_rejects_invented_or_tiny_quotes():
    assert not research.quote_in_page("The A1 Mini costs $199 and prints at up to 500 mm/s.", PAGE)
    assert not research.quote_in_page("costs", PAGE)
    assert not research.quote_in_page("", PAGE)


def test_citations_renumber_by_source_and_flag_unknown():
    findings = [{"url": "https://a.com", "title": "A"}, {"url": "https://b.com", "title": "B"},
                {"url": "https://a.com", "title": "A"}]
    text, order = research.renumber_citations("X is true [2]. Y too [1, 3]. Z [9].", findings)
    assert text == "X is true [1]. Y too [2]. Z [?]."
    assert [o["url"] for o in order] == ["https://b.com", "https://a.com"]


def test_safe_fetch_refuses_local_and_private_addresses():
    for url in ("http://127.0.0.1:8765/gateway/devices", "http://localhost/", "http://192.168.1.1/", "file:///C:/x"):
        try:
            research.safe_fetch(url, timeout=3)
        except ValueError as exc:
            assert "refused" in str(exc) or "not a web address" in str(exc)
        else:
            raise AssertionError(f"fetched {url}")


# ── background task engine: checkpoints, finish early, stop ───────────────

def test_task_engine_runs_checkpoints_and_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "TASKS_DIR", tmp_path / "tasks")
    monkeypatch.setattr(tasks, "REPORTS_DIR", tmp_path / "reports")
    calls = []

    def fake_step(task, task_dir, deadline, wrap_up):
        calls.append(task["state"].get("stage"))
        st = task["state"]
        if st["stage"] == "plan":
            st["stage"] = "write"
            return False
        st["report"] = "# Report\n\nmeta\n\nBody text [1]."
        st["stage"] = "done"
        return True

    monkeypatch.setattr(tasks.research, "step", fake_step)
    got = []
    eng = tasks.TaskEngine(notify=lambda title, text, path: got.append((title, path)))
    t = eng.start("best budget 3d printers", minutes=2)
    for _ in range(100):
        if got:
            break
        time.sleep(0.1)
    assert got and got[0][0].startswith("Research ready")
    done = eng.load(t["id"])
    assert done["status"] == "done" and Path(done["report"]).exists()
    assert calls == ["plan", "write"]


def test_task_engine_resumes_and_gives_up_after_three_interrupts(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "TASKS_DIR", tmp_path / "tasks")
    eng = tasks.TaskEngine()
    monkeypatch.setattr(eng, "ensure_running", lambda: None)
    t = eng.start("topic one", minutes=5)
    t["status"] = "running"
    eng.save(t)
    eng.resume_after_restart()
    assert eng.load(t["id"])["status"] == "queued"
    t = eng.load(t["id"]); t["status"] = "running"; t["interrupts"] = 3; eng.save(t)
    eng.resume_after_restart()
    assert eng.load(t["id"])["status"] == "failed"


def test_scheduled_task_waits_for_its_time(tmp_path, monkeypatch):
    monkeypatch.setattr(tasks, "TASKS_DIR", tmp_path / "tasks")
    eng = tasks.TaskEngine()
    monkeypatch.setattr(eng, "ensure_running", lambda: None)
    later = (datetime.now().hour + 2) % 24
    t = eng.start("tonight compare laptops", minutes=5, at=f"{later:02d}:00")
    assert t["status"] == "scheduled" and eng._next() is None


# ── quiet hours ────────────────────────────────────────────────────────────

def test_quiet_hours_window_crosses_midnight(monkeypatch):
    monkeypatch.setattr(quiet, "get_config", lambda: {"enabled": True, "start": "23:00", "end": "07:00"})
    assert quiet.is_quiet(datetime(2026, 11, 23, 23, 30))
    assert quiet.is_quiet(datetime(2026, 11, 24, 6, 59))
    assert not quiet.is_quiet(datetime(2026, 11, 24, 7, 0))
    assert not quiet.is_quiet(datetime(2026, 11, 24, 14, 0))
    monkeypatch.setattr(quiet, "get_config", lambda: {"enabled": False, "start": "23:00", "end": "07:00"})
    assert not quiet.is_quiet(datetime(2026, 11, 23, 23, 30))


# ── undo ───────────────────────────────────────────────────────────────────

def test_undo_restores_deleted_and_overwritten_files(tmp_path, monkeypatch):
    monkeypatch.setattr(undo, "UNDO_DIR", tmp_path / "undo")
    monkeypatch.setattr(undo, "JOURNAL", tmp_path / "undo" / "journal.json")
    f = tmp_path / "notes.txt"
    f.write_text("original", encoding="utf-8")
    b = undo.backup(f)
    f.write_text("changed", encoding="utf-8")
    undo.record("write", path=str(f), backup=b)
    assert "Restored" in undo.undo()
    assert f.read_text(encoding="utf-8") == "original"
    assert undo.undo() == "Nothing to undo."


# ── session memory ────────────────────────────────────────────────────────

def test_last_session_note_is_used_once(tmp_path, monkeypatch):
    monkeypatch.setattr(session_memory, "SUMMARY", tmp_path / "summary.json")
    (tmp_path / "summary.json").write_text(json.dumps({"at": datetime.now().isoformat(timespec="seconds"),
                                                      "summary": "Talked about the exam on Thursday.", "consumed": False}))
    first = session_memory.startup_context()
    assert "exam on Thursday" in first
    assert session_memory.startup_context() == ""
