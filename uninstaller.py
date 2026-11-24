"""Jarvis-Uninstall.exe: installed with Jarvis, removes Jarvis and itself.

How it starts:
  Start menu → "Uninstall Jarvis", Settings → Apps → Jarvis → Uninstall, or Customise → Uninstall.

What it does (docs/UNINSTALLER_PLAN.md §2a):
  1. copies itself to %TEMP%\\Jarvis-Uninstall-<random>\\ and restarts from there, so nothing in the
     Jarvis folder is locked
  2. window: Keep my data / Remove everything (+ backup, type DELETE) → review the exact list → go
  3. cleanup (core/uninstall.py), then `msiexec /x <product> /qn JARVIS_CLEANED=1`, which removes the
     program, the shortcuts, the Apps & Features entry and the installed uninstaller
  4. shows what happened; on close, deletes its own temp copy (and a RunOnce entry does it at the next
     sign-in if that fails)

Other modes:
  --cleanup --keep-data --quiet --no-admin --program-dir X   the MSI's safety-net step (no window)
  --quiet                                                    QuietUninstallString: keep data, no window
  --dry-run                                                  print the plan
  --preview                                                  the window, but nothing is changed
Running from source (not the installed copy) always opens in preview.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, "frozen", False))
TEMP_PREFIX = "Jarvis-Uninstall-"
RUNONCE = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
RUNONCE_NAME = "JarvisUninstallCleanup"
_NOWIN = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_DETACHED = 0x00000008  # DETACHED_PROCESS


def _args(argv):
    ap = argparse.ArgumentParser(prog="Jarvis-Uninstall")
    ap.add_argument("--from-temp", action="store_true")
    ap.add_argument("--install-dir", default="")
    ap.add_argument("--program-dir", default="")
    ap.add_argument("--cleanup", action="store_true")
    ap.add_argument("--keep-data", action="store_true")
    ap.add_argument("--no-admin", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--preview", action="store_true")
    return ap.parse_args(argv)


# ── running from a temp copy, and removing it afterwards ────────────────────
def _is_our_temp_dir(folder: Path) -> bool:
    return (folder.name.startswith(TEMP_PREFIX)
            and os.path.normcase(str(folder.parent.resolve())) == os.path.normcase(str(Path(tempfile.gettempdir()).resolve())))


def relaunch_from_temp(install_dir: Path, extra: list[str]) -> None:
    tmp = Path(tempfile.gettempdir()) / f"{TEMP_PREFIX}{uuid.uuid4().hex[:10]}"
    tmp.mkdir()
    exe = tmp / "Jarvis-Uninstall.exe"
    shutil.copy2(sys.executable, exe)
    subprocess.Popen([str(exe), "--from-temp", "--install-dir", str(install_dir), *extra],
                     cwd=str(tmp.parent), close_fds=True, creationflags=_DETACHED)


def schedule_self_delete() -> None:
    """Delete this temp copy after we exit. RunOnce is the backup if the PC is shut down first."""
    folder = Path(sys.executable).resolve().parent
    if not FROZEN or not _is_our_temp_dir(folder):
        return
    try:
        import winreg
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUNONCE) as k:
            winreg.SetValueEx(k, RUNONCE_NAME, 0, winreg.REG_SZ, f'cmd.exe /d /c rmdir /s /q "{folder}"')
    except OSError:
        pass
    cmd = (f'cmd.exe /d /c ping -n 4 127.0.0.1 >nul & rmdir /s /q "{folder}" & '
           f'reg delete "HKCU\\{RUNONCE}" /v {RUNONCE_NAME} /f >nul 2>&1')
    subprocess.Popen(cmd, cwd=os.environ.get("SystemRoot", "C:\\Windows"), close_fds=True,
                     creationflags=_NOWIN | _DETACHED)


# ── the work, without a window ──────────────────────────────────────────────
def _env(install_dir: Path):
    from core import uninstall as un
    env = un.Env.current()
    env.program_dir = install_dir
    return env


def backup_data(data_dir: Path, dest_folder: Path) -> Path:
    """Zip the owner's data (keys stay encrypted to this Windows account). Links are skipped."""
    from core.uninstall import _is_link
    out = dest_folder / f"Jarvis-backup-{time.strftime('%Y%m%d-%H%M%S')}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for root, dirs, files in os.walk(data_dir):
            dirs[:] = [d for d in dirs if not _is_link(Path(root) / d)]
            for f in files:
                p = Path(root) / f
                if not _is_link(p):
                    z.write(p, p.relative_to(data_dir))
    return out


def run_uninstall(install_dir: Path, *, remove_data: bool, selected: set[str], log, preview: bool,
                  allow_admin: bool = True, pl=None) -> dict:
    from core import uninstall as un
    env = _env(install_dir)
    pl = pl or un.plan(env, remove_data=remove_data)
    if preview:
        log("Preview only: nothing was changed.")
        return {"plan": pl, "results": [], "leftovers": [], "program": "preview", "report": un.report_text(pl, [], [])}
    results = un.execute(pl, env, selected=selected, log=log, allow_admin=allow_admin)
    leftovers = un.verify(results, env, remove_data=remove_data)
    program = "not installed"
    code = un.installed_product_code()
    if code:
        log("[uninstall] removing the Jarvis program, shortcuts and Apps entry…")
        rc = un.remove_installed(code, quiet=True)
        program = "removed" if rc in (0, 3010, 1641) else f"failed (Windows Installer code {rc})"
        log(f"[uninstall] program: {program}")
    return {"plan": pl, "results": results, "leftovers": leftovers, "program": program,
            "report": un.report_text(pl, results, leftovers) + f"Program: {program}\n"}


# ── the window ──────────────────────────────────────────────────────────────
def run_window(install_dir: Path, preview: bool) -> int:
    from PyQt6.QtCore import Qt, pyqtSignal
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import (QApplication, QButtonGroup, QCheckBox, QFileDialog, QHBoxLayout, QLineEdit,
                                 QRadioButton, QScrollArea, QStackedWidget, QTextEdit, QVBoxLayout, QWidget)
    from gui import kit
    from core import uninstall as un

    app = QApplication.instance() or QApplication(sys.argv)
    app.setApplicationName("Uninstall Jarvis")
    icon = ROOT / "assets" / "Jarvis_Lite_Logo.ico"
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    radio = ("QRadioButton { color: #e8ecf3; spacing: 10px; background: transparent; }"
             "QRadioButton::indicator { width: 12px; height: 12px; border-radius: 8px; border: 2px solid #8a93a6; }"
             f"QRadioButton::indicator:checked {{ background: {kit.accent_hex()}; border: 2px solid {kit.accent_hex()}; }}")
    check = ("QCheckBox { color: #e8ecf3; spacing: 10px; background: transparent; }"
             "QCheckBox::indicator { width: 16px; height: 16px; }")

    class Window(QWidget):
        planned = pyqtSignal(object)
        logged = pyqtSignal(str)
        finished_ = pyqtSignal(dict)

        def __init__(self):
            super().__init__()
            self.setWindowTitle("Uninstall Jarvis" + ("  (preview)" if preview else ""))
            self.setStyleSheet("QWidget#root { background: #070a10; }")
            self.setObjectName("root")
            self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
            self.resize(720, 600)
            self.plan = None
            self.result = None
            self.boxes: dict[str, QCheckBox] = {}
            self.env = _env(install_dir)

            outer = QVBoxLayout(self)
            outer.setContentsMargins(18, 18, 18, 18)
            self.card = kit.HoloPanel("Uninstall Jarvis", "PREVIEW" if preview else "")
            outer.addWidget(self.card)
            self.stack = QStackedWidget()
            self.stack.setStyleSheet("background: transparent;")
            self.card.body.addWidget(self.stack, 1)
            self.err = kit.label("", 9, kit.BAD)
            self.card.body.addWidget(self.err)
            nav = QHBoxLayout()
            self.b_cancel = kit.HudButton("Cancel", "muted")
            self.b_cancel.clicked.connect(self.close)
            self.b_back = kit.HudButton("Back", "muted")
            self.b_back.clicked.connect(lambda: self.go(0))
            self.b_next = kit.HudButton("Next", "accent")
            self.b_next.clicked.connect(self.next_)
            nav.addWidget(self.b_cancel)
            nav.addStretch(1)
            nav.addWidget(self.b_back)
            nav.addWidget(self.b_next)
            self.card.body.addLayout(nav)

            self.stack.addWidget(self.page_choose())
            self.stack.addWidget(self.page_review())
            self.stack.addWidget(self.page_done())
            self.planned.connect(self.show_plan)
            self.logged.connect(lambda s: self.log.append(s))
            self.finished_.connect(self.show_done)
            self.go(0)

        # page 1
        def page_choose(self):
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.setSpacing(10)
            lay.addWidget(kit.label("How much should go?", 13, kit.TEXT))
            self.r_keep = QRadioButton("Keep my data  (recommended)")
            self.r_all = QRadioButton("Remove everything")
            for r in (self.r_keep, self.r_all):
                r.setStyleSheet(radio)
                r.setFont(kit.font(10.5))
            grp = QButtonGroup(w)
            grp.addButton(self.r_keep)
            grp.addButton(self.r_all)
            self.r_keep.setChecked(True)
            lay.addWidget(self.r_keep)
            lay.addWidget(kit.label("Removes Jarvis, its tasks, firewall rules, registry entries and cache. Your "
                                    f"memory, rules and settings stay in {self.env.data_dir}, so reinstalling brings "
                                    "everything back.", 9, kit.MUTED))
            lay.addWidget(self.r_all)
            lay.addWidget(kit.label("Also erases your Jarvis data: keys, memory, rules, logs and reports. This "
                                    "can't be undone.", 9, kit.MUTED))
            row = QHBoxLayout()
            self.b_backup = kit.HudButton("Back up my data first…", "muted")
            self.b_backup.clicked.connect(self.backup)
            row.addWidget(self.b_backup)
            row.addStretch(1)
            lay.addLayout(row)
            self.backup_note = kit.label("", 9, kit.GOOD)
            lay.addWidget(self.backup_note)
            self.confirm = QLineEdit()
            self.confirm.setPlaceholderText("Type DELETE to confirm")
            self.confirm.setFont(kit.font(10))
            self.confirm.setStyleSheet("QLineEdit { background: rgba(255,255,255,10); color: #e8ecf3; border: 1px "
                                       "solid rgba(255,77,77,120); border-radius: 8px; padding: 7px 10px; }")
            lay.addWidget(self.confirm)
            lay.addStretch(1)
            for r in (self.r_keep, self.r_all):
                r.toggled.connect(self.sync)
            self.sync()
            return w

        def sync(self):
            on = self.r_all.isChecked()
            self.confirm.setVisible(on)
            self.b_backup.setVisible(on)

        def backup(self):
            folder = QFileDialog.getExistingDirectory(self, "Save a backup of your Jarvis data to…",
                                                      str(Path.home() / "Documents"))
            if not folder:
                return
            if not self.env.data_dir.is_dir():
                self.backup_note.setText("There's no Jarvis data to back up.")
                return
            try:
                out = backup_data(self.env.data_dir, Path(folder))
                self.backup_note.setText(f"Backup saved: {out}")
            except Exception as exc:
                self.backup_note.setStyleSheet(f"color: {kit.BAD};")
                self.backup_note.setText(f"Backup failed: {exc}")

        # page 2
        def page_review(self):
            w = QWidget()
            lay = QVBoxLayout(w)
            lay.addWidget(kit.label("This is exactly what will be removed", 13, kit.TEXT))
            self.review_hint = kit.label("Checking what Jarvis left on this PC…", 9, kit.MUTED)
            lay.addWidget(self.review_hint)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
            scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.review = QWidget()
            self.review.setStyleSheet("background: transparent;")
            self.review_lay = QVBoxLayout(self.review)
            self.review_lay.setAlignment(Qt.AlignmentFlag.AlignTop)
            scroll.setWidget(self.review)
            lay.addWidget(scroll, 1)
            return w

        def build_plan(self):
            self.plan = None
            self.b_next.setEnabled(False)
            self.review_hint.setText("Checking what Jarvis left on this PC…")
            while self.review_lay.count():
                it = self.review_lay.takeAt(0)
                if it.widget():
                    it.widget().deleteLater()
            remove = self.r_all.isChecked()
            threading.Thread(target=lambda: self.planned.emit(un.plan(self.env, remove_data=remove)),
                             daemon=True).start()

        def show_plan(self, pl):
            self.plan = pl
            self.boxes = {}
            titles = {"running": "Close Jarvis", "system": "Windows entries", "temp": "Temporary files",
                      "cache": "Cache", "data": "Your Jarvis data", "desktop": "Things Jarvis made for you (tick to remove)"}
            for g, title in titles.items():
                its = [i for i in pl.items if i.group == g]
                if not its:
                    continue
                self.review_lay.addWidget(kit.label(title, 10, kit.accent_hex()))
                for i in its:
                    if i.optional:
                        cb = QCheckBox(i.label)
                        cb.setStyleSheet(check)
                        cb.setFont(kit.font(9))
                        self.boxes[i.key()] = cb
                        self.review_lay.addWidget(cb)
                    else:
                        self.review_lay.addWidget(kit.label("•  " + i.label, 9, kit.TEXT))
            self.review_lay.addWidget(kit.label("Also: the Jarvis program, its shortcuts and its Apps & Features entry, "
                                                "and this uninstaller.", 9, kit.TEXT))
            for n in pl.notes:
                self.review_lay.addWidget(kit.label("Note: " + n, 8.5, kit.MUTED))
            self.review_hint.setText("Nothing has been changed yet." if not preview else
                                     "Preview: nothing will be changed.")
            self.b_next.setEnabled(not preview)

        # page 3
        def page_done(self):
            w = QWidget()
            lay = QVBoxLayout(w)
            self.done_title = kit.label("Uninstalling…", 13, kit.TEXT)
            lay.addWidget(self.done_title)
            self.log = QTextEdit()
            self.log.setReadOnly(True)
            self.log.setStyleSheet("QTextEdit { background: rgba(255,255,255,6); color: #c9d0dc; border: 1px solid "
                                   "rgba(255,255,255,25); border-radius: 8px; font-family: Consolas; font-size: 9pt; }")
            lay.addWidget(self.log, 1)
            self.outside = kit.label("Only you can do these, outside this PC:\n"
                                     "  •  Revoke your Gemini key at aistudio.google.com/apikey\n"
                                     "  •  Delete your Telegram bot: message @BotFather, send /deletebot", 9, kit.TEXT)
            self.outside.setVisible(False)
            lay.addWidget(self.outside)
            row = QHBoxLayout()
            self.b_report = kit.HudButton("Save report…", "muted")
            self.b_report.clicked.connect(self.save_report)
            self.b_report.setVisible(False)
            row.addWidget(self.b_report)
            row.addStretch(1)
            lay.addLayout(row)
            return w

        def start(self):
            selected = {k for k, cb in self.boxes.items() if cb.isChecked()}
            remove = self.r_all.isChecked()

            def work():
                try:
                    res = run_uninstall(install_dir, remove_data=remove, selected=selected, preview=preview,
                                        log=self.logged.emit, pl=self.plan)
                except Exception as exc:
                    res = {"error": str(exc), "results": [], "leftovers": [], "program": "not removed",
                           "report": f"Uninstall stopped: {exc}\n"}
                self.finished_.emit(res)
            threading.Thread(target=work, daemon=True).start()

        def show_done(self, res):
            self.result = res
            failed = [r for r in res.get("results", []) if r.status == "failed"]
            ok = not failed and not res.get("leftovers") and not res.get("error") and \
                not str(res.get("program", "")).startswith("failed")
            self.done_title.setText("Jarvis has been removed." if ok else "Done, but some things need your attention.")
            if not ok:
                for r in failed:
                    self.log.append(f"Couldn't remove: {r.item.label} ({r.note})")
                for i in res.get("leftovers", []):
                    self.log.append(f"Still there: {i.label}")
                if res.get("error"):
                    self.log.append(res["error"])
            self.outside.setVisible(self.r_all.isChecked())
            self.b_report.setVisible(True)
            self.b_cancel.setVisible(False)
            self.b_back.setVisible(False)
            self.b_next.setText("Close")
            self.b_next.setEnabled(True)

        def save_report(self):
            path, _ = QFileDialog.getSaveFileName(self, "Save the uninstall report",
                                                  str(Path.home() / "Documents" / un.REPORT_NAME), "Text (*.txt)")
            if path and self.result:
                Path(path).write_text(self.result.get("report", ""), encoding="utf-8")

        # navigation
        def go(self, i: int):
            self.stack.setCurrentIndex(i)
            self.err.setText("")
            self.b_back.setVisible(i == 1)
            if i == 0:
                self.b_next.setText("Next")
                self.b_next.setEnabled(True)
            elif i == 1:
                self.b_next.setText("Preview only" if preview else "Uninstall")
                self.build_plan()
            elif i == 2:
                self.b_next.setEnabled(False)
                self.b_cancel.setEnabled(False)

        def next_(self):
            i = self.stack.currentIndex()
            if i == 0:
                if self.r_all.isChecked() and self.confirm.text().strip() != "DELETE":
                    self.err.setText("To remove everything, type DELETE in the box (capital letters).")
                    return
                self.go(1)
            elif i == 1 and self.plan is not None and not preview:
                self.go(2)
                self.start()
            elif i == 2:
                self.close()

        def closeEvent(self, e):
            if self.stack.currentIndex() == 2 and self.result is None:
                e.ignore()          # never close mid-uninstall
                return
            e.accept()

    w = Window()
    w.show()
    rc = app.exec()
    return rc


def main(argv: list[str] | None = None) -> int:
    a = _args(sys.argv[1:] if argv is None else argv)
    install_dir = Path(a.install_dir or a.program_dir or (Path(sys.executable).parent if FROZEN else ROOT))

    if a.cleanup or a.dry_run:            # MSI safety net / listing: in place, no window
        from core import uninstall as un
        args = ["--dry-run"] if a.dry_run else ["--cleanup", "--keep-data"]
        args += ["--program-dir", str(install_dir)]
        args += ["--quiet"] if a.quiet else []
        args += ["--no-admin"] if a.no_admin else []
        return un.main(args)

    if FROZEN and not a.from_temp:        # step out of the Jarvis folder first
        relaunch_from_temp(install_dir, ["--quiet"] if a.quiet else (["--preview"] if a.preview else []))
        return 0

    try:
        os.chdir(tempfile.gettempdir())   # don't hold any folder open
        if a.quiet:                       # QuietUninstallString: keep data, no window, no prompts
            res = run_uninstall(install_dir, remove_data=False, selected=set(), log=lambda *_: None,
                                preview=not FROZEN, allow_admin=False)
            return 0 if not str(res["program"]).startswith("failed") else 1
        return run_window(install_dir, preview=a.preview or not FROZEN)
    finally:
        schedule_self_delete()


if __name__ == "__main__":
    sys.exit(main())
