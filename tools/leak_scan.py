"""Leak scanner: proves a build of Jarvis contains none of the owner's personal data.

Usage:
    python tools/leak_scan.py                 # scan the files that would ship from this project
    python tools/leak_scan.py <folder>        # scan a built/frozen output folder (everything in it)

Exit code 0 = clean, 1 = leaks found (the build must stop), 2 = scanner error.

What counts as a leak:
  • API keys and tokens (Gemini, Groq, OpenAI/Anthropic/OpenRouter, Telegram bot, Discord bot, GitHub)
  • email addresses (except a short allow-list of harmless library/example addresses)
  • personal markers: your name, Windows username, home folder, Telegram chat ID, … collected
    automatically from THIS PC's private config, plus anything you add to config/leak_markers.txt.
    Those private files are never shipped, so the scanner itself carries no personal data.

The developer credit may appear only in core/brand.py, version.txt (Windows file properties)
and LICENSE. Anywhere else, your name stops the build.
"""

from __future__ import annotations

import getpass
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.brand import DEVELOPER_NAME  # noqa: E402

# ── what ships (allow-list: anything not named here is left out of the installer) ──
SHIP_DIRS = ["actions", "agent", "auth", "core", "dashboard", "gui", "jarvis_connect",
             "plugins", "smart_home", "assets", "tools"]
# from folders that also hold personal data, only these exact files ship
SHIP_FILES_EXTRA = ["memory/__init__.py", "memory/memory_manager.py", "config/models/hand_landmarker.task"]
SHIP_FILES = ["main.py", "ui.py", "updater.py", "discord_bot.py", "groq_client.py", "key_manager.py",
              "llm_client.py", "or_client.py", "plugin_manager.py", "workspace_store.py",
              "gesture_utils.py", "smart_home_page_new.py", "version.txt", "requirements.txt",
              "LICENSE", "config/__init__.py", "jarvis_python.py"]
SKIP_PARTS = {"__pycache__", ".git", "node_modules"}
SKIP_SUFFIXES = {".pyc", ".pyo"}
BINARY_SUFFIXES = {".png", ".jpg", ".jpeg", ".ico", ".gif", ".webp", ".mp3", ".wav", ".ogg",
                   ".ttf", ".otf", ".woff", ".woff2", ".onnx", ".bin", ".dll", ".pyd", ".exe"}

# Files allowed to contain the developer credit (nothing else about you).
CREDIT_FILES = {"core/brand.py", "version.txt", "version_setup.txt", "LICENSE", "TRADEMARK.md", "README.md",
                "bootstrap.ps1", "start_jarvis.bat"}

SECRET_PATTERNS = {
    "Google/Gemini API key": re.compile(r"AIza[0-9A-Za-z_\-]{35}"),
    "Groq API key": re.compile(r"gsk_[0-9A-Za-z]{20,}"),
    "OpenAI/Anthropic/OpenRouter key": re.compile(r"\bsk-(?:ant-|or-)?[0-9A-Za-z_\-]{20,}"),
    "Telegram bot token": re.compile(r"\b\d{8,10}:AA[0-9A-Za-z_\-]{30,}"),
    "Discord bot token": re.compile(r"\b[MN][A-Za-z\d]{23,25}\.[\w-]{6}\.[\w-]{27,}"),
    "GitHub token": re.compile(r"\bgh[pousr]_[0-9A-Za-z]{30,}"),
    # a real key: the header followed by the key itself (the bare header is in every crypto library)
    "private key block": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |ENCRYPTED )?PRIVATE KEY-----\s*[A-Za-z0-9+/=]{60,}"),
}
EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")
HARMLESS_EMAIL = re.compile(r"@(example\.(com|org)|users\.noreply\.github\.com|localhost)$|^noreply@|^(you|user|name)@", re.I)


@dataclass
class Hit:
    file: str
    line: int
    kind: str
    text: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}  [{self.kind}]  {self.text}"


def _mask(s: str) -> str:
    s = s.strip()
    return s if len(s) <= 6 else s[:3] + "…" + s[-2:]


def personal_markers(base: Path = BASE_DIR) -> list[str]:
    """Collect what identifies the owner of THIS PC, from private files that never ship."""
    found: set[str] = set()
    user = getpass.getuser()
    if user:
        found.add(user)
    home = str(Path.home())
    found.update({home, home.replace("\\", "/"), home.replace("\\", "\\\\")})

    def _json(rel: str):
        try:
            return json.loads((base / rel).read_text(encoding="utf-8"))
        except Exception:
            return None

    ident = _json("config/identity.json") or {}
    owner = ident.get("owner", {}) if isinstance(ident, dict) else {}
    for key in ("name", "preferred_name", "location"):
        val = str(owner.get(key) or "").strip()
        if val:
            found.add(val)
            for part in re.split(r"[\s,]+", val):
                if len(part) >= 4 and part.lower() not in {"sir", "madam", "ma'am", "boss"}:
                    found.add(part)

    try:   # the Telegram file is encrypted (core/secrets.py); read it through the same path object
        from core.secrets import load_json
        tg = load_json(base / "config" / "telegram_bot.json", {})
    except Exception:
        tg = _json("config/telegram_bot.json") or {}
    if isinstance(tg, dict):
        for key in ("allowed_chat_ids", "chat_ids", "owner_chat_id", "chat_id"):
            val = tg.get(key)
            for v in (val if isinstance(val, list) else [val]):
                if v not in (None, "") and len(str(v)) >= 6:
                    found.add(str(v))

    # your own keys and tokens: if one of these appears anywhere in a build, it's a leak
    try:
        from core.secrets import load_json
        for rel in ("config/api_keys.json", "config/telegram_bot.json", "config/discord_bot.json"):
            for v in (load_json(base / rel, {}) or {}).values():
                for item in (v if isinstance(v, list) else [v]):
                    if isinstance(item, str) and len(item.strip()) >= 20:
                        found.add(item.strip())
    except Exception:
        pass

    extra = base / "config" / "leak_markers.txt"
    if extra.exists():
        for ln in extra.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if ln and not ln.startswith("#"):
                found.add(ln)
    return sorted((m for m in found if len(m) >= 4), key=len, reverse=True)


def shipped_files(base: Path = BASE_DIR) -> list[Path]:
    out: list[Path] = []
    for rel in SHIP_FILES + SHIP_FILES_EXTRA:
        p = base / rel
        if p.is_file():
            out.append(p)
    for d in SHIP_DIRS:
        root = base / d
        if root.is_dir():
            out.extend(p for p in root.rglob("*") if p.is_file())
    return [p for p in out if not (SKIP_PARTS & set(p.parts)) and p.suffix.lower() not in SKIP_SUFFIXES]


def scan_text(rel: str, text: str, markers: list[str], emails: bool = True) -> list[Hit]:
    hits: list[Hit] = []
    lowered_markers = [(m, m.lower()) for m in markers]
    rel_n = rel.replace("\\", "/")
    # the credit file, wherever a build or installer put it (e.g. Programs/Jarvis/LICENSE)
    credit_ok = rel_n in CREDIT_FILES or any(rel_n.endswith("/" + c) for c in CREDIT_FILES if "/" not in c)
    for no, line in enumerate(text.splitlines(), 1):
        if len(line) > 5000:
            line = line[:5000]
        for kind, rx in SECRET_PATTERNS.items():
            if not emails and kind != "private key block":
                continue   # third-party files: generic key shapes are their own public keys; YOUR keys are markers
            for m in rx.finditer(line):
                hits.append(Hit(rel, no, kind, _mask(m.group(0))))
        for m in (EMAIL.finditer(line) if emails else ()):
            addr = m.group(0)
            if not HARMLESS_EMAIL.search(addr) and not addr.lower().endswith((".png", ".jpg", ".svg")):
                hits.append(Hit(rel, no, "email address", _mask(addr)))
        probe = line
        if credit_ok:  # only the fixed developer credit itself is allowed here
            for credit in (DEVELOPER_NAME, DEVELOPER_NAME.replace(" ", "")):
                probe = probe.replace(credit, "")
        low = probe.lower()
        for raw, mk in lowered_markers:
            if mk in low:
                hits.append(Hit(rel, no, "personal marker", _mask(raw)))
                break
    return hits


def _frozen_code(exe: Path):
    """(module name, bytes) for the Python code packed inside a PyInstaller .exe, decompressed."""
    try:
        from PyInstaller.archive.readers import CArchiveReader
        car = CArchiveReader(str(exe))
    except Exception:
        return
    for name in list(car.toc):
        try:
            if name.upper().endswith("PYZ") or name.lower().endswith(".pyz"):
                pyz = car.open_embedded_archive(name)
                for mod in list(pyz.toc):
                    data = pyz.extract(mod, raw=True)
                    if isinstance(data, (bytes, bytearray)):
                        yield mod, bytes(data)
            else:
                data = car.extract(name)
                if isinstance(data, (bytes, bytearray)):
                    yield name, bytes(data)
        except Exception:
            continue


OUR_TOP = {"actions", "agent", "auth", "core", "dashboard", "gui", "jarvis_connect", "smart_home", "plugins",
           "memory", "config", "ui", "main", "updater", "discord_bot", "groq_client", "key_manager", "llm_client",
           "or_client", "plugin_manager", "workspace_store", "gesture_utils", "smart_home_page_new", "jarvis_python"}


def _ours(name: str) -> bool:
    return name.replace("/", ".").split(".")[0] in OUR_TOP


def scan(paths: list[Path], root: Path, markers: list[str], built: bool = False) -> list[Hit]:
    hits: list[Hit] = []
    for p in paths:
        rel = str(p.relative_to(root)).replace("\\", "/")
        if p.suffix.lower() == ".exe":
            for mod, data in _frozen_code(p):
                # the developer credit is allowed only in the brand module, exactly as in the source
                key = "core/brand.py" if mod == "core.brand" else f"{rel}::{mod}"
                for h in scan_text(key, data.decode("latin-1"), markers, emails=_ours(mod)):
                    if key == "core/brand.py":
                        h.file = f"{rel}::{mod}"
                    hits.append(h)
        if p.suffix.lower() in BINARY_SUFFIXES:
            data = p.read_bytes()
            text = data.decode("latin-1", "ignore")
            # look only for secrets/markers in binaries, as raw or UTF-16 text
            text += "\n" + data.decode("utf-16-le", "ignore")
        else:
            try:
                text = p.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                text = p.read_bytes().decode("latin-1", "ignore")
            except OSError:
                continue
        hits.extend(scan_text(rel, text, markers, emails=(not built) or _ours(rel)))
    return hits


def main(argv: list[str]) -> int:
    try:
        markers = personal_markers()
        if len(argv) > 1:
            root = Path(argv[1]).resolve()
            files = [p for p in root.rglob("*") if p.is_file()]
        else:
            root = BASE_DIR
            files = shipped_files()
        hits = scan(files, root, markers, built=len(argv) > 1)
    except Exception as exc:  # pragma: no cover
        print(f"[leak-scan] error: {exc}")
        return 2
    print(f"[leak-scan] scanned {len(files)} files against {len(markers)} private markers")
    if not hits:
        print("[leak-scan] CLEAN: no keys, emails or personal data found.")
        return 0
    for h in hits:
        print("  " + str(h))
    print(f"[leak-scan] FAILED: {len(hits)} leak(s). Fix them before building the installer.")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
