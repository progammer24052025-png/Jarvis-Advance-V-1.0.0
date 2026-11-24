"""Uninstall cleanup: remove everything Jarvis made on this PC, and nothing else.

    python -m core.uninstall --dry-run                 # list what would be removed (changes nothing)
    python -m core.uninstall --cleanup --keep-data     # remove all traces, keep the owner's data
    python -m core.uninstall --cleanup --remove-data --confirm DELETE   # remove everything

The installed uninstaller (Jarvis-Uninstall.exe) and the MSI's safety-net step call this.

Safety rules (docs/UNINSTALLER_PLAN.md §2), enforced here, again right before every delete:
  • allow-list only: fixed known places + Jarvis's own footprint ledger; never a disk search
  • folders: must be named "Jarvis" and sit in their expected parent; never a drive root, the
    user folder, Desktop, Documents, Windows or Program Files; links are removed, never followed
  • a development copy (data inside the project folder) is never deleted
  • tasks, firewall rules, registry entries are removed only if they really belong to Jarvis
  • the owner's own files (Desktop outputs) only if ticked; owner data only on "remove everything"
  • only Jarvis's own processes are stopped; admin is asked for only when actually needed
  • every step is independent; a failure is reported and the rest carries on
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import stat
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

from core.brand import APP_ID

JARVIS_EXES = {"jarvis.exe", "jarvis-python.exe"}
TASK_PREFIXES = ("JarvisReminder_", "JarvisAI_")
FIREWALL_RE = re.compile(r"^Jarvis Dashboard (Port \d{1,5}|Python)$")
TEMP_PATTERNS = ("Jarvis_edge_tts_*.mp3", "JarvisReminder_*.pyw", "JarvisReminder_*.xml", "Jarvis_fw_*.bat")
DESKTOP_FILES = ("jarvis_debug_*.png", "jarvis_screenshot.png", "jarvis_calendar.ics")
DESKTOP_FOLDERS = ("JarvisAI", "JarvisProjects")
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
STARTUP_APPROVED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run"
URI_KEY = r"Software\Classes\jarvis"
OWN_KEYS = (rf"Software\Classes\AppUserModelId\{APP_ID}",
            rf"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\{APP_ID}")
REPORT_NAME = "Jarvis-uninstall-report.txt"
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ── where things are ─────────────────────────────────────────────────────────
@dataclass
class Env:
    home: Path
    appdata: Path
    localappdata: Path
    temp: Path
    program_dir: Path
    data_dir: Path
    cache_dir: Path
    desktop: Path = None  # type: ignore[assignment]
    documents: Path = None  # type: ignore[assignment]

    def __post_init__(self):
        self.desktop = self.desktop or self.home / "Desktop"
        self.documents = self.documents or self.home / "Documents"

    @classmethod
    def current(cls) -> "Env":
        from core import paths
        home = Path.home()
        return cls(home=home,
                   appdata=Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming"),
                   localappdata=Path(os.environ.get("LOCALAPPDATA") or home / "AppData" / "Local"),
                   temp=Path(os.environ.get("TEMP") or os.environ.get("TMP") or home / "AppData" / "Local" / "Temp"),
                   program_dir=paths.PROGRAM_DIR, data_dir=paths.DATA_DIR, cache_dir=paths.CACHE_DIR)


def _n(p) -> str:
    return os.path.normcase(os.path.abspath(str(p))).rstrip("\\/")


def _under(child, parent) -> bool:
    c, p = _n(child), _n(parent)
    return c == p or c.startswith(p + os.sep)


def _protected(env: Env) -> set[str]:
    sysroot = os.environ.get("SystemRoot", r"C:\Windows")
    places = [env.home, env.desktop, env.documents, env.appdata, env.localappdata, env.temp,
              env.localappdata / "Programs", sysroot,
              os.environ.get("ProgramFiles", r"C:\Program Files"),
              os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
              os.environ.get("ProgramData", r"C:\ProgramData"), Path(env.home).parent]
    return {_n(p) for p in places if p}


def _is_dev_tree(p: Path) -> bool:
    """A source checkout (the project folder): never deleted as 'data'."""
    try:
        return (p / "main.py").is_file() and (p / "core").is_dir()
    except OSError:
        return False


def _basic_refusal(p: Path, env: Env) -> str | None:
    if not os.path.isabs(str(p)):
        return "not a full path"
    n = _n(p)
    if len(Path(n).parts) <= 1 or n == _n(Path(n).anchor):
        return "drive root"
    if n in _protected(env):
        return "protected Windows/user folder"
    if _under(env.program_dir, p):
        return "this is (or contains) the Jarvis program; the installer removes that"
    if _is_dev_tree(p):
        return "this is a development copy"
    return None


def check_owned_dir(path: Path, parent: Path, env: Env) -> str | None:
    """None if `path` is a Jarvis-owned folder we may delete, else the reason we won't."""
    path = Path(path)
    if path.name.lower() != "jarvis":
        return "not a folder named Jarvis"
    if _n(path.parent) != _n(parent):
        return "not in its expected place"
    return _basic_refusal(path, env)


def check_user_item(path: Path, env: Env, *, folder: bool) -> str | None:
    """Desktop outputs / ledger files: inside the user's folder, never a protected folder itself."""
    path = Path(path)
    why = _basic_refusal(path, env)
    if why:
        return why
    if not _under(path, env.home) or _n(path) == _n(env.home):
        return "outside your user folder"
    if folder and "jarvis" not in path.name.lower():
        return "folder name doesn't say Jarvis"
    if _under(path, env.data_dir) or _under(path, env.cache_dir):
        return "handled with Jarvis's own folders"
    return None


# ── the plan ─────────────────────────────────────────────────────────────────
@dataclass
class Item:
    kind: str            # process | task | toasts | firewall | regkey | regvalue | folder | file
    target: str
    label: str
    group: str           # running | system | temp | cache | desktop | data
    optional: bool = False
    extra: dict = field(default_factory=dict)

    def key(self) -> str:
        return f"{self.kind}:{self.target}"


@dataclass
class Plan:
    items: list[Item]
    notes: list[str]
    remove_data: bool

    def text(self) -> str:
        lines = []
        for g, title in (("running", "Stop"), ("system", "Windows entries"), ("temp", "Temporary files"),
                         ("cache", "Cache"), ("data", "Your Jarvis data"), ("desktop", "Things Jarvis made for you (only if ticked)")):
            its = [i for i in self.items if i.group == g]
            if its:
                lines.append(title + ":")
                lines += [f"  - {i.label}" for i in its]
        lines += ["Note: " + n for n in self.notes]
        return "\n".join(lines) or "Nothing to remove."


class System:
    """Everything that touches Windows, in one place (tests swap in a fake)."""

    def ps(self, script: str, timeout: int = 40) -> tuple[bool, str]:
        try:
            r = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                "-Command", script], capture_output=True, text=True, timeout=timeout,
                               creationflags=_NOWIN)
            return r.returncode == 0, r.stdout
        except Exception as exc:
            return False, str(exc)

    def run(self, args: list[str], timeout: int = 30) -> tuple[bool, str]:
        try:
            r = subprocess.run(args, capture_output=True, text=True, timeout=timeout, creationflags=_NOWIN)
            return r.returncode == 0, (r.stdout + r.stderr).strip()
        except Exception as exc:
            return False, str(exc)

    def elevated(self, commands: list[str]) -> bool:
        """Run cmd lines in ONE admin window (one UAC prompt). False if refused."""
        if not commands:
            return True
        import tempfile
        fd, bat = tempfile.mkstemp(suffix=".cmd", prefix="Jarvis_uninstall_")
        try:
            os.write(fd, ("@echo off\r\n" + "\r\n".join(commands) + "\r\n").encode("mbcs" if os.name == "nt" else "utf-8"))
            os.close(fd)
            ok, _ = self.ps(f"$p = Start-Process -FilePath cmd.exe -ArgumentList '/c','\"{bat}\"' -Verb RunAs "
                            "-WindowStyle Hidden -Wait -PassThru; exit $p.ExitCode", timeout=180)
            return ok
        finally:
            try:
                os.unlink(bat)
            except OSError:
                pass

    # processes
    def processes(self) -> list[dict]:
        import psutil
        out = []
        for p in psutil.process_iter(["pid", "name", "exe", "cmdline"]):
            try:
                cmd = " ".join(p.info["cmdline"] or [])
                cwd = ""
                if "main.py" in cmd and (p.info["name"] or "").lower().startswith("python"):
                    try:
                        cwd = p.cwd()        # `pythonw main.py` started inside the project folder
                    except Exception:
                        pass
                out.append({"pid": p.info["pid"], "name": p.info["name"] or "", "exe": p.info["exe"] or "",
                            "cmdline": cmd, "cwd": cwd})
            except Exception:
                continue
        return out

    def protected_pids(self) -> set[int]:
        import psutil
        me = psutil.Process()
        return {me.pid, *(p.pid for p in me.parents())}

    def stop(self, pids: list[int]) -> list[int]:
        """Ask nicely, then end. Returns pids still alive."""
        import psutil
        procs = []
        for pid in pids:
            try:
                procs.append(psutil.Process(pid))
            except psutil.Error:
                pass
        for p in procs:
            try:
                p.terminate()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(procs, timeout=10)
        for p in alive:
            try:
                p.kill()
            except psutil.Error:
                pass
        _, alive = psutil.wait_procs(alive, timeout=5)
        return [p.pid for p in alive]

    # registry (HKCU only)
    def reg_exists(self, key: str) -> bool:
        import winreg
        try:
            winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CURRENT_USER, key))
            return True
        except OSError:
            return False

    def reg_get(self, key: str, name: str) -> str | None:
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
                return str(winreg.QueryValueEx(k, name)[0])
        except OSError:
            return None

    def reg_delete_tree(self, key: str) -> None:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_ALL_ACCESS) as k:
            subs = []
            i = 0
            while True:
                try:
                    subs.append(winreg.EnumKey(k, i))
                    i += 1
                except OSError:
                    break
        for s in subs:
            self.reg_delete_tree(key + "\\" + s)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, key)

    def reg_delete_value(self, key: str, name: str) -> None:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0, winreg.KEY_SET_VALUE) as k:
            winreg.DeleteValue(k, name)


def _mentions_jarvis_program(text: str, env: Env) -> bool:
    t = os.path.normcase(text or "")
    return any(_n(p) in t for p in (env.program_dir, env.data_dir)) or (
        _n(env.temp) in t and "jarvisreminder_" in t)


def _ledger() -> list[dict]:
    try:
        from core import footprint
        return footprint.entries()
    except Exception:
        return []


def plan(env: Env | None = None, *, remove_data: bool = False, system: System | None = None,
         ledger: list[dict] | None = None) -> Plan:
    env = env or Env.current()
    sysm = system or System()
    ledger = _ledger() if ledger is None else ledger
    items: list[Item] = []
    notes: list[str] = []
    seen: set[str] = set()

    def add(item: Item):
        if item.key() not in seen:
            seen.add(item.key())
            items.append(item)

    good = [e for e in ledger if e.get("verified")]
    bad = [e for e in ledger if not e.get("verified")]
    if bad:
        notes.append(f"{len(bad)} footprint entr{'y' if len(bad) == 1 else 'ies'} not signed by Jarvis: ignored")

    # 1. running Jarvis processes
    try:
        skip = sysm.protected_pids()
        main_py = _n(env.program_dir / "main.py")
        for p in sysm.processes():
            if p["pid"] in skip:
                continue
            ours = (p["name"].lower() in JARVIS_EXES and _under(p["exe"], env.program_dir)) if p["exe"] else False
            if not ours and p["name"].lower().startswith("python"):   # a developer-run Jarvis
                args = [a.strip('"') for a in p["cmdline"].split()]
                ours = any(_n(a) == main_py or (a.lower() == "main.py" and p.get("cwd") and
                                                _n(p["cwd"]) == _n(env.program_dir)) for a in args)
            if ours:
                add(Item("process", str(p["pid"]), f"{p['name']} (running)", "running"))
    except Exception as exc:
        notes.append(f"couldn't list running programs: {exc}")

    # 2. scheduled tasks (root folder only; name prefix or ledger, AND the action runs Jarvis)
    ledger_tasks = {e["id"] for e in good if e["kind"] == "task"}
    ok, out = sysm.ps("Get-ScheduledTask -TaskPath '\\' -ErrorAction SilentlyContinue | "
                      "Where-Object { $_.TaskName -like 'Jarvis*' } | ForEach-Object { "
                      "$a = ($_.Actions | ForEach-Object { \"$($_.Execute) $($_.Arguments)\" }) -join ' ; '; "
                      "\"$($_.TaskName)`t$a\" }")
    for line in (out or "").splitlines() if ok else []:
        name, _, action = line.strip().partition("\t")
        if not name:
            continue
        if not (name.startswith(TASK_PREFIXES) or name in ledger_tasks):
            continue
        if _mentions_jarvis_program(action, env):
            add(Item("task", name, f"Scheduled task {name}", "system"))
        else:
            notes.append(f"task {name} runs something else: left alone")

    # 3. Windows-scheduled reminder notifications
    ok, out = sysm.ps(f"[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime];"
                      f"([Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}')"
                      f".GetScheduledToastNotifications() | Measure-Object).Count")
    try:
        count = int((out or "0").strip().splitlines()[-1]) if ok else 0
    except (ValueError, IndexError):
        count = 0
    if count:
        add(Item("toasts", APP_ID, f"{count} scheduled reminder notification(s)", "system"))

    # 4. firewall rules (exact Jarvis names only)
    ok, out = sysm.ps("Get-NetFirewallRule -DisplayName 'Jarvis Dashboard*' -ErrorAction SilentlyContinue | "
                      "ForEach-Object { $_.DisplayName }")
    for name in sorted({l.strip() for l in (out or "").splitlines() if l.strip()}) if ok else []:
        if FIREWALL_RE.match(name):
            add(Item("firewall", name, f"Firewall rule \"{name}\" (needs admin)", "system"))

    # 5. registry (HKCU)
    for key in OWN_KEYS:
        if sysm.reg_exists(key):
            add(Item("regkey", key, f"Registry: HKCU\\{key}", "system"))
    if sysm.reg_exists(URI_KEY):
        cmd = sysm.reg_get(URI_KEY + r"\shell\open\command", "") or ""
        if _mentions_jarvis_program(cmd, env):
            add(Item("regkey", URI_KEY, "The jarvis:// link handler", "system"))
        else:
            notes.append("jarvis:// links belong to another program: left alone")
    run = sysm.reg_get(RUN_KEY, "Jarvis")
    if run is not None:
        if _mentions_jarvis_program(run, env):
            add(Item("regvalue", RUN_KEY + "|Jarvis", "Start with Windows", "system"))
            if sysm.reg_get(STARTUP_APPROVED, "Jarvis") is not None:
                add(Item("regvalue", STARTUP_APPROVED + "|Jarvis", "Start with Windows (Task Manager setting)", "system"))
        else:
            notes.append("a different program named Jarvis starts with Windows: left alone")
    for e in good:
        if e["kind"] != "registry":
            continue
        key = re.sub(r"^HKCU\\", "", e["id"], flags=re.I)
        if key.lower().startswith("software\\") and ("jarvis" in key.lower() or APP_ID.lower() in key.lower()) \
                and key != URI_KEY and sysm.reg_exists(key):
            add(Item("regkey", key, f"Registry: HKCU\\{key}", "system"))

    # 6. temporary files Jarvis made
    for pat in TEMP_PATTERNS:
        for p in sorted(env.temp.glob(pat)):
            if p.is_file() and not _is_link(p):
                add(Item("file", str(p), f"Temp file {p.name}", "temp", extra={"scope": "temp"}))

    # 7. cache
    if _n(env.cache_dir) != _n(env.program_dir) and env.cache_dir.exists():
        why = check_owned_dir(env.cache_dir, env.localappdata, env)
        if why:
            notes.append(f"cache folder {env.cache_dir} not removed: {why}")
        else:
            add(Item("folder", str(env.cache_dir), f"Cache folder {env.cache_dir}", "cache",
                     extra={"parent": str(env.localappdata)}))

    # 8. owner data
    if remove_data:
        why = ("it's inside the Jarvis program / development folder"
               if _n(env.data_dir) == _n(env.program_dir) or _is_dev_tree(env.data_dir)
               else check_owned_dir(env.data_dir, env.appdata, env))
        if why:
            notes.append(f"your data folder {env.data_dir} not removed: {why}")
        elif env.data_dir.exists():
            add(Item("folder", str(env.data_dir), f"Your Jarvis data {env.data_dir} (keys, memory, rules, logs)", "data",
                     extra={"parent": str(env.appdata), "wipe_config": True}))
    elif env.data_dir.exists() and _n(env.data_dir) != _n(env.program_dir):
        notes.append(f"your data stays in {env.data_dir}")

    # 9. Desktop outputs + ledger files/folders (only if ticked)
    candidates: list[tuple[Path, bool, str]] = []
    for pat in DESKTOP_FILES:
        candidates += [(p, False, "made by Jarvis") for p in env.desktop.glob(pat)]
    candidates += [(env.desktop / f, True, "made by Jarvis") for f in DESKTOP_FOLDERS]
    for e in good:
        if e["kind"] in ("file", "folder"):
            candidates.append((Path(e["id"]), e["kind"] == "folder", e.get("detail") or "made by Jarvis"))
    for p, folder, why_made in candidates:
        if not (p.is_dir() if folder else p.is_file()) or _is_link(p):
            continue
        if _under(p, env.temp):
            continue  # temp files are handled above (by name pattern)
        why = check_user_item(p, env, folder=folder)
        if why:
            notes.append(f"{p.name} not offered: {why}")
            continue
        add(Item("folder" if folder else "file", str(p), f"{p} ({why_made})", "desktop", optional=True,
                 extra={"scope": "user"}))

    return Plan(items, notes, remove_data)


# ── doing it ─────────────────────────────────────────────────────────────────
def _is_link(p) -> bool:
    try:
        st = os.lstat(p)
    except OSError:
        return False
    return stat.S_ISLNK(st.st_mode) or bool(getattr(st, "st_file_attributes", 0) & 0x400)  # reparse point


def _remove_link(p: Path) -> None:
    try:
        os.rmdir(p)          # a directory junction / symlink: removes the link, never the target
    except OSError:
        os.unlink(p)


def _unlink(p: Path) -> None:
    try:
        os.unlink(p)
    except PermissionError:
        os.chmod(p, stat.S_IWRITE)
        os.unlink(p)


def remove_tree(p: Path) -> None:
    """Delete a folder without ever following a link out of it."""
    p = Path(p)
    if _is_link(p):
        _remove_link(p)
        return
    if p.is_file():
        _unlink(p)
        return
    with os.scandir(p) as it:
        entries = list(it)
    for e in entries:
        cp = Path(e.path)
        if _is_link(cp):
            _remove_link(cp)
        elif e.is_dir(follow_symlinks=False):
            remove_tree(cp)
        else:
            _unlink(cp)
    try:
        os.rmdir(p)
    except PermissionError:
        os.chmod(p, stat.S_IWRITE)
        os.rmdir(p)


def _wipe_config(data_dir: Path) -> None:
    """Overwrite key/token files once before deleting (SSDs may still keep copies: revoke your keys)."""
    cfg = data_dir / "config"
    if not cfg.is_dir() or _is_link(cfg):
        return
    for f in cfg.iterdir():
        try:
            if f.is_file() and not _is_link(f) and f.stat().st_size <= 1_000_000:
                size = f.stat().st_size
                with open(f, "r+b") as fh:
                    fh.write(b"\0" * size)
                    fh.flush()
                    os.fsync(fh.fileno())
        except OSError:
            pass


@dataclass
class Result:
    item: Item
    status: str        # removed | failed | skipped
    note: str = ""


def _recheck(item: Item, env: Env) -> str | None:
    p = Path(item.target)
    if item.kind == "folder" and item.group in ("cache", "data"):
        return check_owned_dir(p, Path(item.extra["parent"]), env)
    if item.extra.get("scope") == "user":
        return check_user_item(p, env, folder=item.kind == "folder")
    if item.extra.get("scope") == "temp":
        if _n(p.parent) != _n(env.temp) or not any(fnmatch.fnmatch(p.name, pat) for pat in TEMP_PATTERNS):
            return "not a Jarvis temp file"
    return None


def execute(pl: Plan, env: Env | None = None, *, system: System | None = None,
            selected: set[str] | None = None, log=print, forget=None, allow_admin: bool = True) -> list[Result]:
    """Carry out a plan. `selected` = keys of optional items the owner ticked."""
    env = env or Env.current()
    sysm = system or System()
    selected = selected or set()
    if forget is None:
        try:
            from core import footprint
            forget = footprint.forget
        except Exception:
            forget = lambda *_: None  # noqa: E731
    results: list[Result] = []

    def done(item, status, note=""):
        results.append(Result(item, status, note))
        log(f"[uninstall] {status}: {item.label}" + (f" ({note})" if note else ""))

    todo = [i for i in pl.items if not i.optional or i.key() in selected]
    for i in pl.items:
        if i.optional and i.key() not in selected:
            done(i, "skipped", "not ticked")

    # running Jarvis first, so nothing is locked
    procs = [i for i in todo if i.kind == "process"]
    if procs:
        alive = set(sysm.stop([int(i.target) for i in procs]))
        for i in procs:
            done(i, "failed" if int(i.target) in alive else "removed", "still running" if int(i.target) in alive else "")

    admin: list[tuple[Item, str]] = []
    for i in todo:
        if i.kind == "process":
            continue
        try:
            why = _recheck(i, env)
            if why:
                done(i, "skipped", why)
                continue
            if i.kind == "task":
                ok, msg = sysm.run(["schtasks", "/Delete", "/TN", i.target, "/F"])
                if ok:
                    forget("task", i.target)
                    done(i, "removed")
                else:
                    admin.append((i, f'schtasks /Delete /TN "{i.target}" /F'))
            elif i.kind == "toasts":
                ok, msg = sysm.ps(
                    "[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime];"
                    f"$n = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}');"
                    "$n.GetScheduledToastNotifications() | ForEach-Object { $n.RemoveFromSchedule($_) };"
                    f"try {{ [Windows.UI.Notifications.ToastNotificationManager]::History.Clear('{APP_ID}') }} catch {{}}")
                done(i, "removed" if ok else "failed", "" if ok else (msg or "")[:120])
            elif i.kind == "firewall":
                admin.append((i, f'netsh advfirewall firewall delete rule name="{i.target}"'))
            elif i.kind == "regkey":
                sysm.reg_delete_tree(i.target)
                forget("registry", "HKCU\\" + i.target)
                done(i, "removed")
            elif i.kind == "regvalue":
                key, _, name = i.target.rpartition("|")
                sysm.reg_delete_value(key, name)
                done(i, "removed")
            elif i.kind == "folder":
                if i.extra.get("wipe_config"):
                    _wipe_config(Path(i.target))
                remove_tree(Path(i.target))
                if i.group == "desktop":
                    forget("folder", i.target)
                done(i, "removed")
            elif i.kind == "file":
                _unlink(Path(i.target))
                forget("file", i.target)
                done(i, "removed")
        except FileNotFoundError:
            done(i, "removed", "already gone")
        except Exception as exc:
            done(i, "failed", f"{type(exc).__name__}: {exc}"[:160])

    if admin:
        if allow_admin:
            log(f"[uninstall] asking Windows for admin rights once, for {len(admin)} item(s)")
        granted = allow_admin and sysm.elevated([cmd for _, cmd in admin])
        for i, _ in admin:
            if granted:
                if i.kind == "task":
                    forget("task", i.target)
                done(i, "removed")
            else:
                done(i, "failed", "needs admin rights (refused or unavailable)")
    return results


def verify(results: list[Result], env: Env | None = None, *, system: System | None = None,
           ledger: list[dict] | None = None, remove_data: bool = False) -> list[Item]:
    """Look again: which items we said we removed are still there?"""
    again = plan(env, remove_data=remove_data, system=system, ledger=ledger)
    still = {i.key() for i in again.items}
    return [r.item for r in results if r.status == "removed" and r.item.kind != "process" and r.item.key() in still]


def report_text(pl: Plan, results: list[Result], leftovers: list[Item]) -> str:
    """Plain report: what was removed / failed / kept. Never any key, token or file content."""
    lines = ["Jarvis uninstall report", time.strftime("%Y-%m-%d %H:%M:%S"),
             "Mode: " + ("remove everything" if pl.remove_data else "keep my data"), ""]
    for status in ("removed", "failed", "skipped"):
        rs = [r for r in results if r.status == status]
        if rs:
            lines.append(status.capitalize() + ":")
            lines += [f"  - {r.item.label}" + (f"  [{r.note}]" if r.note else "") for r in rs]
    if leftovers:
        lines.append("Still present after cleanup:")
        lines += [f"  - {i.label}" for i in leftovers]
    if pl.notes:
        lines.append("Notes:")
        lines += [f"  - {n}" for n in pl.notes]
    lines += ["", "Outside this PC (only you can do these):",
              "  - Revoke your Gemini key: https://aistudio.google.com/apikey",
              "  - Delete your Telegram bot: message @BotFather, send /deletebot"]
    return "\n".join(lines) + "\n"


def write_report(pl: Plan, results: list[Result], leftovers: list[Item], env: Env | None = None,
                 folder: Path | None = None) -> Path:
    env = env or Env.current()
    out = Path(folder or env.temp) / REPORT_NAME
    out.write_text(report_text(pl, results, leftovers), encoding="utf-8")
    return out


# ── the installed copy (MSI) ─────────────────────────────────────────────────
UPGRADE_CODE = "{6E1C6B2A-4C1D-4B8E-9C3F-2A7D5E1F0B91}"     # = tools/wix_msi.UPGRADE_CODE
ARP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis"


def installed_product_code(system: System | None = None) -> str | None:
    """The MSI product code of the installed Jarvis, or None (e.g. running from source)."""
    sysm = system or System()
    code = sysm.reg_get(ARP_KEY, "ProductCode")
    if code and re.fullmatch(r"\{[0-9A-Fa-f-]{36}\}", code):
        return code.upper()
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(39)
        if ctypes.windll.msi.MsiEnumRelatedProductsW(UPGRADE_CODE, 0, 0, buf) == 0:
            return buf.value.upper()
    except Exception:
        pass
    return None


def launch_window() -> bool:
    """Open the uninstall window (Customise → Uninstall Jarvis). From source it opens as a preview."""
    from core import paths
    try:
        if paths.FROZEN:
            exe = paths.PROGRAM_DIR / "Jarvis-Uninstall.exe"
            if not exe.is_file():
                return False
            subprocess.Popen([str(exe)], cwd=str(paths.PROGRAM_DIR), close_fds=True)
        else:
            subprocess.Popen([sys.executable, str(paths.PROGRAM_DIR / "uninstaller.py"), "--preview"],
                             cwd=str(paths.PROGRAM_DIR), close_fds=True)
        return True
    except OSError:
        return False


def remove_installed(product_code: str, *, quiet: bool = True) -> int:
    """Run the MSI uninstall: removes the program, shortcuts, Apps entry and the uninstaller.
    JARVIS_CLEANED=1 tells the MSI's safety-net step that cleanup already ran."""
    args = ["msiexec.exe", "/x", product_code, "/qn" if quiet else "/passive", "/norestart", "JARVIS_CLEANED=1"]
    return subprocess.run(args, creationflags=_NOWIN).returncode


# ── command line ─────────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="core.uninstall", description="Remove everything Jarvis made on this PC.")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--dry-run", action="store_true", help="list what would be removed; change nothing")
    mode.add_argument("--cleanup", action="store_true", help="remove it")
    data = ap.add_mutually_exclusive_group()
    data.add_argument("--keep-data", action="store_true", help="keep %%APPDATA%%\\Jarvis (default)")
    data.add_argument("--remove-data", action="store_true", help="also erase your Jarvis data")
    ap.add_argument("--confirm", default="", help='must be "DELETE" together with --remove-data')
    ap.add_argument("--desktop", action="store_true", help="also remove the Desktop items Jarvis made")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--no-admin", action="store_true", help="never ask for admin rights (the MSI's silent step)")
    ap.add_argument("--program-dir", default="", help="the installed Jarvis folder (the uninstaller passes it)")
    ap.add_argument("--report-dir", default="", help="where to write the report (default: %%TEMP%%)")
    a = ap.parse_args(argv)

    if a.remove_data and a.cleanup and a.confirm != "DELETE":
        print('Refusing: --remove-data needs --confirm DELETE', file=sys.stderr)
        return 2
    log = (lambda *_: None) if a.quiet else print
    env = Env.current()
    if a.program_dir:
        env.program_dir = Path(a.program_dir)
    pl = plan(env, remove_data=a.remove_data)
    if a.dry_run:
        print(pl.text())
        return 0
    selected = {i.key() for i in pl.items if i.optional} if a.desktop else set()
    results = execute(pl, env, selected=selected, log=log, allow_admin=not a.no_admin)
    leftovers = verify(results, env, remove_data=a.remove_data)
    if a.report_dir or not a.quiet:       # silent runs leave no report file behind
        report = write_report(pl, results, leftovers, env, Path(a.report_dir) if a.report_dir else None)
        log(f"[uninstall] report: {report}")
    return 1 if leftovers or any(r.status == "failed" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
