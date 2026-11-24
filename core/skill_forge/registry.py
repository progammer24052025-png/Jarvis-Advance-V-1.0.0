"""Where skills live, and the fingerprint lock that keeps approved code unchanged.

skills/<slug>/
    skill.py        the code (must define run(params))
    test_skill.py   tests written from the spec before the code
    manifest.json   spec, permissions, status, build report
    REVIEW.html     the page the owner reads before approving
    history/vN/     every previous version (for rollback)

config/skill_approvals.json is the approval ledger: slug → SHA-256 of the exact
skill.py the owner approved. A skill only runs if the file on disk still hashes
to that value, so any edit after approval (by malware, by mistake, by Jarvis)
blocks it until the owner approves again.
"""

from __future__ import annotations

from core.paths import DATA_DIR  # owner data lives apart from the program
import hashlib
import json
import re
import shutil
import threading
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
SKILLS_DIR = DATA_DIR / "skills"
LEDGER = DATA_DIR / "config" / "skill_approvals.json"
_lock = threading.Lock()


def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")
    return (s or "skill")[:40]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def skill_dir(slug: str) -> Path:
    return SKILLS_DIR / slugify(slug)


def load_manifest(slug: str) -> dict | None:
    try:
        return json.loads((skill_dir(slug) / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        return None


def save_manifest(slug: str, manifest: dict) -> None:
    d = skill_dir(slug)
    d.mkdir(parents=True, exist_ok=True)
    tmp = d / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(d / "manifest.json")


def _ledger() -> dict:
    try:
        return json.loads(LEDGER.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


def _save_ledger(data: dict) -> None:
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(data, indent=2), encoding="utf-8")


def list_skills() -> list[dict]:
    out = []
    if SKILLS_DIR.exists():
        for d in sorted(SKILLS_DIR.iterdir()):
            m = load_manifest(d.name)
            if m:
                out.append(m)
    return out


def approve(slug: str) -> str:
    """Pin the current skill.py. Returns its hash."""
    d = skill_dir(slug)
    with _lock:
        digest = sha256_file(d / "skill.py")
        ledger = _ledger()
        m = load_manifest(slug) or {}
        ledger[slugify(slug)] = {"sha256": digest, "version": m.get("version", 1),
                                 "approved_at": datetime.now().isoformat(timespec="seconds")}
        _save_ledger(ledger)
        m.update(status="approved", sha256=digest, approved_at=ledger[slugify(slug)]["approved_at"])
        save_manifest(slug, m)
    return digest


def revoke(slug: str) -> None:
    with _lock:
        ledger = _ledger()
        ledger.pop(slugify(slug), None)
        _save_ledger(ledger)


def verify(slug: str) -> tuple[bool, str]:
    """True only if the skill is approved AND its code is byte-for-byte what was approved."""
    m = load_manifest(slug)
    if not m:
        return False, "No such skill."
    if m.get("status") != "approved":
        return False, f"Skill '{m.get('name', slug)}' isn't approved (status: {m.get('status')})."
    entry = _ledger().get(slugify(slug))
    if not entry:
        return False, "Skill isn't in the approval ledger. It must be approved again."
    try:
        now = sha256_file(skill_dir(slug) / "skill.py")
    except Exception:
        return False, "Skill code is missing."
    if now != entry["sha256"] or now != m.get("sha256"):
        return False, ("SECURITY: this skill's code changed after you approved it, so it has been blocked. "
                       "Review and approve it again if the change was yours.")
    return True, ""


def skill_is_risky(slug: str) -> bool:
    """Guardian hook: writing files or a declared high risk → needs confirmation in tainted contexts."""
    m = load_manifest(slug)
    if not m:
        return True
    perms = m.get("permissions") or {}
    return bool(perms.get("write_paths")) or str(m.get("risk", "")).lower() == "high"


def snapshot_version(slug: str) -> int:
    """Copy the current files into history/vN before they are replaced."""
    d = skill_dir(slug)
    m = load_manifest(slug) or {}
    v = int(m.get("version", 0) or 0)
    if v and (d / "skill.py").exists():
        h = d / "history" / f"v{v}"
        h.mkdir(parents=True, exist_ok=True)
        for f in ("skill.py", "test_skill.py", "manifest.json", "REVIEW.html"):
            if (d / f).exists():
                shutil.copy2(d / f, h / f)
    return v + 1


def remove(slug: str) -> bool:
    d = skill_dir(slug)
    if not d.exists():
        return False
    revoke(slug)
    trash = SKILLS_DIR / ".removed" / f"{slugify(slug)}_{datetime.now():%Y%m%d_%H%M%S}"
    trash.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(d), str(trash))  # kept, not destroyed: recoverable
    return True
