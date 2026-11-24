"""Undo for file actions Jarvis performed: create, write, move, rename, delete.

Before anything is overwritten or deleted, a copy is kept in config/undo/
(files and folders up to 300 MB; larger ones are noted as not undoable).
The journal keeps the last 50 actions; backups older than 7 days are cleaned.
"""

from __future__ import annotations

from core.paths import DATA_DIR  # owner data lives apart from the program
import json
import shutil
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
UNDO_DIR = DATA_DIR / "config" / "undo"
JOURNAL = UNDO_DIR / "journal.json"
MAX_BACKUP_BYTES = 300 * 1024 * 1024
_lock = threading.RLock()


def _load() -> list[dict]:
    try:
        return json.loads(JOURNAL.read_text(encoding="utf-8")) or []
    except Exception:
        return []


def _save(items: list[dict]) -> None:
    UNDO_DIR.mkdir(parents=True, exist_ok=True)
    JOURNAL.write_text(json.dumps(items[-50:], indent=2, ensure_ascii=False), encoding="utf-8")


def _size(p: Path) -> int:
    if p.is_file():
        return p.stat().st_size
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


def backup(path: str | Path) -> str | None:
    """Copy a file/folder aside before it's changed. Returns the backup path, or None."""
    p = Path(path)
    if not p.exists():
        return None
    try:
        if _size(p) > MAX_BACKUP_BYTES:
            return None
        dest = UNDO_DIR / "files" / uuid.uuid4().hex[:12] / p.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        if p.is_dir():
            shutil.copytree(p, dest)
        else:
            shutil.copy2(p, dest)
        return str(dest)
    except Exception:
        return None


def record(op: str, **data) -> None:
    with _lock:
        items = _load()
        items.append({"id": uuid.uuid4().hex[:8], "op": op, "at": datetime.now().isoformat(timespec="seconds"), **data})
        _save(items)


def _describe(e: dict) -> str:
    n = lambda k: Path(e.get(k, "")).name  # noqa: E731
    return {
        "create": f"created {n('path')}", "write": f"wrote to {n('path')}", "delete": f"deleted {n('path')}",
        "move": f"moved {n('src')} to {Path(e.get('dst', '')).parent.name}", "rename": f"renamed {n('src')} to {n('dst')}",
    }.get(e["op"], e["op"]) + f" ({e['at'][11:16]})"


def list_text(n: int = 8) -> str:
    items = [e for e in _load() if not e.get("undone")][-n:]
    if not items:
        return "Nothing to undo."
    return "Recent file actions I can undo (newest last):\n" + "\n".join(f"- {_describe(e)}" for e in items)


def _undo_one(e: dict) -> str:
    op = e["op"]
    if op == "create":
        p = Path(e["path"])
        if p.exists():
            try:
                import send2trash
                send2trash.send2trash(str(p))
            except Exception:
                return f"Couldn't remove {p.name}."
        return f"Removed {p.name} (it's in the Recycle Bin)."
    if op in ("write", "delete"):
        p, b = Path(e["path"]), e.get("backup")
        if not b or not Path(b).exists():
            return f"I don't have a copy of {p.name} to restore (it was too large or no backup was possible)."
        if p.exists():
            backup(p)  # keep the current version aside too, just in case
            if p.is_dir():
                shutil.rmtree(p)
            else:
                p.unlink()
        p.parent.mkdir(parents=True, exist_ok=True)
        (shutil.copytree if Path(b).is_dir() else shutil.copy2)(b, p)
        return f"Restored {p.name} to how it was."
    if op in ("move", "rename"):
        src, dst = Path(e["src"]), Path(e["dst"])
        if not dst.exists():
            return f"{dst.name} isn't where I left it any more, so I can't move it back."
        if src.exists():
            return f"Something new is already at {src}, so I won't overwrite it."
        src.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(dst), str(src))
        return f"Put {src.name} back where it was."
    return "That action can't be undone."


def undo(count: int = 1) -> str:
    with _lock:
        items = _load()
        todo = [e for e in reversed(items) if not e.get("undone")][:max(1, int(count))]
        if not todo:
            return "Nothing to undo."
        out = []
        for e in todo:
            try:
                out.append(_undo_one(e))
                e["undone"] = True
            except Exception as exc:
                out.append(f"Couldn't undo '{_describe(e)}': {exc}")
        _save(items)
    return " ".join(out)


def cleanup(days: int = 7) -> None:
    root = UNDO_DIR / "files"
    if not root.exists():
        return
    cutoff = time.time() - days * 86400
    for d in root.iterdir():
        try:
            if d.stat().st_mtime < cutoff:
                shutil.rmtree(d, ignore_errors=True)
        except Exception:
            pass


def undo_tool(params: dict) -> str:
    action = str(params.get("action") or "undo").lower()
    if action == "list":
        return list_text()
    return undo(int(params.get("count") or 1))
