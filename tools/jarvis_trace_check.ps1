<#
  Jarvis trace check: lists every trace of Jarvis on this Windows account. READ-ONLY: changes nothing.

  Use it to test the uninstaller (docs/UNINSTALL_TEST_CHECKLIST.md):
      powershell -ExecutionPolicy Bypass -File jarvis_trace_check.ps1
  After "Remove everything" it must say: 0 traces.
  After "Keep my data" the only trace must be the data folder (%APPDATA%\Jarvis).
#>

$ErrorActionPreference = 'SilentlyContinue'
$found = New-Object System.Collections.ArrayList
function Add-Trace([string]$what, [string]$where) { [void]$found.Add([pscustomobject]@{ Trace = $what; Where = $where }) }

# Jarvis's Windows app id ends in ".Jarvis" (see core/brand.py); find it instead of naming it here
$appIds = @()
foreach ($base in @('HKCU:\Software\Classes\AppUserModelId', 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings')) {
  $appIds += Get-ChildItem -LiteralPath $base | Where-Object { $_.PSChildName -like '*.Jarvis' } | ForEach-Object { $_.PSChildName }
}
$appIds = @($appIds | Sort-Object -Unique)
$desktop = [Environment]::GetFolderPath('Desktop')
$startMenu = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'

# folders
$folders = [ordered]@{
  'Program folder'     = Join-Path $env:LOCALAPPDATA 'Programs\Jarvis'
  'Your data (memory, keys, rules)' = Join-Path $env:APPDATA 'Jarvis'
  'Cache'              = Join-Path $env:LOCALAPPDATA 'Jarvis'
}
foreach ($k in $folders.Keys) { if (Test-Path -LiteralPath $folders[$k]) { Add-Trace $k $folders[$k] } }

# shortcuts
foreach ($p in @((Join-Path $desktop 'Jarvis.lnk'), (Join-Path $startMenu 'Jarvis.lnk'), (Join-Path $startMenu 'Uninstall Jarvis.lnk'))) {
  if (Test-Path -LiteralPath $p) { Add-Trace 'Shortcut' $p }
}

# uninstaller leftovers in %TEMP%
Get-ChildItem -LiteralPath $env:TEMP -Directory -Filter 'Jarvis-Uninstall-*' | ForEach-Object { Add-Trace 'Uninstaller temp copy' $_.FullName }
Get-ChildItem -LiteralPath $env:TEMP -File | Where-Object { $_.Name -like 'Jarvis_edge_tts_*' -or $_.Name -like 'JarvisReminder_*' -or $_.Name -like 'Jarvis_fw_*' -or $_.Name -like 'Jarvis_uninstall_*' -or $_.Name -eq 'Jarvis-uninstall-report.txt' } |
  ForEach-Object { Add-Trace 'Temp file' $_.FullName }

# registry (this user)
$keys = @(
  'HKCU:\Software\Jarvis',
  'HKCU:\Software\Classes\jarvis',
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis'
)
foreach ($id in $appIds) {
  $keys += "HKCU:\Software\Classes\AppUserModelId\$id"
  $keys += "HKCU:\Software\Microsoft\Windows\CurrentVersion\Notifications\Settings\$id"
}
foreach ($k in $keys) { if (Test-Path -LiteralPath $k) { Add-Trace 'Registry key' $k } }
foreach ($pair in @(@('HKCU:\Software\Microsoft\Windows\CurrentVersion\Run', 'Jarvis'),
                    @('HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run', 'Jarvis'),
                    @('HKCU:\Software\Microsoft\Windows\CurrentVersion\RunOnce', 'JarvisUninstallCleanup'))) {
  $v = Get-ItemProperty -LiteralPath $pair[0] -Name $pair[1]
  if ($null -ne $v) { Add-Trace 'Registry value' ($pair[0] + ' -> ' + $pair[1]) }
}

# Apps & Features entries / Windows Installer registration
Get-ChildItem 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall', 'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall', 'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall' |
  ForEach-Object { $d = (Get-ItemProperty -LiteralPath $_.PSPath).DisplayName; if ($d -eq 'Jarvis') { Add-Trace 'Apps & Features entry' $_.Name } }
try {
  $installer = New-Object -ComObject WindowsInstaller.Installer
  foreach ($code in $installer.RelatedProducts('{6E1C6B2A-4C1D-4B8E-9C3F-2A7D5E1F0B91}')) { Add-Trace 'Installed MSI product' $code }
} catch {}

# scheduled tasks and reminders
Get-ScheduledTask -TaskPath '\' | Where-Object { $_.TaskName -like 'Jarvis*' } | ForEach-Object { Add-Trace 'Scheduled task' $_.TaskName }
try {
  [void][Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime]
  foreach ($id in $appIds) {
    $n = ([Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($id).GetScheduledToastNotifications() | Measure-Object).Count
    if ($n -gt 0) { Add-Trace 'Scheduled reminder notifications' "$n pending ($id)" }
  }
} catch {}

# firewall rules
Get-NetFirewallRule -DisplayName 'Jarvis Dashboard*' | ForEach-Object { Add-Trace 'Firewall rule' $_.DisplayName }

# running
Get-Process | Where-Object { $_.ProcessName -in @('Jarvis', 'jarvis-python', 'Jarvis-Uninstall') } | ForEach-Object { Add-Trace 'Running program' "$($_.ProcessName) (pid $($_.Id))" }

# Desktop things Jarvis made for you (only removed if you ticked them)
foreach ($p in @('JarvisAI', 'JarvisProjects', 'jarvis_screenshot.png', 'jarvis_calendar.ics')) {
  $f = Join-Path $desktop $p
  if (Test-Path -LiteralPath $f) { Add-Trace 'Desktop item made by Jarvis' $f }
}

Write-Host ''
if ($found.Count -eq 0) {
  Write-Host 'Jarvis trace check: 0 traces. Nothing of Jarvis is left on this account.' -ForegroundColor Green
} else {
  Write-Host "Jarvis trace check: $($found.Count) trace(s) found:" -ForegroundColor Yellow
  $found | Format-Table -AutoSize -Wrap | Out-String -Width 200 | Write-Host
}
