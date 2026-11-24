"""What happens when a jarvis:// link is opened (toast buttons, notification clicks).

Any web page can also open a jarvis:// link, so this handler is deliberately harmless:
it can only SHOW things (the window, the last alert of a kind, a report from the
reports/ folder). It never runs a command, approves a safety hold, or changes settings.
"""

from __future__ import annotations

from core.paths import DATA_DIR  # owner data lives apart from the program
from pathlib import Path
from urllib.parse import parse_qs, urlparse

BASE_DIR = Path(__file__).resolve().parent.parent
REPORTS_DIR = (DATA_DIR / "reports").resolve()
_last: dict[str, tuple[str, str]] = {}   # panel → (title, text), filled by whoever raised the alert


def remember(panel: str, title: str, text: str) -> None:
    """Keep the latest item of a kind so its notification button can show it."""
    _last[panel] = (title, text)


def parse(payload: str) -> dict:
    """'uri:jarvis://open?panel=report&path=…' → {'panel': 'report', 'path': '…'}"""
    uri = payload[4:] if payload.startswith("uri:") else payload
    u = urlparse(uri)
    if u.scheme != "jarvis":
        return {}
    q = {k: v[0] for k, v in parse_qs(u.query).items() if v}
    q["action"] = (u.netloc or u.path.strip("/") or "open").lower()
    return q


def _safe_report(path: str) -> Path | None:
    try:
        p = Path(path).resolve()
        if p.suffix.lower() == ".md" and p.is_file() and REPORTS_DIR in p.parents:
            return p
    except Exception:
        pass
    return None


def handle(payload: str, ui) -> None:
    """Show Jarvis, plus the panel the link asked for. Read-only by design."""
    req = parse(payload)
    try:
        ui.show_main()
    except Exception:
        pass
    if not req:
        return
    panel = req.get("panel", "")
    if panel == "report":
        p = _safe_report(req.get("path", ""))
        if p is not None:
            ui.show_content(p.stem.replace("_", " "), p.read_text(encoding="utf-8")[:20000])
            return
    if panel in _last:
        title, text = _last[panel]
        ui.show_content(title, text)
