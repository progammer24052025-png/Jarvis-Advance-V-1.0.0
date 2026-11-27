# Jarvis uninstaller: remove Jarvis from everywhere, safely

## 1. The problem

Today, *Settings → Apps → Jarvis → Uninstall* runs the MSI. The MSI removes the program folder, the
shortcuts and the registry keys it knows about, but Jarvis also creates things **at run time** that the MSI
never sees. Those are left behind:

| Left behind | Where | Who made it |
|---|---|---|
| Owner data: keys (DPAPI), memory, rules, logs, skills, reports, downloads | `%APPDATA%\Jarvis` | everything (kept on purpose today) |
| Cache: website analysis, temp output | `%LOCALAPPDATA%\Jarvis` | `core/paths.CACHE_DIR` |
| Files created after install inside the program folder (`__pycache__`, etc.) | `%LOCALAPPDATA%\Programs\Jarvis` | Python at run time; MSI won't delete unknown files |
| Scheduled tasks `JarvisReminder_*`, `JarvisAI_GameUpdater` | Task Scheduler | `actions/reminder.py`, `actions/game_updater.py` |
| Firewall rules "Jarvis Dashboard Port N", "Jarvis Dashboard Python" | Windows Firewall (admin) | `dashboard/server.py` |
| Notification settings key | `HKCU\...\Notifications\Settings\<APP_ID>` | `core/notify.py` |
| Temp audio `Jarvis_edge_tts_*.mp3` | `%TEMP%` | `actions/attention_monitor.py` |
| Output folders `JarvisAI`, `JarvisProjects`, `jarvis_*.png`, `jarvis_calendar.ics` | Desktop | office builder, dev agent, screenshots, calendar |
| Accounts outside the PC: Gemini key, Telegram bot | Google / Telegram | the owner, during setup |

## 2. Goals and safety rules

**Goal:** one uninstall leaves **zero** Jarvis traces on the PC, unless the owner chooses to keep their data.

These rules hold in every phase:

1. **Allow-list only.** The uninstaller deletes only items on a fixed list of known locations plus items
   recorded in Jarvis's own footprint ledger (§3). It never searches the disk and deletes what it finds
   ("from root" means *every place Jarvis touched*, never a wildcard delete over `C:\`).
2. **Path guards on every delete:**
   - the resolved path must end in `\Jarvis` (or match a recorded file exactly);
   - it must sit under its expected parent (`%APPDATA%`, `%LOCALAPPDATA%`, `%LOCALAPPDATA%\Programs`, `%TEMP%`);
   - it must never be a drive root, the user profile, Desktop, Documents or Windows itself;
   - junctions and symlinks are removed as links and never followed.
3. **Never in a dev copy.** If `DATA_DIR == PROGRAM_DIR` (running from the project folder), data removal is
   refused: there, "data" is your source code.
4. **Never during an upgrade.** Installing a newer MSI must not trigger any cleanup.
5. **Dry run first.** The owner sees the full list of what will be removed before anything happens.
6. **The owner's own files are kept by default.** Documents Jarvis made for you on the Desktop are listed,
   and deleted only if you tick them.
7. **Only Jarvis's processes are stopped:** those whose executable lives in the install folder.
8. **Admin only when needed.** One UAC prompt, and only if Jarvis firewall rules actually exist.
9. **Report, no secrets.** A plain report of what was removed, what failed and why. It never contains keys
   or personal content.
10. **Third-party processes, settings and tasks are never touched**, even with "Jarvis" in their name. A
    task is ours only if its name has our prefix **and** its action points at the Jarvis program or data folder.

## 2a. Installed with Jarvis, removed with Jarvis

The uninstaller is **part of the MSI**: one install puts Jarvis *and* its uninstaller on the PC, and one
uninstall removes both. Nothing has to be downloaded or run separately.

**What the MSI installs:**

- **`Jarvis-Uninstall.exe`** goes in the install folder, next to `Jarvis.exe`.
  - It is a small, separate, self-contained program (PyInstaller onefile; PyQt6 QtWidgets only, about 25 MB),
    so it can run after the rest of Jarvis is gone.
  - It is built, leak-scanned and clean-room checked with the rest of the app.
- **Start menu → Jarvis → "Uninstall Jarvis"** points to it.
- **Its own Apps & Features entry.**
  - The MSI writes `HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis`, with:
    - `UninstallString` and `QuietUninstallString` pointing to `Jarvis-Uninstall.exe`;
    - the icon, publisher, version and size.
  - The MSI hides its own plain entry (`ARPSYSTEMCOMPONENT=1`).
  - So **Settings → Apps → Jarvis → Uninstall** opens the Jarvis uninstall window too: every route leads to
    the same window.

**How it removes itself with Jarvis:**

1. It copies itself to a fresh `%TEMP%\Jarvis-Uninstall-<random>\` folder, relaunches from there and exits,
   so no file in the install folder stays locked.
2. It runs the cleanup (§4), then `msiexec /x {ProductCode} /qn`.
   - That is quiet because you already confirmed in the window.
   - The MSI removes `Jarvis.exe`, the original `Jarvis-Uninstall.exe`, the shortcuts and the Apps & Features
     entry, because all of them are MSI components.
3. It verifies that everything is gone and shows the "Done" page.
4. When you close it, it deletes its temp copy.
   - A detached `cmd /c` waits 3 seconds, then removes the folder.
   - If that fails, `MoveFileEx(..., DELAY_UNTIL_REBOOT)` removes it at the next restart.
   - Result: **not even the uninstaller is left behind**.

**Safety nets:**

- **A direct `msiexec /x`** (admins, scripts) still works. The MSI's own custom action runs
  `Jarvis-Uninstall.exe --cleanup --keep-data --quiet` first.
- **Upgrades** replace the uninstaller along with Jarvis and never run any cleanup.
- **A missing or damaged uninstaller:** the Apps & Features entry falls back to `msiexec /x`, so Jarvis can
  always be removed.

## 3. Footprint ledger (know everything Jarvis created)

New `core/footprint.py`:

- `record(kind, ident, detail)` appends to `DATA_DIR/footprint.json`. The kinds are `task`, `firewall`,
  `registry`, `file` and `folder`.
- The first write is a signed append, reusing the directives HMAC helper.
- Callers:
  - `reminder.py` and `game_updater.py` (tasks);
  - `dashboard/server.py` (firewall rules);
  - `notify.py` (registry keys);
  - the office builder, dev agent, screenshot and calendar code (Desktop outputs).
- A test fails if any code creates a scheduled task, firewall rule or `winreg.CreateKey` without a
  matching `footprint.record` (a static scan, like the leak scanner).

The fixed list in `core/uninstall.py` covers anything made before the ledger existed.

## 4. How it works

### Where it starts

- **The normal way:** Start menu → "Uninstall Jarvis", **Settings → Apps → Jarvis → Uninstall**, or
  **Customise → About → Uninstall Jarvis**.
  - All three open `Jarvis-Uninstall.exe` (§2a), which shows the window (§5) and runs the steps below.
  - It then removes Jarvis and itself with `msiexec /x`.
- **The safety net:** a direct `msiexec /x`.
  - A WiX deferred custom action runs `Jarvis-Uninstall.exe --cleanup --keep-data --quiet` before files are removed.
  - Condition: `REMOVE="ALL" AND NOT UPGRADINGPRODUCTCODE AND NOT JARVIS_CLEANED`.
    - The uninstaller passes `JARVIS_CLEANED=1`, so the cleanup never runs twice.
  - It runs impersonated, as the user, with `Return="ignore"`, so a cleanup error never blocks the uninstall.
  - It **keeps your data**, because a silent uninstall can't ask.

### Cleanup order (`core/uninstall.py`)

1. Build the plan from the fixed list, the ledger and live checks (does each item still exist?).
2. Stop Jarvis: tray app, `jarvis-python.exe` helpers, the dashboard server and the Discord/Telegram threads,
   each matched by executable path. Wait up to 10 seconds, then end them.
3. Scheduled tasks: `schtasks /Delete` for each verified Jarvis task.
4. Firewall rules: delete by exact name, in one elevated step, only if any exist.
5. Registry (HKCU only):
   - the `jarvis://` handler, `AppUserModelId\<APP_ID>` and the notification settings key;
   - the Run value and `Software\Jarvis`.
6. Temp and cache: `%TEMP%\Jarvis_*` that Jarvis made, and `%LOCALAPPDATA%\Jarvis`.
7. Desktop outputs: only the ones you ticked.
8. Your data: `%APPDATA%\Jarvis`, only on "Remove everything".
   - The encrypted key files are overwritten once, then deleted.
   - SSDs don't guarantee overwrite, which is why §5 also tells you to revoke the keys.
9. Program folder leftovers: WiX `util:RemoveFolderEx` on the install folder at uninstall, so files created
   at run time go too.
10. Verify: re-scan every location from step 1 and write `%TEMP%\Jarvis-uninstall-report.txt`.

Each step is independent: a failure is recorded, and the cleanup carries on.

## 5. The uninstall window

**Page 1: choose.**
- **Keep my data** (default): removes the program, tasks, rules, registry and cache. Reinstalling brings
  back your memory and settings.
- **Remove everything**: also erases `%APPDATA%\Jarvis` (keys, memory, rules, logs). This option has:
  - a **"Back up first"** button, which saves a zip of your data to a folder you choose;
  - a type-to-confirm box: you must type `DELETE`.

**Page 2: review.** The exact list of what will be removed (the dry run). Desktop outputs are shown with
checkboxes, all unticked.

**Page 3: done.** What was removed, anything that couldn't be removed (with a reason), and a checklist of
outside accounts:
- revoke the Gemini key at Google AI Studio;
- delete the Telegram bot with @BotFather (`/deletebot`).

Voice and text commands: "uninstall yourself" only opens this window. Jarvis never uninstalls itself
without the window and your clicks. The tool policy marks it `ask`, and it can't be done remotely over
Telegram.

## 6. Phases

Status:
- **Phases 1–2: done.** `core/footprint.py`, `core/uninstall.py`, `tests/test_uninstall.py`.
- **Phases 3–4: done.**
  - `uninstaller.py` becomes `Jarvis-Uninstall.exe`, with a button in Customise.
  - The build script builds the uninstaller EXE, and `tools/wix_msi.py` adds the Apps entry, Start-menu
    shortcut, safety net and `RemoveFolderEx`.
  - The version goes up to 1.0.1, so the new MSI replaces 1.0.0.
- **Phase 5: test kit ready; waiting for the owner's run on a clean PC.**
  - Windows Sandbox isn't available on Windows 11 Home.
  - The kit is in `dist\uninstall-test-kit`: the 1.0.1 MSI, a 1.0.2 update-test MSI,
    `tools/jarvis_trace_check.ps1` and `docs/UNINSTALL_TEST_CHECKLIST.md`.
- **Phase 6: done.** README "Uninstalling" section; GitHub release v1.0.1 with `Jarvis-1.0.1.msi`
  published, before the Phase 5 run on a clean PC.

| Phase | Work | Done when |
|---|---|---|
| 1 | `core/footprint.py`; ledger calls in the 5 creators; static "unrecorded side effect" test | every side effect is recorded; test green |
| 2 | `core/uninstall.py`: plan, path guards, process stop, each removal step, report; `--cleanup` / `--keep-data` / `--quiet` / `--dry-run` flags | unit tests pass on a fake profile (env-overridden `APPDATA`, `LOCALAPPDATA`, `TEMP`; injectable registry root and `schtasks`) |
| 3 | `uninstaller.py` → `Jarvis-Uninstall.exe` (onefile): 3-page window, backup zip, type-to-confirm, copy-to-temp relaunch, `msiexec /x … JARVIS_CLEANED=1`, self-delete; Customise button opens it | the window works in dry-run mode on the dev PC with no changes made; the temp copy deletes itself |
| 4 | Build + WiX: build the uninstaller EXE; ship it in the MSI; own Apps & Features entry (`ARPSYSTEMCOMPONENT=1` + `Uninstall\Jarvis` key); "Uninstall Jarvis" Start-menu shortcut; safety-net custom action (not on upgrade); `RemoveFolderEx`; rebuild MSI | MSI builds; leak scan + clean-room CLEAN; Apps & Features shows one Jarvis entry that opens the window |
| 5 | **Footprint test on a clean Windows user** (or Windows Sandbox): install → set up → use reminders, dashboard, Desktop outputs → uninstall from Start menu, from Settings, and by `msiexec /x` | the after-scan finds **0** Jarvis items, uninstaller included, for "Remove everything"; only `%APPDATA%\Jarvis` for "Keep my data"; upgrade 1.0.0 → 1.0.1 keeps everything and replaces the uninstaller |
| 6 | README "Uninstalling" section, release v1.0.1 | published |

## 7. Tests (must pass before release)

- Path guard refuses: `C:\`, the user profile, Desktop, Documents, a folder not named Jarvis, a junction
  pointing outside, the dev project folder.
- A task named `JarvisReminder_x` whose action runs something else is **not** deleted.
- An upgrade (`UPGRADINGPRODUCTCODE` set) runs no cleanup.
- "Keep my data" never touches `%APPDATA%\Jarvis`.
- The report and logs contain no key, token or personal content (leak scanner run over the report).
- Cleanup is idempotent: running it twice is harmless, and the second run reports "nothing left".
- A cleanup failure (locked file, no admin) still lets the MSI uninstall finish.
- The installed copy contains `Jarvis-Uninstall.exe`, the Start-menu shortcut and exactly one Apps & Features
  entry; after uninstall, none of the three remain, and neither does the uninstaller's temp copy.
- With the uninstaller deleted by hand, Apps & Features still removes Jarvis (via the `msiexec` fallback).
