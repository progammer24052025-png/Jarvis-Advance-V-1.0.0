# Uninstaller test checklist (Phase 5)

Run this on a PC (or Windows account) where Jarvis has **never** been installed. Your own development PC
isn't suitable, because the developer copy of Jarvis leaves its own traces there.

## You need

- `Jarvis-1.0.1.msi` (the installer under test);
- `Jarvis-1.0.2.msi` (an update-test copy: the same app with a higher version number, only for test 5);
- `jarvis_trace_check.ps1` (from `tools\`).

To check for traces at any point, open PowerShell in the folder with the files and run:

```powershell
powershell -ExecutionPolicy Bypass -File .\jarvis_trace_check.ps1
```

It only reads; it never changes anything.

**Before you start:** run the check. It must say **0 traces**. If it doesn't, use a different PC or account.

## Test 1: install

1. Double-click `Jarvis-1.0.1.msi`, accept the licence, click Install. No admin prompt should appear.
2. Check:
   - [ ] Start menu has **Jarvis** and **Uninstall Jarvis**.
   - [ ] Settings → Apps → Installed apps shows **one** Jarvis entry (version 1.0.1, publisher: the developer named in the README).
   - [ ] `%LOCALAPPDATA%\Programs\Jarvis` contains `Jarvis.exe` **and** `Jarvis-Uninstall.exe`.
3. Open Jarvis, finish setup (a free Gemini key is enough), then use it a little so it leaves traces:
   - say *"remind me in 10 minutes to drink water"* (a scheduled reminder);
   - say *"make a Word document about cats"* (a `JarvisAI` folder on the Desktop);
   - turn on **Start with Windows** in setup or Customise;
   - optional: open the dashboard on your phone (firewall rules; this asks for admin once).
4. Run the trace check: it should list the program folder, your data, shortcuts, registry entries, the
   reminder and so on. That's expected.

## Test 2: uninstall from Settings → "Keep my data"

1. Close nothing. Leave Jarvis running.
2. Settings → Apps → Jarvis → **Uninstall**.
   - [ ] The **Uninstall Jarvis** window opens. A black window may flash for a moment first; that's fine.
3. Leave **Keep my data** selected → Next.
   - [ ] The review lists the reminder, registry entries, cache and so on.
   - [ ] `JarvisAI` on the Desktop has an **unticked** box.
4. Click **Uninstall**.
   - [ ] Jarvis closes by itself.
   - [ ] The window ends with "Jarvis has been removed."
5. Close the window, wait 10 seconds, run the trace check.
   - [ ] The only traces left are **Your data** (`%APPDATA%\Jarvis`) and the Desktop `JarvisAI` folder.
   - [ ] No program folder, shortcuts, Apps entry, reminder, registry entries or `Jarvis-Uninstall-*` temp folder.

## Test 3: reinstall keeps your data, then "Remove everything" from the Start menu

1. Install `Jarvis-1.0.1.msi` again and open Jarvis.
   - [ ] It **doesn't** ask for setup again: it remembers your name and settings.
2. Start → **Uninstall Jarvis** → **Remove everything**.
   - [ ] **Next** refuses until you type `DELETE`.
   - [ ] Optional: **Back up my data first…** saves a `Jarvis-backup-….zip`.
3. Next → tick the `JarvisAI` Desktop item → **Uninstall**.
   - If you made firewall rules, Windows asks for admin once.
   - [ ] The Done page shows the revoke-your-Gemini-key / delete-your-bot reminder.
4. Close, wait 10 seconds, run the trace check.
   - [ ] **0 traces.**

## Test 4: the direct `msiexec /x` safety net

1. Install `Jarvis-1.0.1.msi`, open Jarvis, set a reminder, then close Jarvis.
2. In PowerShell, run:

   ```powershell
   $code = (Get-ItemProperty 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis').ProductCode
   msiexec /x $code /qb
   ```

3. Run the trace check.
   - [ ] Only **Your data** is left. The reminder, registry entries and cache are gone even though the window
     never opened.
4. Clean up: Start → Run `%APPDATA%`, delete the `Jarvis` folder, and run the check again.
   - [ ] **0 traces.**

## Test 5: an update keeps everything

1. Install `Jarvis-1.0.1.msi`, set up Jarvis, set a reminder.
2. Install `Jarvis-1.0.2.msi` over it.
   - [ ] Settings → Apps still shows **one** Jarvis entry, now version 1.0.2.
   - [ ] Jarvis opens with your settings and memory; the reminder still exists; the trace check still lists
     it.
   - [ ] `Jarvis-Uninstall.exe` is still in the program folder.
3. Uninstall with **Remove everything**. Run the trace check.
   - [ ] **0 traces.**

## Test 6: the uninstaller file is missing

1. Install `Jarvis-1.0.1.msi`.
2. Delete `%LOCALAPPDATA%\Programs\Jarvis\Jarvis-Uninstall.exe` by hand.
3. Settings → Apps → Jarvis → **Uninstall**.
   - [ ] Windows' own uninstall runs instead (it may ask "Are you sure?"), and Jarvis is removed.
4. Delete `%APPDATA%\Jarvis` and run the trace check.
   - [ ] **0 traces.**

## If something fails

Note the test number and step, plus what the trace check printed. In the uninstall window, **Save report…**
writes a plain report: it lists what was removed and what wasn't, and never contains keys or personal content.
