"""Uninstaller Phases 1–2: the footprint ledger and the cleanup engine, on a fake Windows profile."""

import json
import os
import re
import subprocess
from pathlib import Path

from core import footprint, uninstall as un
from core.brand import APP_ID

ROOT = Path(__file__).resolve().parent.parent


# ── a fake PC ────────────────────────────────────────────────────────────────
def make_env(tmp: Path) -> un.Env:
    home = tmp / "home"
    roaming, local = home / "AppData" / "Roaming", home / "AppData" / "Local"
    env = un.Env(home=home, appdata=roaming, localappdata=local, temp=local / "Temp",
                 program_dir=local / "Programs" / "Jarvis", data_dir=roaming / "Jarvis", cache_dir=local / "Jarvis")
    for d in (env.temp, env.program_dir, env.data_dir / "config", env.cache_dir / "analysis", env.desktop, env.documents):
        d.mkdir(parents=True, exist_ok=True)
    return env


class FakeSystem(un.System):
    def __init__(self, env: un.Env):
        self.env = env
        exe = str(env.program_dir / "Jarvis.exe")
        self.tasks = {"JarvisReminder_20261201_0900": f"{env.program_dir}\\jarvis-python.exe \"{env.temp}\\JarvisReminder_20261201_0900.pyw\"",
                      "JarvisAI_GameUpdater": f"{env.program_dir}\\jarvis-python.exe game_updater.py --scheduled",
                      "JarvisReminder_fake": r"C:\Other\evil.exe",          # a look-alike: not ours
                      "SomeoneElse": exe}
        self.firewall = ["Jarvis Dashboard Port 8000", "Jarvis Dashboard Python", "Jarvis Dashboard Evil"]
        self.toasts = 2
        self.reg = {rf"Software\Classes\AppUserModelId\{APP_ID}": {},
                    rf"Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\{APP_ID}": {},
                    r"Software\Classes\jarvis": {},
                    r"Software\Classes\jarvis\shell\open\command": {"": f'"{exe}" --uri "%1"'},
                    un.RUN_KEY: {"Jarvis": f'"{exe}" --startup', "OneDrive": "onedrive.exe"},
                    un.STARTUP_APPROVED: {"Jarvis": "02"}}
        self.procs = [{"pid": 101, "name": "Jarvis.exe", "exe": exe, "cmdline": exe},
                      {"pid": 102, "name": "jarvis-python.exe", "exe": str(env.program_dir / "jarvis-python.exe"), "cmdline": ""},
                      {"pid": 103, "name": "Jarvis.exe", "exe": r"C:\Games\Jarvis.exe", "cmdline": ""},   # another app
                      {"pid": 104, "name": "Jarvis.exe", "exe": exe, "cmdline": exe},                      # = us (protected)
                      {"pid": 105, "name": "pythonw.exe", "exe": r"C:\Py\pythonw.exe", "cmdline": "pythonw.exe main.py",
                       "cwd": str(env.program_dir)},                                                     # dev-run Jarvis
                      {"pid": 106, "name": "bash.exe", "exe": r"C:\Git\bash.exe", "cmdline": "bash -c 'python main.py'",
                       "cwd": str(env.program_dir)},                                                     # not Python: left alone
                      {"pid": 107, "name": "python.exe", "exe": r"C:\Py\python.exe", "cmdline": "python main.py",
                       "cwd": r"C:\other\project"}]                                                     # someone else's main.py
        self.stopped, self.elevated_cmds, self.ran = [], [], []
        self.admin_ok = True

    def ps(self, script, timeout=40):
        if "Get-ScheduledTask" in script:
            return True, "\n".join(f"{n}\t{a}" for n, a in self.tasks.items() if n.startswith("Jarvis"))
        if "RemoveFromSchedule" in script:
            self.toasts = 0
            return True, ""
        if "GetScheduledToastNotifications" in script:
            return True, str(self.toasts)
        if "Get-NetFirewallRule" in script:
            return True, "\n".join(self.firewall)
        return False, ""

    def run(self, args, timeout=30):
        self.ran.append(args)
        if args[:2] == ["schtasks", "/Delete"]:
            name = args[3]
            if name == "JarvisAI_GameUpdater":
                return False, "Access is denied."        # made as SYSTEM: needs admin
            self.tasks.pop(name, None)
            return True, ""
        return False, ""

    def elevated(self, commands):
        self.elevated_cmds += commands
        if not self.admin_ok:
            return False
        for c in commands:
            m = re.search(r'name="([^"]+)"', c) or re.search(r'/TN "([^"]+)"', c)
            if m:
                self.firewall = [f for f in self.firewall if f != m.group(1)]
                self.tasks.pop(m.group(1), None)
        return True

    def processes(self):
        return list(self.procs)

    def protected_pids(self):
        return {104}

    def stop(self, pids):
        self.stopped += pids
        self.procs = [p for p in self.procs if p["pid"] not in pids]
        return []

    def reg_exists(self, key):
        return key in self.reg

    def reg_get(self, key, name):
        return self.reg.get(key, {}).get(name)

    def reg_delete_tree(self, key):
        for k in [k for k in self.reg if k == key or k.startswith(key + "\\")]:
            del self.reg[k]

    def reg_delete_value(self, key, name):
        del self.reg[key][name]


def populate(env: un.Env) -> None:
    (env.temp / "Jarvis_edge_tts_abc.mp3").write_bytes(b"x")
    (env.temp / "JarvisReminder_20261201_0900.pyw").write_text("x", encoding="utf-8")
    (env.temp / "unrelated.tmp").write_text("keep", encoding="utf-8")
    (env.cache_dir / "analysis" / "site.json").write_text("{}", encoding="utf-8")
    (env.data_dir / "config" / "api_keys.json").write_text('{"k": "SECRET-KEY-123"}', encoding="utf-8")
    (env.data_dir / "memory").mkdir()
    (env.data_dir / "memory" / "notes.json").write_text("{}", encoding="utf-8")
    (env.desktop / "JarvisAI").mkdir()
    (env.desktop / "JarvisAI" / "report.docx").write_bytes(b"x")
    (env.desktop / "jarvis_screenshot.png").write_bytes(b"x")
    (env.desktop / "my_own_notes.txt").write_text("mine", encoding="utf-8")


def _quiet(*_):
    pass


# ── Phase 1: the ledger ─────────────────────────────────────────────────────
def test_ledger_records_once_verifies_and_forgets(tmp_path, monkeypatch):
    monkeypatch.setattr(footprint, "LEDGER", tmp_path / "footprint.json")
    monkeypatch.setattr(footprint, "_key", lambda: b"k" * 32)
    assert footprint.record("task", "JarvisReminder_1", "reminder")
    assert footprint.record("task", "JarvisReminder_1", "reminder")       # no duplicate
    assert not footprint.record("nonsense", "x")
    footprint.record("file", tmp_path / "a.txt")
    es = footprint.entries()
    assert [(e["kind"], e["verified"]) for e in es] == [("task", True), ("file", True)]
    data = json.loads((tmp_path / "footprint.json").read_text(encoding="utf-8"))
    data["entries"][0]["id"] = "EvilTask"                                 # edited outside Jarvis
    (tmp_path / "footprint.json").write_text(json.dumps(data), encoding="utf-8")
    assert footprint.entries()[0]["verified"] is False
    footprint.forget("file", tmp_path / "a.txt")
    assert [e["kind"] for e in footprint.entries()] == ["task"]


def test_every_side_effect_is_recorded_in_the_ledger():
    """Code that creates a task, firewall rule or registry key must tell the ledger (or be a known fixed item)."""
    fixed = {  # handled by the uninstaller's fixed list, with an ownership check
        "core/app_integration.py": "Run value 'Jarvis'",
        "ui.py": "Run value 'Jarvis'",
        "actions/computer_settings.py": "changes the owner's Windows theme on request; not a Jarvis artefact",
        "uninstaller.py": "a RunOnce entry that deletes the uninstaller's temp copy, and removes itself",
    }
    pat = re.compile(r"schtasks[\"', ]+/Create|firewall add rule|winreg\.CreateKey|winreg\.SetValueEx|Register-ScheduledTask")
    skip = ("tests/", "build/", "dist/", ".venv/", "_reference/", "extra/", "jarvis-connect-android/")
    missing = []
    for f in ROOT.rglob("*.py"):
        rel = str(f.relative_to(ROOT)).replace("\\", "/")
        if rel.startswith(skip) or rel in fixed or rel == "core/uninstall.py":
            continue
        text = f.read_text(encoding="utf-8-sig", errors="ignore")
        if pat.search(text) and "footprint.record" not in text:
            missing.append(rel)
    assert missing == [], f"side effects not recorded for the uninstaller: {missing}"


# ── Phase 2: guards ─────────────────────────────────────────────────────────
def test_folder_guard_refuses_everything_but_jarvis_folders(tmp_path):
    env = make_env(tmp_path)
    ok = un.check_owned_dir
    assert ok(env.data_dir, env.appdata, env) is None
    assert ok(env.cache_dir, env.localappdata, env) is None
    assert ok(Path(env.home.anchor), env.appdata, env)                      # drive root
    assert ok(env.home, env.home.parent, env)                               # user folder
    assert ok(env.appdata / "Other", env.appdata, env)                      # not named Jarvis
    assert ok(env.documents / "Jarvis", env.appdata, env)                   # wrong place
    assert ok(env.program_dir, env.localappdata / "Programs", env)          # the program: MSI's job
    dev = env.appdata / "Jarvis"
    (dev / "core").mkdir(parents=True, exist_ok=True)
    (dev / "main.py").write_text("", encoding="utf-8")
    assert "development" in ok(dev, env.appdata, env)                      # a source checkout


def test_user_item_guard(tmp_path):
    env = make_env(tmp_path)
    assert un.check_user_item(env.desktop / "JarvisAI", env, folder=True) is None
    assert un.check_user_item(env.desktop / "Projects", env, folder=True)  # folder must say Jarvis
    assert un.check_user_item(env.desktop, env, folder=True)
    assert un.check_user_item(env.documents, env, folder=True)
    assert un.check_user_item(tmp_path / "elsewhere" / "jarvis.txt", env, folder=False)   # outside home
    assert un.check_user_item(env.data_dir / "x.txt", env, folder=False)


def test_remove_tree_never_follows_a_junction(tmp_path):
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    victim = tmp_path / "Jarvis"
    (victim / "sub").mkdir(parents=True)
    (victim / "sub" / "f.txt").write_text("x", encoding="utf-8")
    r = subprocess.run(["cmd", "/c", "mklink", "/J", str(victim / "link"), str(outside)], capture_output=True)
    assert r.returncode == 0, r.stderr
    un.remove_tree(victim)
    assert not victim.exists()
    assert (outside / "keep.txt").read_text(encoding="utf-8") == "keep"


# ── Phase 2: the whole run ──────────────────────────────────────────────────
def test_keep_my_data_removes_every_trace_but_your_data(tmp_path):
    env = make_env(tmp_path)
    populate(env)
    sysm = FakeSystem(env)
    pl = un.plan(env, remove_data=False, system=sysm, ledger=[])
    keys = {i.key() for i in pl.items}
    assert "task:JarvisReminder_fake" not in keys and "firewall:Jarvis Dashboard Evil" not in keys
    assert {"process:101", "process:102", "process:105"} <= keys
    assert not keys & {"process:103", "process:104", "process:106", "process:107"}
    assert not any(i.group == "data" for i in pl.items)

    results = un.execute(pl, env, system=sysm, log=_quiet, forget=lambda *_: None)
    assert [r for r in results if r.status == "failed"] == []
    assert sorted(sysm.stopped) == [101, 102, 105]
    assert set(sysm.tasks) == {"JarvisReminder_fake", "SomeoneElse"}
    assert sysm.firewall == ["Jarvis Dashboard Evil"]
    assert len([c for c in sysm.elevated_cmds]) == 3                      # 2 rules + the SYSTEM task, one prompt
    assert sysm.toasts == 0
    assert set(sysm.reg) == {un.RUN_KEY, un.STARTUP_APPROVED}
    assert sysm.reg[un.RUN_KEY] == {"OneDrive": "onedrive.exe"} and sysm.reg[un.STARTUP_APPROVED] == {}
    assert not env.cache_dir.exists()
    assert sorted(p.name for p in env.temp.iterdir()) == ["unrelated.tmp"]
    assert (env.data_dir / "config" / "api_keys.json").read_text(encoding="utf-8").startswith('{"k"')
    assert (env.desktop / "JarvisAI" / "report.docx").exists()            # not ticked: kept
    assert (env.desktop / "my_own_notes.txt").exists()

    assert un.verify(results, env, system=sysm, ledger=[]) == []
    again = un.plan(env, remove_data=False, system=sysm, ledger=[])       # idempotent: nothing left
    assert [i for i in again.items if not i.optional] == []


def test_remove_everything_wipes_data_and_ticked_desktop_items(tmp_path):
    env = make_env(tmp_path)
    populate(env)
    sysm = FakeSystem(env)
    pl = un.plan(env, remove_data=True, system=sysm, ledger=[])
    desk = {i.key() for i in pl.items if i.optional}
    assert desk == {f"folder:{env.desktop / 'JarvisAI'}", f"file:{env.desktop / 'jarvis_screenshot.png'}"}
    wiped = {}
    orig = un._wipe_config
    un._wipe_config = lambda d: (orig(d), wiped.update(k=(d / "config" / "api_keys.json").read_bytes()))
    try:
        un.execute(pl, env, system=sysm, selected={f"folder:{env.desktop / 'JarvisAI'}"}, log=_quiet, forget=lambda *_: None)
    finally:
        un._wipe_config = orig
    assert set(wiped["k"]) == {0}                                          # overwritten before deletion
    assert not env.data_dir.exists() and not (env.desktop / "JarvisAI").exists()
    assert (env.desktop / "jarvis_screenshot.png").exists()               # not ticked
    assert (env.desktop / "my_own_notes.txt").exists()
    assert env.appdata.exists() and env.desktop.exists()


def test_dev_copy_data_is_never_removed(tmp_path):
    env = make_env(tmp_path)
    env.data_dir = env.cache_dir = env.program_dir
    (env.program_dir / "core").mkdir()
    (env.program_dir / "main.py").write_text("", encoding="utf-8")
    pl = un.plan(env, remove_data=True, system=FakeSystem(env), ledger=[])
    assert not any(i.kind == "folder" and i.group in ("data", "cache") for i in pl.items)
    assert any("not removed" in n for n in pl.notes)


def test_other_programs_named_jarvis_are_left_alone(tmp_path):
    env = make_env(tmp_path)
    sysm = FakeSystem(env)
    sysm.reg[r"Software\Classes\jarvis\shell\open\command"] = {"": r'"C:\Other\jarvis.exe" "%1"'}
    sysm.reg[un.RUN_KEY]["Jarvis"] = r"C:\Other\jarvis.exe"
    pl = un.plan(env, system=sysm, ledger=[])
    keys = {i.key() for i in pl.items}
    assert f"regkey:{un.URI_KEY}" not in keys and not any(i.kind == "regvalue" for i in pl.items)
    assert any("another program" in n for n in pl.notes) and any("different program" in n for n in pl.notes)


def test_ledger_entries_are_hints_checked_by_the_guards(tmp_path):
    env = make_env(tmp_path)
    mine = env.desktop / "flights_x.txt"
    mine.write_text("x", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("x", encoding="utf-8")
    ledger = [{"kind": "file", "id": str(mine), "detail": "flight search results", "verified": True},
              {"kind": "file", "id": str(outside), "detail": "", "verified": True},
              {"kind": "file", "id": str(env.desktop / "my_own_notes.txt"), "detail": "", "verified": False},
              {"kind": "registry", "id": r"HKCU\Software\Microsoft\Windows", "detail": "", "verified": True}]
    pl = un.plan(env, system=FakeSystem(env), ledger=ledger)
    keys = {i.key() for i in pl.items}
    assert f"file:{mine}" in keys and f"file:{outside}" not in keys
    assert not any("Microsoft\\Windows" == i.target.split("Software\\")[-1] for i in pl.items)
    assert any("not signed" in n for n in pl.notes)


def test_admin_refused_is_reported_not_fatal(tmp_path):
    env = make_env(tmp_path)
    populate(env)
    sysm = FakeSystem(env)
    sysm.admin_ok = False
    results = un.execute(un.plan(env, system=sysm, ledger=[]), env, system=sysm, log=_quiet, forget=lambda *_: None)
    failed = {r.item.target for r in results if r.status == "failed"}
    assert failed == {"Jarvis Dashboard Port 8000", "Jarvis Dashboard Python", "JarvisAI_GameUpdater"}
    assert not env.cache_dir.exists()                                      # the rest still happened


def test_report_has_no_secrets(tmp_path):
    from tools import leak_scan
    env = make_env(tmp_path)
    populate(env)
    sysm = FakeSystem(env)
    pl = un.plan(env, remove_data=True, system=sysm, ledger=[])
    results = un.execute(pl, env, system=sysm, log=_quiet, forget=lambda *_: None)
    report = un.write_report(pl, results, [], env)
    text = report.read_text(encoding="utf-8")
    assert "SECRET-KEY-123" not in text and "Removed:" in text and "BotFather" in text
    assert leak_scan.scan_text("report.txt", text, ["SECRET-KEY-123"], emails=True) == []


def test_command_line_needs_typed_confirmation_to_erase_data():
    assert un.main(["--cleanup", "--remove-data", "--quiet"]) == 2
    assert un.main(["--cleanup", "--remove-data", "--confirm", "delete", "--quiet"]) == 2


# ── Phase 3: the uninstaller program ────────────────────────────────────────
def test_uninstaller_only_ever_deletes_its_own_temp_copy(tmp_path):
    import tempfile
    import uninstaller
    t = Path(tempfile.gettempdir())
    assert uninstaller._is_our_temp_dir(t / "Jarvis-Uninstall-abc123")
    assert not uninstaller._is_our_temp_dir(t / "SomethingElse")
    assert not uninstaller._is_our_temp_dir(tmp_path / "Jarvis-Uninstall-abc123")    # not directly in %TEMP%
    assert not uninstaller._is_our_temp_dir(t)


def test_backup_zip_skips_links(tmp_path):
    import zipfile
    import uninstaller
    data = tmp_path / "Jarvis"
    (data / "memory").mkdir(parents=True)
    (data / "memory" / "m.json").write_text("{}", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "private.txt").write_text("x", encoding="utf-8")
    subprocess.run(["cmd", "/c", "mklink", "/J", str(data / "link"), str(outside)], capture_output=True)
    dest = tmp_path / "backups"
    dest.mkdir()
    z = uninstaller.backup_data(data, dest)
    names = zipfile.ZipFile(z).namelist()
    assert names == ["memory/m.json"] or names == ["memory\m.json"]


def test_msi_safety_net_mode_keeps_data_and_never_asks_for_admin(monkeypatch):
    import uninstaller
    seen = {}
    monkeypatch.setattr(un, "main", lambda args: seen.setdefault("args", args) and 0)
    uninstaller.main(["--cleanup", "--keep-data", "--quiet", "--no-admin", "--program-dir", r"C:\X\Programs\Jarvis\."])
    a = seen["args"]
    assert a[:2] == ["--cleanup", "--keep-data"] and "--no-admin" in a and "--quiet" in a
    assert "--remove-data" not in a and Path(a[a.index("--program-dir") + 1]) == Path(r"C:\X\Programs\Jarvis")


def test_from_source_the_window_is_always_a_preview(monkeypatch):
    import uninstaller
    seen = {}
    monkeypatch.setattr(uninstaller, "run_window", lambda d, preview: seen.setdefault("preview", preview) and 0)
    monkeypatch.setattr(uninstaller, "schedule_self_delete", lambda: None)
    cwd = os.getcwd()
    try:
        uninstaller.main([])
    finally:
        os.chdir(cwd)
    assert seen["preview"] is True


def test_silent_cleanup_leaves_no_report_file(tmp_path, monkeypatch):
    env = make_env(tmp_path)
    sysm = FakeSystem(env)
    monkeypatch.setattr(un.Env, "current", classmethod(lambda cls: env))
    monkeypatch.setattr(un, "plan", lambda e=None, **k: un.Plan([], [], False))
    monkeypatch.setattr(un, "verify", lambda *a, **k: [])
    assert un.main(["--cleanup", "--keep-data", "--quiet", "--no-admin"]) == 0
    assert not (env.temp / un.REPORT_NAME).exists()
