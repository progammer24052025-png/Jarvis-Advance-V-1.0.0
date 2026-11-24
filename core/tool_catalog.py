"""Tool Catalog: what every Jarvis tool can do, so the owner's rules can talk about them.

Built from the live tool declarations (main.TOOL_DECLARATIONS) plus a small hand-checked table of
facts the declarations don't carry: is it outbound (leaves the PC), reversible, which argument
names hold its "target" (app, contact, site, file), and a plain-words category.

Approved Skill Forge skills are added automatically (category "skill", risk from their permissions).
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field

from core.paths import SKILLS_DIR


@dataclass
class ToolInfo:
    name: str
    description: str = ""
    params: tuple[str, ...] = ()
    category: str = "other"
    risk: str = "low"                  # low | medium | high
    outbound: bool = False             # sends something off this PC
    reversible: bool = True
    targets: tuple[str, ...] = ()      # argument names that say what the tool acts on
    aliases: tuple[str, ...] = ()      # words the owner may use for it

    def summary(self) -> str:
        bits = [self.category, f"{self.risk} risk"]
        if self.outbound:
            bits.append("sends things off this PC")
        if not self.reversible:
            bits.append("can't be undone")
        return f"{self.name.replace('_', ' ')} ({', '.join(bits)})"


# name: (category, outbound, reversible, target args, aliases)
_META: dict[str, tuple[str, bool, bool, tuple[str, ...], tuple[str, ...]]] = {
    "send_message":         ("messaging", True, False, ("receiver", "platform"), ("message", "messages", "whatsapp", "sms", "text")),
    "instagram_reply":      ("messaging", True, False, ("action",), ("instagram", "insta", "dm")),
    "check_instagram_messages": ("messaging", False, True, (), ("instagram", "insta")),
    "open_app":             ("apps", False, True, ("app_name",), ("app", "apps", "open")),
    "browser_control":      ("web", False, True, ("url", "query", "action"), ("browser", "chrome", "edge", "website", "web")),
    "web_search":           ("web", False, True, ("query",), ("search", "google", "web")),
    "youtube_video":        ("entertainment", False, True, ("query", "url"), ("youtube", "video", "videos")),
    "spotify_controller":   ("entertainment", False, True, ("query", "action"), ("spotify", "music", "song")),
    "game_updater":         ("entertainment", False, True, ("game_name", "platform"), ("game", "games", "steam", "epic")),
    "file_controller":      ("files", False, True, ("path", "destination", "target", "action"), ("file", "files", "folder")),
    "file_processor":       ("files", False, True, ("file_path", "action"), ("file", "files")),
    "word_document":        ("documents", False, True, ("output_path", "file_path"), ("word", "document", "letter", "docx")),
    "pdf_document":         ("documents", False, True, ("output_path", "file_path"), ("pdf", "document")),
    "presentation_builder": ("documents", False, True, ("output_path",), ("ppt", "presentation", "slides")),
    "spreadsheet_builder":  ("documents", False, True, ("output_path",), ("excel", "spreadsheet", "sheet")),
    "computer_control":     ("pc control", False, False, ("action", "title", "text"), ("mouse", "keyboard", "type", "click")),
    "computer_settings":    ("pc settings", False, True, ("action",), ("volume", "brightness", "shutdown", "restart", "settings")),
    "desktop_control":      ("pc control", False, True, ("action", "path"), ("desktop", "wallpaper")),
    "system_manager":       ("pc control", False, False, ("action", "process_name"), ("process", "kill", "task manager")),
    "dev_agent":            ("agents", False, True, ("project_name",), ("coding agent", "code", "project")),
    "agent_task":           ("agents", False, True, ("goal",), ("agent", "task", "multi-step")),
    "background_task":      ("agents", False, True, ("topic", "action"), ("research", "background")),
    "screen_process":       ("vision", False, True, ("angle",), ("screen", "camera", "webcam")),
    "clipboard_processor":  ("vision", False, True, (), ("clipboard",)),
    "smart_home_control":   ("smart home", False, True, ("command",), ("lights", "fan", "smart home", "ac")),
    "unlock_device":        ("devices", False, False, ("target",), ("phone", "unlock")),
    "connect_execute":      ("devices", True, False, ("device", "target", "action"), ("phone", "device")),
    "connect_pair_device":  ("devices", False, True, ("device_name",), ("pair",)),
    "connect_list_devices": ("devices", False, True, (), ("devices",)),
    "connect_get_device":   ("devices", False, True, ("device", "target"), ("device",)),
    "connect_get_capabilities": ("devices", False, True, ("device", "target"), ("device",)),
    "connect_disconnect_device": ("devices", False, True, ("device", "target"), ("disconnect",)),
    "reminder":             ("planning", False, True, ("message",), ("reminder", "remind")),
    "calendar_scheduler":   ("planning", False, True, ("title", "action"), ("calendar", "meeting", "event")),
    "routine_manager":      ("planning", False, True, ("name", "action"), ("routine", "routines")),
    "flight_finder":        ("web", False, True, ("origin", "destination"), ("flight", "flights")),
    "weather_report":       ("info", False, True, ("city",), ("weather",)),
    "daily_briefing":       ("info", False, True, ("category",), ("briefing", "news")),
    "website_analyzer":     ("web", False, True, ("url",), ("analyze website", "reverse engineer")),
    "security_check":       ("security", False, True, ("action",), ("security",)),
    "skill_forge":          ("skills", False, True, ("request", "skill"), ("skill forge", "build skill")),
    "run_skill":            ("skills", False, True, ("skill",), ("skill", "skills")),
    "save_memory":          ("memory", False, True, ("category", "key"), ("memory", "remember")),
    "undo_last":            ("files", False, True, (), ("undo",)),
    "quiet_hours":          ("owner control", False, True, (), ("quiet hours",)),
    "owner_rules":          ("owner control", False, True, ("action",), ("rules", "silent", "focus")),
    "shutdown_Jarvis":      ("owner control", False, True, (), ("shut down jarvis", "quit")),
    "background_monitor":   ("monitoring", False, True, ("target", "type"), ("monitor", "watch")),
}

# Tools pulled in by agent_task's executor that aren't top-level declarations.
_AGENT_ONLY = {
    "cmd_control": ("pc control", False, False, ("command",), ("command prompt", "cmd", "terminal")),
    "calorie_counter": ("info", False, True, (), ()),
    "pushup_counter": ("info", False, True, (), ()),
    "system_monitor": ("info", False, True, (), ()),
    "upload_video": ("entertainment", True, False, ("platform",), ("upload",)),
    "code_helper": ("agents", False, True, (), ("code",)),
    "generated_code": ("agents", False, True, (), ("code",)),
}

_catalog: dict[str, ToolInfo] = {}


def _risk(name: str) -> str:
    try:
        from core.guardian import HIGH_RISK_ACTIONS, HIGH_RISK_TOOLS
        if name in HIGH_RISK_TOOLS:
            return "high"
        if name in HIGH_RISK_ACTIONS:
            return "medium"
    except Exception:
        pass
    return "low"


def _make(name: str, description: str = "", params: tuple[str, ...] = ()) -> ToolInfo:
    cat, out, rev, targets, aliases = _META.get(name) or _AGENT_ONLY.get(name) or ("other", False, True, (), ())
    return ToolInfo(name=name, description=description, params=params, category=cat, risk=_risk(name),
                    outbound=out, reversible=rev, targets=targets, aliases=aliases)


def register_declarations(declarations: list[dict]) -> None:
    for d in declarations or []:
        try:
            props = tuple((d.get("parameters") or {}).get("properties", {}).keys())
            _catalog[d["name"]] = _make(d["name"], d.get("description", ""), props)
        except Exception:
            continue
    for name in _AGENT_ONLY:
        _catalog.setdefault(name, _make(name))
    _catalog.setdefault("owner_rules", _make("owner_rules"))
    _register_skills()


def _register_skills() -> None:
    try:
        import json
        for d in SKILLS_DIR.iterdir():
            m = d / "manifest.json"
            if not m.exists():
                continue
            meta = json.loads(m.read_text(encoding="utf-8"))
            if meta.get("status") != "approved":
                continue
            perms = meta.get("permissions") or {}
            name = f"skill:{meta.get('name', d.name)}"
            _catalog[name] = ToolInfo(
                name=name, description=meta.get("description", ""), category="skill",
                risk="high" if perms.get("write_paths") or perms.get("network_domains") else "low",
                outbound=bool(perms.get("network_domains")), reversible=not perms.get("write_paths"),
                targets=(), aliases=(meta.get("name", d.name),))
    except Exception:
        pass


def get(name: str) -> ToolInfo:
    if not _catalog:
        _bootstrap()
    return _catalog.get(name) or _make(name)


def all_tools() -> list[ToolInfo]:
    if not _catalog:
        _bootstrap()
    return sorted(_catalog.values(), key=lambda t: (t.category, t.name))


def _bootstrap() -> None:
    for name in list(_META) + list(_AGENT_ONLY):
        _catalog.setdefault(name, _make(name))


def target_values(name: str, args: dict) -> list[str]:
    """The concrete things a call acts on (app name, contact, url, path…), lower-cased."""
    info = get(name)
    keys = info.targets or tuple(args.keys())
    vals = []
    for k in keys:
        v = args.get(k)
        if isinstance(v, (str, int, float)) and str(v).strip():
            vals.append(str(v).strip().lower())
    return vals


def matches_tool(pattern: str, name: str) -> bool:
    """'send_message', 'open_app|youtube_video', 'connect_*', a category ('messaging') or an alias ('whatsapp')."""
    info = get(name)
    for p in (pattern or "").lower().split("|"):
        p = p.strip()
        if not p:
            continue
        if p == "*" or fnmatch.fnmatch(name.lower(), p) or p == info.category or p in info.aliases:
            return True
        if p.startswith("category:") and p[9:] == info.category:
            return True
    return False


def find(query: str) -> list[ToolInfo]:
    q = (query or "").lower().strip()
    if not q:
        return all_tools()
    out = []
    for t in all_tools():
        hay = " ".join([t.name, t.category, " ".join(t.aliases), t.description]).lower()
        if any(w in hay for w in q.split() if len(w) > 2):
            out.append(t)
    return out


def describe(query: str = "") -> str:
    tools = find(query)
    if not tools:
        return f"I don't have a tool for '{query}'."
    by_cat: dict[str, list[str]] = {}
    for t in tools:
        by_cat.setdefault(t.category, []).append(t.summary())
    lines = [f"{cat.title()}: " + "; ".join(items) for cat, items in sorted(by_cat.items())]
    return "\n".join(lines)
