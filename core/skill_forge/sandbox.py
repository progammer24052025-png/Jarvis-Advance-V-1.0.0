"""Launches sandbox_runner.py in a separate, stripped-down process."""

from __future__ import annotations

from core.paths import DATA_DIR  # owner data lives apart from the program
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]
RUNNER = Path(__file__).resolve().parent / "sandbox_runner.py"
MARK = "@@SKILL_RESULT@@"
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _python() -> str:
    if getattr(sys, "frozen", False):
        from core.paths import python_exe
        return python_exe()
    venv = BASE_DIR / ".venv" / "Scripts" / "python.exe"
    return str(venv) if venv.exists() else sys.executable


def _clean_env() -> dict:
    """Only what Python needs to start. No API keys, tokens or user variables."""
    keep = ("SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "PATHEXT", "COMSPEC", "NUMBER_OF_PROCESSORS",
            "PROCESSOR_ARCHITECTURE", "OS")
    env = {k: v for k, v in os.environ.items() if k.upper() in keep}
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONNOUSERSITE"] = "1"
    return env


def run_in_sandbox(skill_dir: Path, mode: str, permissions: dict, params: dict | None = None,
                   timeout: int = 60) -> dict:
    """mode: 'test' runs test_skill.py; 'run' calls run(params). Returns the runner's JSON result."""
    workdir = tempfile.mkdtemp(prefix="jarvis_skill_")
    env = _clean_env()
    env["TEMP"] = env["TMP"] = workdir
    payload = json.dumps({"permissions": permissions or {}, "params": params or {}, "jarvis_root": str(BASE_DIR), "data_root": str(DATA_DIR)})
    try:
        proc = subprocess.run(
            [_python(), "-I", "-X", "utf8", str(RUNNER), str(skill_dir), mode, workdir],
            input=payload, capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, cwd=workdir, env=env, creationflags=_NOWIN,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"Timed out after {timeout}s (the skill was stopped)."}
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    for line in reversed((proc.stdout or "").splitlines()):
        if line.startswith(MARK):
            try:
                return json.loads(line[len(MARK):])
            except Exception:
                break
    return {"ok": False, "error": "The skill crashed before reporting a result.",
            "stderr": (proc.stderr or "")[-1500:]}
