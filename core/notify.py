"""Real Windows notifications (Action Center toasts) for Jarvis, with no extra packages.

  • Jarvis is registered as its own notification source ("Jarvis" name + icon, app id
    from core.brand), so toasts are branded and grouped like any app's.
  • Buttons use the jarvis:// link scheme, registered for this Windows user. Clicking one
    works even if Jarvis is closed: Windows launches it, and a running copy is simply
    told what to show (single instance).
  • Scheduled toasts are handed to Windows itself and fire at their time even when
    Jarvis is fully quit or the PC has restarted (used for reminders).
  • Privacy: by default Windows is told not to show Jarvis notification *content* on the
    lock screen, and while the PC is locked Jarvis sends only a generic line.
  • A toast can never approve anything. Safety holds open Jarvis's on-screen banner.

Toasts are shown through PowerShell's access to the Windows notification API, in the
background, so nothing in Jarvis waits on them. Off Windows every call is a no-op.
"""

from __future__ import annotations

from core.paths import DATA_DIR  # owner data lives apart from the program
import base64
import json
import subprocess
import sys
import threading
import uuid
from datetime import datetime
from pathlib import Path
from xml.sax.saxutils import escape

from core.brand import APP_ID, APP_NAME

BASE_DIR = Path(__file__).resolve().parent.parent
ICON_PNG = BASE_DIR / "assets" / "Jarvis_Lite_Logo.png"
ICON_ICO = BASE_DIR / "assets" / "Jarvis_Lite_Logo.ico"
SETTINGS = DATA_DIR / "config" / "app_settings.json"
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_registered = False


def _settings() -> dict:
    try:
        return json.loads(SETTINGS.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}


# ── registration (once per run; cheap, idempotent) ───────────────────────
def _launch_cmd() -> str:
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --uri "%1"'
    pyw = Path(sys.executable).with_name("pythonw.exe")
    exe = pyw if pyw.exists() else Path(sys.executable)
    return f'"{exe}" "{BASE_DIR / "main.py"}" --uri "%1"'


def register() -> bool:
    """Register app id (name + icon), the jarvis:// link scheme, and lock-screen privacy."""
    global _registered
    if sys.platform != "win32":
        return False
    if _registered:
        return True
    try:
        import winreg
        # 1. who sends the toasts: shown as "Jarvis" with our icon
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}") as k:
            winreg.SetValueEx(k, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            if ICON_ICO.exists():
                winreg.SetValueEx(k, "IconUri", 0, winreg.REG_SZ, str(ICON_ICO))
            winreg.SetValueEx(k, "IconBackgroundColor", 0, winreg.REG_SZ, "FF0A0D14")
        # 2. jarvis:// links (toast buttons, "open the report", etc.)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\jarvis") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, "URL:Jarvis")
            winreg.SetValueEx(k, "URL Protocol", 0, winreg.REG_SZ, "")
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\jarvis\DefaultIcon") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, str(ICON_ICO))
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, r"Software\Classes\jarvis\shell\open\command") as k:
            winreg.SetValueEx(k, "", 0, winreg.REG_SZ, _launch_cmd())
        # 3. privacy: hide notification content on the lock screen unless the owner turns it on
        show_on_lock = bool(_settings().get("notify_content_on_lock_screen", False))
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER,
                              rf"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\{APP_ID}") as k:
            winreg.SetValueEx(k, "AllowContentAboveLock", 0, winreg.REG_DWORD, 1 if show_on_lock else 0)
        _tag_start_menu_shortcut()
        _registered = True
        return True
    except Exception as exc:
        print(f"[Notify] registration failed: {exc}")
        return False


def _tag_start_menu_shortcut() -> None:
    """Stamp our app id on the Start-menu shortcut: Windows reads the toast's display name
    ("Jarvis") and icon from the shortcut that carries the same app id."""
    try:
        import os
        import pythoncom
        from win32com.propsys import propsys, pscon
        from win32com.shell import shell

        lnk = Path(os.environ.get("APPDATA", "")) / r"Microsoft\Windows\Start Menu\Programs\Jarvis.lnk"
        if not lnk.exists():
            return
        link = pythoncom.CoCreateInstance(shell.CLSID_ShellLink, None, pythoncom.CLSCTX_INPROC_SERVER, shell.IID_IShellLink)
        persist = link.QueryInterface(pythoncom.IID_IPersistFile)
        persist.Load(str(lnk), 2)  # STGM_READWRITE, so the property store can be changed
        store = link.QueryInterface(propsys.IID_IPropertyStore)
        current = store.GetValue(pscon.PKEY_AppUserModel_ID).GetValue()
        if current == APP_ID:
            return
        store.SetValue(pscon.PKEY_AppUserModel_ID, propsys.PROPVARIANTType(APP_ID, pythoncom.VT_LPWSTR))
        store.Commit()
        persist.Save(str(lnk), True)
    except Exception as exc:
        print(f"[Notify] couldn't tag the Start-menu shortcut: {exc}")


# ── helpers ──────────────────────────────────────────────────────────────
def is_session_locked() -> bool:
    """True while the Windows session is locked (no input desktop available)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        u = ctypes.windll.user32
        h = u.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
        if not h:
            return True
        ok = u.SwitchDesktop(h)
        u.CloseDesktop(h)
        return not ok
    except Exception:
        return False


def _toast_xml(title: str, body: str, buttons: list[tuple[str, str]] | None, launch: str,
               reminder: bool = False, urgent: bool = False) -> str:
    img = f'<image placement="appLogoOverride" hint-crop="circle" src="{escape(ICON_PNG.as_uri())}"/>' if ICON_PNG.exists() else ""
    acts = ""
    if reminder:
        # Windows' own snooze / dismiss: works with Jarvis closed
        acts = ('<input id="snoozeTime" type="selection" defaultInput="10">'
                '<selection id="5" content="5 minutes"/><selection id="10" content="10 minutes"/>'
                '<selection id="30" content="30 minutes"/><selection id="60" content="1 hour"/></input>'
                '<action activationType="system" arguments="snooze" hint-inputId="snoozeTime" content=""/>'
                '<action activationType="system" arguments="dismiss" content=""/>')
    for label, uri in (buttons or [])[:3]:
        acts += f'<action content="{escape(label)}" activationType="protocol" arguments="{escape(uri)}"/>'
    scenario = ' scenario="reminder"' if reminder else (' scenario="urgent"' if urgent else "")
    sound = "Notification.Reminder" if reminder else ("Notification.Looping.Alarm2" if False else "Notification.Default")
    return (f'<toast launch="{escape(launch)}" activationType="protocol"{scenario}>'
            f'<visual><binding template="ToastGeneric"><text>{escape(title[:120])}</text>'
            f'<text>{escape(body[:600])}</text>{img}</binding></visual>'
            f'<actions>{acts}</actions><audio src="ms-winsoundevent:{sound}"/></toast>')


_PS_PRELUDE = r"""
$ErrorActionPreference = 'Stop'
[void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
[void][Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime]
$notifier = [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('__APPID__')
"""


def _run_ps(script: str, wait: bool = False, timeout: int = 20) -> tuple[bool, str]:
    full = _PS_PRELUDE.replace("__APPID__", APP_ID) + script
    enc = base64.b64encode(full.encode("utf-16-le")).decode()
    cmd = ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", enc]
    if not wait:
        subprocess.Popen(cmd, creationflags=_NOWIN, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True, ""
    r = subprocess.run(cmd, creationflags=_NOWIN, capture_output=True, text=True, timeout=timeout)
    return r.returncode == 0, (r.stdout + r.stderr).strip()


def _xml_to_ps(xml: str) -> str:
    b = base64.b64encode(xml.encode("utf-8")).decode()
    return (f"$x = New-Object Windows.Data.Xml.Dom.XmlDocument\n"
            f"$x.LoadXml([Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{b}')))\n")


# ── public API ───────────────────────────────────────────────────────────
def toast(title: str, body: str, *, buttons: list[tuple[str, str]] | None = None,
          launch: str = "jarvis://open", tag: str | None = None, urgent: bool = False,
          private_text: str | None = None, wait: bool = False) -> bool:
    """Show a Windows notification now. Returns False if notifications aren't available."""
    if sys.platform != "win32" or not _settings().get("os_notifications", True):
        return False
    register()
    if is_session_locked():
        body = private_text or "You have a new notification. Unlock to see it in Jarvis."
    xml = _toast_xml(title, body, buttons, launch, urgent=urgent)
    t = (tag or uuid.uuid4().hex[:12])[:16]
    script = _xml_to_ps(xml) + (f"$t = New-Object Windows.UI.Notifications.ToastNotification $x\n"
                                f"$t.Tag = '{t}'; $t.Group = 'jarvis'\n$notifier.Show($t)\n")
    try:
        ok, out = _run_ps(script, wait=wait)
        if wait and not ok:
            print(f"[Notify] toast failed: {out[:200]}")
        return ok
    except Exception as exc:
        print(f"[Notify] toast failed: {exc}")
        return False


def schedule(title: str, body: str, when: datetime, *, toast_id: str | None = None) -> str | None:
    """Hand a reminder to Windows. It fires at `when` even if Jarvis is quit. Returns its id."""
    if sys.platform != "win32":
        return None
    register()
    tid = (toast_id or uuid.uuid4().hex[:12])[:16]
    xml = _toast_xml(title, body, [("Open Jarvis", "jarvis://open")], "jarvis://open", reminder=True)
    iso = when.astimezone().isoformat()
    script = _xml_to_ps(xml) + (f"$when = [DateTimeOffset]::Parse('{iso}')\n"
                                f"$s = New-Object Windows.UI.Notifications.ScheduledToastNotification($x, $when)\n"
                                f"$s.Id = '{tid}'; $s.Tag = '{tid}'; $s.Group = 'jarvis'\n$notifier.AddToSchedule($s)\n"
                                "'scheduled'\n")
    ok, out = _run_ps(script, wait=True)
    if not ok or "scheduled" not in out:
        print(f"[Notify] schedule failed: {out[:200]}")
        return None
    return tid


def list_scheduled() -> list[dict]:
    if sys.platform != "win32":
        return []
    ok, out = _run_ps("$notifier.GetScheduledToastNotifications() | ForEach-Object { $_.Id + '|' + $_.DeliveryTime.ToString('o') }",
                      wait=True)
    items = []
    for line in out.splitlines():
        if "|" in line:
            i, w = line.split("|", 1)
            items.append({"id": i.strip(), "when": w.strip()})
    return items


def cancel(toast_id: str) -> bool:
    if sys.platform != "win32":
        return False
    ok, _ = _run_ps(f"$notifier.GetScheduledToastNotifications() | Where-Object {{ $_.Id -eq '{toast_id[:16]}' }} | "
                    "ForEach-Object { $notifier.RemoveFromSchedule($_) }", wait=True)
    return ok


def notify_async(*args, **kwargs) -> None:
    threading.Thread(target=toast, args=args, kwargs=kwargs, daemon=True, name="toast").start()


def _jarvis_in_front(ui) -> bool:
    """True when the Jarvis window is visible and focused: the owner is already looking at it."""
    try:
        win = getattr(ui, "_win", None)
        if win is None or not win.isVisible() or win.isMinimized():
            return False
        import ctypes
        return int(ctypes.windll.user32.GetForegroundWindow()) == int(win.winId())
    except Exception:
        return False


def alert(ui, panel: str, title: str, text: str, *, buttons: list[tuple[str, str]] | None = None,
          urgent: bool = False, private_text: str | None = None, routed: bool = False) -> bool:
    """One entry point for 'tell the owner even if Jarvis is in the tray'.

    • remembers the full text so the toast's button can show it in Jarvis
    • skipped while the owner is looking at Jarvis (in-app is enough), unless urgent
    • held back during quiet hours, unless urgent
    """
    try:
        from core.uri_handler import remember
        remember(panel, title, text)
    except Exception:
        pass
    if not urgent and not routed:   # routed = the Output Router already applied the owner's rules
        if _jarvis_in_front(ui):
            return False
        try:
            from core.quiet import is_quiet
            if is_quiet():
                return False
        except Exception:
            pass
    btns = buttons if buttons is not None else [("Open Jarvis", f"jarvis://open?panel={panel}")]
    notify_async(title, text, buttons=btns, launch=f"jarvis://open?panel={panel}", urgent=urgent,
                 private_text=private_text)
    return True
