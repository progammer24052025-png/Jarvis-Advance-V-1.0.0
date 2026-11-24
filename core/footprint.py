"""Footprint ledger: a list of everything Jarvis creates outside its own folders.

Every piece of code that leaves something on the PC that the installer doesn't know about
(a scheduled task, a firewall rule, a registry key, a file on the Desktop) calls `record()`.
The uninstaller (core/uninstall.py) reads this list, so it removes exactly what Jarvis made.

  • Each entry is signed (HMAC-SHA256 with the DPAPI-protected rules key), so an entry
    written by anything other than Jarvis is reported as unverified, never trusted blindly.
  • Even a verified entry is only a *hint*: the uninstaller still checks every item against
    its own safety rules (right place, really Jarvis's) before removing it.
  • Recording never raises: a failure to write the ledger must not break the feature.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import time
from pathlib import Path

from core.paths import CONFIG_DIR

LEDGER = CONFIG_DIR / "footprint.json"
KINDS = ("task", "firewall", "registry", "file", "folder")
_lock = threading.Lock()


def _key() -> bytes:
    from core.directives import _key as rules_key   # same DPAPI-protected key as the rules file
    return rules_key()


def _sig(kind: str, ident: str, detail: str) -> str:
    return hmac.new(_key(), f"{kind}\0{ident}\0{detail}".encode("utf-8"), hashlib.sha256).hexdigest()


def _read() -> list[dict]:
    try:
        data = json.loads(LEDGER.read_text(encoding="utf-8"))
        return [e for e in data.get("entries", []) if isinstance(e, dict)]
    except Exception:
        return []


def _write(entries: list[dict]) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text(json.dumps({"version": 1, "entries": entries}, indent=1), encoding="utf-8")
    tmp.replace(LEDGER)


def record(kind: str, ident: str | Path, detail: str = "") -> bool:
    """Remember that Jarvis created something. Safe to call repeatedly (no duplicates)."""
    if kind not in KINDS:
        return False
    ident = str(ident)
    try:
        with _lock:
            entries = _read()
            if any(e.get("kind") == kind and e.get("id") == ident for e in entries):
                return True
            entries.append({"kind": kind, "id": ident, "detail": str(detail)[:300],
                            "at": time.strftime("%Y-%m-%dT%H:%M:%S"), "sig": _sig(kind, ident, str(detail)[:300])})
            _write(entries)
        return True
    except Exception as exc:
        print(f"[Footprint] could not record {kind} {ident!r}: {exc}")
        return False


def forget(kind: str, ident: str | Path) -> None:
    """The thing is gone (Jarvis removed it itself): drop it from the ledger."""
    ident = str(ident)
    try:
        with _lock:
            entries = _read()
            keep = [e for e in entries if not (e.get("kind") == kind and e.get("id") == ident)]
            if len(keep) != len(entries):
                _write(keep)
    except Exception:
        pass


def entries() -> list[dict]:
    """All entries, each with "verified": True only if its signature checks out."""
    out = []
    for e in _read():
        kind, ident, detail = str(e.get("kind", "")), str(e.get("id", "")), str(e.get("detail", ""))
        if kind not in KINDS or not ident:
            continue
        try:
            ok = hmac.compare_digest(str(e.get("sig", "")), _sig(kind, ident, detail))
        except Exception:
            ok = False
        out.append({"kind": kind, "id": ident, "detail": detail, "at": e.get("at", ""), "verified": ok})
    return out
