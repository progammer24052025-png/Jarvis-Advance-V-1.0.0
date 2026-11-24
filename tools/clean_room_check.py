"""Clean-room check of a built MSI, without installing it over your own Jarvis.

    .venv\\Scripts\\python.exe tools\\clean_room_check.py dist\\Jarvis-1.0.0.msi

  1. unpacks the MSI with an administrative install (msiexec /a): files only, nothing registered
  2. leak-scans every unpacked file (including the code inside the .exe files)
  3. checks the payload: both executables present, no config/memory/skills/logs/reports data folders,
     no key or token files anywhere
  4. prints how to do the final check on a fresh Windows account or your friend's PC
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from tools import leak_scan  # noqa: E402

FORBIDDEN_NAMES = {"api_keys.json", "telegram_bot.json", "discord_bot.json", "identity.json", "app_settings.json",
                   "directives.json", ".rules_key", "long_term.json", "chat_history.json", "session_transcript.json",
                   "security_baseline.json", "routines.json", "skill_approvals.json", "leak_markers.txt",
                   "workspace_store.sqlite3", "smart_home.sqlite3", "smart_home.key", "ig_session.json"}


def main(msi: str) -> int:
    msi_path = Path(msi).resolve()
    out = Path(tempfile.mkdtemp(prefix="jarvis_cleanroom_"))
    print(f"[clean-room] unpacking {msi_path.name} → {out}")
    r = subprocess.run(["msiexec", "/a", str(msi_path), "/qn", f"TARGETDIR={out}", "/L*v", str(out / "unpack.log")])
    if r.returncode != 0:
        print(f"[clean-room] FAILED: msiexec /a returned {r.returncode}")
        return 1
    exes = list(out.rglob("Jarvis.exe")) + list(out.rglob("jarvis-python.exe"))
    problems = []
    if len(exes) < 2:
        problems.append("Jarvis.exe or jarvis-python.exe is missing from the MSI")
    for p in out.rglob("*"):
        if p.is_file() and p.name.lower() in FORBIDDEN_NAMES:
            problems.append(f"personal file shipped: {p.relative_to(out)}")
    app = exes[0].parent if exes else out
    for d in ("memory", "skills", "logs", "reports", "analysis"):
        dd = app / d
        if dd.exists() and any(x.suffix != ".py" and x.name != "__init__.py" for x in dd.rglob("*") if x.is_file()):
            problems.append(f"data folder shipped: {d}/")
    files = [p for p in out.rglob("*") if p.is_file() and p.name != "unpack.log" and p.suffix.lower() != ".msi"]
    hits = leak_scan.scan(files, out, leak_scan.personal_markers(), built=True)
    for h in hits:
        problems.append(str(h))
    print(f"[clean-room] {len(files)} files checked")
    if problems:
        print("[clean-room] FAILED:")
        for p in problems[:50]:
            print("   " + p)
        return 1
    print("[clean-room] CLEAN: no personal files, keys or markers in the MSI payload.")
    print(f"[clean-room] unpacked app: {app}")
    print("[clean-room] Final check: install the MSI on a fresh Windows account (or your friend's PC). "
          "It must open the setup wizard and show no name, keys, memory or rules.")
    if "--keep" not in sys.argv:
        shutil.rmtree(out, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
