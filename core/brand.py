"""Fixed brand identity for Jarvis.

DEVELOPER_NAME is the one unchangeable credit. There is deliberately no setting,
UI field, config key or voice command that can change it. Every "made by / about /
developed by" line in the app reads from here, so there is a single source of truth.

The OWNER (the person using a given install) is a separate thing entirely, stored in
each owner's own data (config/identity.json) and asked for in first-run setup. The
owner name is never written here.
"""

from __future__ import annotations

# ── fixed developer credit (do not make this configurable) ──
DEVELOPER_NAME = "Rudraksh Patel"
APP_NAME = "Jarvis"
APP_ID = "RudrakshPatel.Jarvis"          # Windows AppUserModelID (taskbar grouping, toasts)
CREDIT = f"Developed by {DEVELOPER_NAME}"
COPYRIGHT = f"© {DEVELOPER_NAME}. All rights reserved."


def about_line() -> str:
    return f"{APP_NAME} · {CREDIT}"


def who_made_you() -> str:
    return f"I was developed by {DEVELOPER_NAME}."
