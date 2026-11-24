"""The OWNER of this install: the person Jarvis serves (you on your PC, your friend on theirs).

Not to be confused with the developer credit in core/brand.py, which is fixed.
The owner's name lives only in their own data folder (config/identity.json), is asked for in
first-run setup and can be changed any time. Nothing here has a built-in name, so a fresh
install never shows anyone else's.
"""

from __future__ import annotations

import json

from core.paths import CONFIG_DIR

IDENTITY_FILE = CONFIG_DIR / "identity.json"


def _owner() -> dict:
    try:
        data = json.loads(IDENTITY_FILE.read_text(encoding="utf-8-sig"))
        owner = data.get("owner") if isinstance(data, dict) else None
        return owner if isinstance(owner, dict) else {}
    except Exception:
        return {}


def owner_name(default: str = "") -> str:
    """Full name, for document authors and signatures."""
    o = _owner()
    return (str(o.get("name") or "").strip() or str(o.get("preferred_name") or "").strip() or default)


def preferred_name(default: str = "") -> str:
    """How Jarvis addresses the owner (e.g. "Alex", "Alex Sir")."""
    o = _owner()
    return (str(o.get("preferred_name") or "").strip() or str(o.get("name") or "").strip() or default)


def greeting_name(default: str = "") -> str:
    """Short, friendly first name for "Good evening, …" greetings."""
    name = preferred_name() or owner_name()
    if name:
        return name.split()[0]
    return default


def is_set_up() -> bool:
    return bool(owner_name())
