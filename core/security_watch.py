"""Security Watch: Jarvis keeps an eye on your PC in the background.

Read-only. It never deletes, kills or changes anything by itself; it tells you
what changed and lets you decide.

Every 15 minutes it compares your PC against a trusted baseline and reports:
  • new programs set to start automatically (Run keys, Startup folders)
  • new scheduled tasks (outside Microsoft's own)
  • new programs listening for network connections
  • Windows Defender real-time protection off, or virus definitions out of date
  • Windows Firewall turned off on any profile
  • new administrator accounts
  • programs running from Temp / Downloads (a common malware trick)
  • Remote Desktop switched on
  • the hosts file changed (used to redirect websites)

The first scan records the baseline silently. Say "Jarvis, trust them" (or "trust
Chrome" for one item) after you install something legit. Trusted items are remembered for good,
and each finding is announced once (it stays in the status until you trust it).
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _run(cmd: list[str], timeout: int = 25) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                           creationflags=_NOWIN, encoding="utf-8", errors="ignore")
        return r.stdout or ""
    except Exception:
        return ""


# ── individual probes ────────────────────────────────────────────────────

def startup_entries() -> list[str]:
    out: list[str] = []
    keys = [
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run",
        r"HKCU\Software\Microsoft\Windows\CurrentVersion\RunOnce",
        r"HKLM\Software\Microsoft\Windows\CurrentVersion\Run",
        r"HKLM\Software\Microsoft\Windows\CurrentVersion\RunOnce",
        r"HKLM\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Run",
    ]
    for k in keys:
        for line in _run(["reg", "query", k]).splitlines():
            line = line.strip()
            if "REG_" in line:
                name, _, rest = line.partition("    REG_")
                value = rest.split("    ", 1)[-1].strip()
                out.append(f"{k.split('\\')[0]}:{name.strip()} = {value[:180]}")
    folders = [
        Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Startup",
        Path(os.environ.get("PROGRAMDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\StartUp",
    ]
    for f in folders:
        try:
            for item in f.iterdir():
                if item.name.lower() != "desktop.ini":
                    out.append(f"StartupFolder:{item.name}")
        except Exception:
            pass
    return sorted(set(out))


def scheduled_tasks() -> list[str]:
    names = set()
    for line in _run(["schtasks", "/query", "/fo", "csv", "/nh"], timeout=40).splitlines():
        name = line.split('","')[0].strip('"')
        if name.startswith("\\") and not name.lower().startswith("\\microsoft\\"):
            names.add(name)
    return sorted(names)


def listening_programs() -> list[str]:
    try:
        import psutil
    except Exception:
        return []
    out = set()
    try:
        for c in psutil.net_connections(kind="inet"):
            if c.status != psutil.CONN_LISTEN or not c.laddr:
                continue
            ip = c.laddr.ip
            if ip.startswith("127.") or ip == "::1":
                continue  # local-only listeners can't be reached from the network
            try:
                pname = psutil.Process(c.pid).name() if c.pid else "system"
            except Exception:
                pname = "unknown"
            out.add(f"{pname}:{c.laddr.port}")
    except Exception:
        pass
    return sorted(out)


def defender_status() -> dict:
    raw = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                "Get-MpComputerStatus | Select-Object RealTimeProtectionEnabled,AntivirusEnabled,AntivirusSignatureAge | ConvertTo-Json"])
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def firewall_off_profiles() -> list[str]:
    raw = _run(["netsh", "advfirewall", "show", "allprofiles", "state"])
    off, current = [], ""
    for line in raw.splitlines():
        if "Profile Settings" in line:
            current = line.split("Profile")[0].strip()
        elif line.strip().upper().startswith("STATE") and "OFF" in line.upper():
            off.append(current or "unknown")
    return off


def admin_accounts() -> list[str]:
    raw = _run(["net", "localgroup", "administrators"])
    lines = [l.strip() for l in raw.splitlines()]
    try:
        start = next(i for i, l in enumerate(lines) if l.startswith("---")) + 1
    except StopIteration:
        return []
    return sorted(l for l in lines[start:] if l and not l.lower().startswith("the command completed"))


def temp_processes() -> list[str]:
    try:
        import psutil
    except Exception:
        return []
    marks = ("\\appdata\\local\\temp\\", "\\downloads\\", "\\windows\\temp\\")
    out = set()
    for p in psutil.process_iter(["name", "exe"]):
        exe = (p.info.get("exe") or "").lower()
        if exe and any(m in exe for m in marks):
            out.add(p.info.get("exe") or "")
    return sorted(out)


def rdp_enabled() -> bool:
    raw = _run(["reg", "query", r"HKLM\System\CurrentControlSet\Control\Terminal Server", "/v", "fDenyTSConnections"])
    return "0x0" in raw


def hosts_hash() -> str:
    try:
        data = Path(os.environ.get("SystemRoot", r"C:\Windows"), r"System32\drivers\etc\hosts").read_bytes()
        return hashlib.sha256(data).hexdigest()
    except Exception:
        return ""


def snapshot() -> dict:
    return {
        "taken": datetime.now().isoformat(timespec="seconds"),
        "startup": startup_entries(),
        "tasks": scheduled_tasks(),
        "listening": listening_programs(),
        "admins": admin_accounts(),
        "hosts": hosts_hash(),
    }


# ── analysis ─────────────────────────────────────────────────────────────

def compare(baseline: dict, current: dict, defender: dict, fw_off: list[str], rdp: bool, temp_procs: list[str]) -> list[dict]:
    findings: list[dict] = []

    def new_items(key: str):
        return [x for x in current.get(key, []) if x not in set(baseline.get(key, []))]

    for x in new_items("startup"):
        findings.append({"severity": "high", "what": "New program set to start with Windows", "detail": x})
    for x in new_items("tasks"):
        findings.append({"severity": "medium", "what": "New scheduled task", "detail": x})
    for x in new_items("listening"):
        findings.append({"severity": "medium", "what": "New program accepting network connections", "detail": x})
    for x in new_items("admins"):
        findings.append({"severity": "high", "what": "New administrator account", "detail": x})
    if baseline.get("hosts") and current.get("hosts") and baseline["hosts"] != current["hosts"]:
        findings.append({"severity": "high", "what": "Hosts file was changed (can redirect websites)", "detail": "C:\\Windows\\System32\\drivers\\etc\\hosts"})
    if defender:
        if defender.get("RealTimeProtectionEnabled") is False or defender.get("AntivirusEnabled") is False:
            findings.append({"severity": "high", "what": "Windows Defender protection is OFF", "detail": "Real-time protection disabled"})
        age = defender.get("AntivirusSignatureAge")
        if isinstance(age, int) and age > 7:
            findings.append({"severity": "medium", "what": "Virus definitions are out of date", "detail": f"{age} days old"})
    for prof in fw_off:
        findings.append({"severity": "high", "what": "Windows Firewall is OFF", "detail": f"{prof} profile"})
    if rdp:
        findings.append({"severity": "medium", "what": "Remote Desktop is enabled", "detail": "Others on the network can try to log in"})
    for exe in temp_procs:
        findings.append({"severity": "medium", "what": "Program running from a Temp/Downloads folder", "detail": exe})
    return findings


# ── plain-words explanations ─────────────────────────────────────────────

_TRUSTED_DIRS = ("\\program files\\", "\\program files (x86)\\", "\\windows\\", "\\windowsapps\\")
_RISKY_DIRS = ("\\appdata\\local\\temp\\", "\\downloads\\", "\\windows\\temp\\", "\\users\\public\\")
_KNOWN_APPS = {"chrome.exe": "Google Chrome", "msedge.exe": "Microsoft Edge", "onedrive.exe": "OneDrive",
               "discord.exe": "Discord", "spotify.exe": "Spotify", "steam.exe": "Steam", "teams.exe": "Microsoft Teams",
               "ms-teams.exe": "Microsoft Teams", "slack.exe": "Slack", "zoom.exe": "Zoom", "whatsapp.exe": "WhatsApp",
               "brave.exe": "Brave", "firefox.exe": "Firefox", "code.exe": "VS Code", "pythonw.exe": "Python",
               "epicgameslauncher.exe": "Epic Games", "jetbrains-toolbox.exe": "JetBrains Toolbox"}


def _exe_from(text: str) -> str:
    t = text.strip()
    if t.startswith('"'):
        return t[1:].split('"', 1)[0]
    i = t.lower().find(".exe")
    return t[: i + 4] if i >= 0 else t.split(" ", 1)[0]


def _app_name(path: str) -> str:
    exe = os.path.basename(path).lower()
    return _KNOWN_APPS.get(exe) or (os.path.splitext(os.path.basename(path))[0] or path)


def _where(path: str) -> tuple[str, str]:
    """(plain-words location, likely: normal | check | suspicious)"""
    low = path.lower().replace("/", "\\")
    if any(d in low for d in _RISKY_DIRS):
        return "from a Temp/Downloads folder", "suspicious"
    if any(d in low for d in _TRUSTED_DIRS):
        return "installed in Program Files/Windows", "normal"
    if "\\appdata\\" in low:
        return "installed in your user AppData folder", "check"
    return "", "check"


def describe(f: dict) -> dict:
    """Adds 'title', 'explain' and 'likely' to a finding, in plain words."""
    what, detail = f["what"], f["detail"]
    title, explain, likely = what, detail, "check"
    if what.startswith("New program set to start"):
        name, _, value = detail.partition(" = ")
        exe = _exe_from(value) if value else ""
        app = _app_name(exe) if exe else (name.split(":", 1)[-1] if ":" in name else name)
        where, likely = _where(exe or value)
        title = f"{app} was set to start with Windows"
        explain = (os.path.basename(exe) or name) + (f", {where}" if where else "")
        if "chrome" in (exe or name).lower() and "autolaunch" in name.lower():
            explain += ". Chrome adds this itself when 'Continue running background apps' is on"
            likely = "normal"
    elif what.startswith("New scheduled task"):
        title = "New scheduled task: " + detail.strip("\\").split("\\")[0]
        if detail.lower().startswith("\\softlanding\\"):
            explain, likely = "Windows' own tips/suggestions task", "normal"
    elif what.startswith("New program accepting network"):
        prog, _, port = detail.rpartition(":")
        title = f"{_app_name(prog)} is accepting network connections"
        explain = f"{prog}, port {port}"
    elif what.startswith("Program running from a Temp"):
        title = f"{_app_name(detail)} is running from a Temp/Downloads folder"
        explain, likely = detail, "suspicious"
    elif what.startswith(("Windows Defender", "Windows Firewall", "New administrator", "Hosts file")):
        likely = "suspicious"
    out = dict(f)
    out.update(title=title, explain=explain, likely=likely)
    return out


_LIKELY_WORD = {"normal": "probably normal", "check": "worth checking", "suspicious": "looks suspicious"}


def summarize(findings: list[dict]) -> str:
    if not findings:
        return "All clear. Nothing suspicious changed on your PC."
    order = {"suspicious": 0, "check": 1, "normal": 2}
    items = sorted((describe(f) for f in findings), key=lambda d: (order.get(d["likely"], 1), d["severity"] != "high"))
    n = len(items)
    lines = [f"Security check: {n} new thing{'s' if n != 1 else ''} on your PC since you last said it was fine."]
    for i, d in enumerate(items[:12], 1):
        mark = "⚠️" if d["likely"] == "suspicious" else ("•" if d["likely"] == "normal" else "◆")
        lines.append(f"{i}. {mark} {d['title']} ({_LIKELY_WORD[d['likely']]}). {d['explain']}.")
    if n > 12:
        lines.append(f"…and {n - 12} more.")
    lines.append("If these are yours, say “trust them” (or “trust Chrome” for just one). "
                 "I won't mention the same thing again unless something new changes.")
    return "\n".join(lines)


def finding_key(f: dict) -> str:
    return f"{f['what']}|{f['detail']}"


class SecurityWatch:
    def __init__(self, base_dir: Path, alert: Callable[[list[dict]], None], interval_secs: int = 900):
        self.base_dir = Path(base_dir)
        self.baseline_path = self.base_dir / "config" / "security_baseline.json"
        self.reported_path = self.base_dir / "config" / "security_reported.json"
        self.alert = alert
        self.interval = interval_secs
        self._reported: dict[str, float] = self._load_reported()
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self.last_findings: list[dict] = []
        self.last_scan: str | None = None

    def _load_baseline(self) -> dict | None:
        try:
            return json.loads(self.baseline_path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _load_reported(self) -> dict[str, float]:
        try:
            data = json.loads(self.reported_path.read_text(encoding="utf-8"))
            return {str(k): float(v) for k, v in data.items()}
        except Exception:
            return {}

    def _save_reported(self) -> None:
        try:
            self.reported_path.parent.mkdir(parents=True, exist_ok=True)
            self.reported_path.write_text(json.dumps(self._reported, indent=1), encoding="utf-8")
        except Exception:
            pass

    def _write_status(self) -> None:
        try:
            (self.base_dir / "config" / "security_status.json").write_text(
                json.dumps({"at": self.last_scan or datetime.now().strftime("%H:%M"), "findings": len(self.last_findings)}),
                encoding="utf-8")
        except Exception:
            pass

    @staticmethod
    def _matches(f: dict, item: str) -> bool:
        return not item or item in (f["what"] + " " + f["detail"] + " " + describe(f)["title"]).lower()

    def accept_current(self, item: str = "") -> str:
        """Trust what's on the PC now (or only the findings matching `item`, e.g. 'chrome').

        Trusted things are ADDED to the baseline and never dropped, so apps that switch their own
        startup entry on and off (Chrome does) aren't reported again each time they come back.
        """
        item = (item or "").strip().lower()
        if item in ("all", "everything", "them", "it", "these", "this", "sab", "badhu"):
            item = ""
        with self._lock:
            base = self._load_baseline() or {}
            if not self.last_findings:
                self.last_findings = []
            matched = [f for f in self.last_findings if self._matches(f, item)]
            if item and not matched:
                titles = "; ".join(describe(f)["title"] for f in self.last_findings) or "nothing right now"
                return f"Nothing in the current findings matches '{item}'. Current findings: {titles}."
            if not item:
                snap = snapshot()
                for key in ("startup", "tasks", "listening", "admins"):
                    base[key] = sorted(set(base.get(key, [])) | set(snap.get(key, [])))
                base["hosts"] = snap.get("hosts", base.get("hosts", ""))
            key_for = {"New program set to start": "startup", "New scheduled task": "tasks",
                       "New program accepting network": "listening", "New administrator": "admins"}
            ack = set(base.get("acknowledged", []))
            for f in matched:
                k = next((v for p, v in key_for.items() if f["what"].startswith(p)), None)
                if k:
                    base[k] = sorted(set(base.get(k, [])) | {f["detail"]})
                elif f["what"].startswith("Hosts file"):
                    base["hosts"] = hosts_hash()
                else:
                    ack.add(finding_key(f))
            base["acknowledged"] = sorted(ack)
            base["taken"] = datetime.now().isoformat(timespec="seconds")
            self.baseline_path.parent.mkdir(parents=True, exist_ok=True)
            self.baseline_path.write_text(json.dumps(base, indent=2), encoding="utf-8")
            self.last_findings = [f for f in self.last_findings if not self._matches(f, item)]
            self._write_status()
        trusted = [describe(f)["title"] for f in matched]
        if not trusted:
            return "Done. Everything on the PC right now is marked as trusted."
        return "Done. Trusted from now on: " + "; ".join(trusted) + ". I won't report these again."

    def scan(self) -> list[dict]:
        with self._lock:
            baseline = self._load_baseline()
            current = snapshot()
            if baseline is None:
                self.baseline_path.parent.mkdir(parents=True, exist_ok=True)
                self.baseline_path.write_text(json.dumps(current, indent=2), encoding="utf-8")
                baseline = current
            findings = compare(baseline, current, defender_status(), firewall_off_profiles(), rdp_enabled(), temp_processes())
            ack = set(baseline.get("acknowledged", []))
            findings = [f for f in findings if finding_key(f) not in ack]
            self.last_findings = findings
            self.last_scan = datetime.now().strftime("%H:%M")
            self._write_status()   # tiny status file for the HUD
            return findings

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="security-watch")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        self._stop.wait(120)  # stay out of the way during startup
        while not self._stop.is_set():
            try:
                findings = self.scan()
                now = time.time()
                # Each thing is announced once; it stays in the status until trusted, with one gentle
                # reminder a week if it's still there. Remembered across restarts.
                fresh = [f for f in findings if now - self._reported.get(finding_key(f), 0) > 7 * 86400]
                for f in fresh:
                    self._reported[finding_key(f)] = now
                if fresh:
                    self._save_reported()
                    self.alert(fresh)
            except Exception as exc:
                print(f"[SecurityWatch] scan error: {exc}")
            self._stop.wait(self.interval)


def security_check(watch: SecurityWatch | None, params: dict) -> str:
    """Tool entry point."""
    if watch is None:
        return "Security watch isn't running."
    action = str(params.get("action") or "scan").lower()
    if action in ("accept", "trust", "baseline", "ignore"):
        return watch.accept_current(str(params.get("item") or ""))
    if action == "status":
        when = watch.last_scan or "not yet"
        return f"Last scan: {when}. " + summarize(watch.last_findings)
    return summarize(watch.scan())
