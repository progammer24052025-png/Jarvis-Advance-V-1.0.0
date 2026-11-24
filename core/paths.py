"""Where Jarvis keeps its program files vs. the owner's data.

  PROGRAM_DIR  the code, assets and orb files (read-only once installed)
  DATA_DIR     the owner's own data: config, keys, memory, skills, reports, logs, tasks, undo
  CACHE_DIR    throw-away output: website analysis, temp files

Running from the project folder (development, i.e. your PC today), DATA_DIR is the project
folder itself, so nothing moves and nothing changes for you.

An installed copy (frozen .exe) keeps data in the owner's own %APPDATA%\\Jarvis and cache in
%LOCALAPPDATA%\\Jarvis. That's what keeps every install separate: the MSI only ever contains
PROGRAM_DIR, so no one's data can travel with it.

JARVIS_DATA_DIR (environment variable) overrides DATA_DIR, which tests and clean-room checks use.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

PROGRAM_DIR = Path(__file__).resolve().parent.parent
FROZEN = bool(getattr(sys, "frozen", False))


def _data_dir() -> Path:
    override = os.environ.get("JARVIS_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if FROZEN:
        return Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming") / "Jarvis"
    return PROGRAM_DIR


def _cache_dir() -> Path:
    override = os.environ.get("JARVIS_DATA_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve() / "cache"
    if FROZEN:
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "Jarvis"
    return PROGRAM_DIR


DATA_DIR = _data_dir()
CACHE_DIR = _cache_dir()

CONFIG_DIR = DATA_DIR / "config"
MEMORY_DIR = DATA_DIR / "memory"
SKILLS_DIR = DATA_DIR / "skills"
REPORTS_DIR = DATA_DIR / "reports"
LOGS_DIR = DATA_DIR / "logs"
ANALYSIS_DIR = CACHE_DIR / "analysis"
DOWNLOADS_DIR = DATA_DIR / "downloads"

IS_SEPARATE_DATA = DATA_DIR != PROGRAM_DIR


def python_exe(windowless: bool = False) -> str:
    """The Python to run helper scripts with. In an installed copy that's jarvis-python.exe (never
    Jarvis.exe itself, which would start a second Jarvis)."""
    if FROZEN:
        return str(PROGRAM_DIR / "jarvis-python.exe")
    exe = Path(sys.executable)
    if windowless and exe.name.lower() == "python.exe" and exe.with_name("pythonw.exe").exists():
        return str(exe.with_name("pythonw.exe"))
    return str(exe)


def ensure_data_dirs() -> Path:
    """Create the owner's data folders if they don't exist yet (first run of an install)."""
    for d in (CONFIG_DIR, MEMORY_DIR, SKILLS_DIR, REPORTS_DIR, LOGS_DIR, ANALYSIS_DIR):
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    if IS_SEPARATE_DATA:   # installed copy: give it the bundled hand-tracking model on first run
        src = PROGRAM_DIR / "config" / "models" / "hand_landmarker.task"
        dst = CONFIG_DIR / "models" / "hand_landmarker.task"
        try:
            if src.exists() and not dst.exists():
                import shutil
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
        except OSError:
            pass
    return DATA_DIR
