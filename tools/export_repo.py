"""Make a clean copy of the project for GitHub: allow-list only, then leak-scan it.

    .venv\\Scripts\\python.exe tools\\export_repo.py <output folder>

Copies ONLY: the app files the installer ships (tools/leak_scan.SHIP_*), plus tests, docs, the build tools,
root documents/launchers, and the Android companion's source (no build output, no local.properties).
Never copies: config data, keys, memory, logs, reports, skills, analysis, extra/, caches, .venv, IDE folders.
Exit code 1 if the leak scanner finds anything in the copy.
"""

from __future__ import annotations

import fnmatch
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools import leak_scan  # noqa: E402

EXTRA_DIRS = ["tests", "docs"]
EXTRA_FILES = ["README.md", "LICENSE", "CODE_OF_CONDUCT.md", "CONTRIBUTING.md", "SECURITY.md", "TRADEMARK.md",
               "SECURITY_ENHANCEMENT_PLAN.md", "requirements.txt", "version.txt", "version_setup.txt", "setup.py",
               "bootstrap.ps1", "start_jarvis.bat", "start_jarvis.vbs", "pyrightconfig.json", ".gitignore",
               "jarvis_python.py"]
ANDROID = "jarvis-connect-android"
ANDROID_SKIP = ["*/build/*", "*/.gradle/*", "*/.idea/*", "*/.cxx/*", "*/captures/*", "local.properties", "*.apk", "*.aab",
                "*.jks", "*.keystore", "*/release/*"]
SKIP_ALWAYS = ["*/__pycache__/*", "*.pyc", "*.log", "*.sqlite3*", "*.db"]
# internal planning notes: written about this PC (paths, account names), so they stay local
LOCAL_ONLY = ["docs/DESKTOP_APP_PLAN.md", "docs/MSI_INSTALLER_PLAN.md", "docs/RUNTIME_DIRECTIVES_PLAN.md"]


def _skip(rel: str, patterns: list[str]) -> bool:
    r = "/" + rel.replace("\\", "/")
    return any(fnmatch.fnmatch(r, "*" + p if p.startswith("/") else ("*/" + p if "/" not in p else p)) or
               fnmatch.fnmatch(Path(rel).name, p) for p in patterns)


def collect() -> list[Path]:
    files = set(leak_scan.shipped_files(ROOT))
    for d in EXTRA_DIRS:
        files.update(p for p in (ROOT / d).rglob("*") if p.is_file())
    for f in EXTRA_FILES:
        if (ROOT / f).is_file():
            files.add(ROOT / f)
    android = ROOT / ANDROID
    if android.is_dir():
        files.update(p for p in android.rglob("*") if p.is_file()
                     and not _skip(str(p.relative_to(ROOT)), ANDROID_SKIP))
    return sorted(p for p in files if not _skip(str(p.relative_to(ROOT)), SKIP_ALWAYS)
                  and str(p.relative_to(ROOT)).replace("\\", "/") not in LOCAL_ONLY)


def main(out: str) -> int:
    dest = Path(out).resolve()
    if dest.exists() and any(p.name != ".git" for p in dest.iterdir()):
        for p in dest.iterdir():
            if p.name == ".git":
                continue
            shutil.rmtree(p) if p.is_dir() else p.unlink()
    dest.mkdir(parents=True, exist_ok=True)
    files = collect()
    for p in files:
        target = dest / p.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target)
    copied = [p for p in dest.rglob("*") if p.is_file() and ".git" not in p.parts]
    print(f"[export] copied {len(copied)} files ({sum(p.stat().st_size for p in copied) / 1e6:.1f} MB) to {dest}")
    hits = leak_scan.scan(copied, dest, leak_scan.personal_markers())
    if hits:
        for h in hits[:80]:
            print("  " + str(h))
        print(f"[export] FAILED: {len(hits)} leak(s) in the copy. Nothing should be pushed.")
        return 1
    print("[export] CLEAN: no keys, emails or personal data in the copy.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
