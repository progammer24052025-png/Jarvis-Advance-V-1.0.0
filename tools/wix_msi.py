"""Turn dist\\Jarvis into Jarvis-<version>.msi with the WiX Toolset v3 (build\\wix).

  • per-user install into %LOCALAPPDATA%\\Programs\\Jarvis: no admin rights needed
  • Start-menu and desktop shortcuts (tagged with Jarvis's Windows app id, so notifications are branded)
  • shows the licence first; listed in Apps & Features with the developer as publisher
  • upgrades replace older versions (fixed UpgradeCode)
  • uninstall removes the program, the jarvis:// link handler and start-with-Windows entry, and KEEPS the
    owner's data in %APPDATA%\\Jarvis (keys, memory, rules) unless they delete it themselves
  • the uninstaller (Jarvis-Uninstall.exe) is installed with Jarvis and removed with it:
      - Apps & Features shows ONE Jarvis entry, and its Uninstall button opens the uninstaller
        (the MSI's own entry is hidden); if the uninstaller is missing, it falls back to msiexec
      - Start menu: "Uninstall Jarvis"
      - safety net: a direct `msiexec /x` still runs the uninstaller's cleanup (keeping the owner's
        data, never on an upgrade, skipped when the uninstaller already did it: JARVIS_CLEANED=1)
      - files Jarvis created inside its own folder are removed too (RemoveFolderEx, only if the
        folder really ends in \\Programs\\Jarvis\\)
"""

from __future__ import annotations

import hashlib
import re
import subprocess
import uuid
from pathlib import Path
from xml.sax.saxutils import escape

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.brand import APP_ID  # noqa: E402  (the fixed Windows app id, one source of truth)

ROOT = Path(__file__).resolve().parent.parent
WIX = ROOT / "build" / "wix"
UPGRADE_CODE = "6E1C6B2A-4C1D-4B8E-9C3F-2A7D5E1F0B91"     # never change: lets new versions replace old ones
UNINSTALLER = "Jarvis-Uninstall.exe"
ARP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Jarvis"
NS = uuid.UUID("0b6f7c1e-5d8a-4c3b-9e2f-7a1d3c5b9e04")


def _id(prefix: str, text: str) -> str:
    return prefix + hashlib.sha1(text.encode("utf-8")).hexdigest()[:24]


def _guid(text: str) -> str:
    return str(uuid.uuid5(NS, text)).upper()


def _license_rtf(dest: Path) -> Path:
    text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    body = "".join(("\\u%d?" % ord(ch)) if ord(ch) > 127 else {"\\": "\\\\", "{": "\\{", "}": "\\}"}.get(ch, ch)
                   for ch in text).replace("\n", "\\par\n")
    dest.write_text("{\\rtf1\\ansi\\deff0{\\fonttbl{\\f0 Segoe UI;}}\\fs18 " + body + "}", encoding="ascii")
    return dest


def _dirs_and_files(app_dir: Path):
    tree: dict[str, list[Path]] = {}
    for p in sorted(app_dir.rglob("*")):
        if p.is_file():
            rel_dir = str(p.parent.relative_to(app_dir)).replace("\\", "/")
            tree.setdefault("" if rel_dir == "." else rel_dir, []).append(p)
    return tree


def _q(s: str) -> str:
    return escape(s, {'"': "&quot;"})


def write_wxs(app_dir: Path, out: Path, version: str, publisher: str, copyright_text: str) -> Path:
    if not (app_dir / UNINSTALLER).is_file():
        raise SystemExit(f"{UNINSTALLER} is missing from {app_dir}: Jarvis never ships without its uninstaller.")
    tree = _dirs_and_files(app_dir)
    size_kb = sum(f.stat().st_size for fs in tree.values() for f in fs) // 1024
    uninstall_cmd = _q(f'cmd.exe /d /c if exist "[INSTALLDIR]{UNINSTALLER}" (start "" "[INSTALLDIR]{UNINSTALLER}") '
                       f'else (start "" msiexec.exe /x [ProductCode])')
    quiet_cmd = _q(f'"[INSTALLDIR]{UNINSTALLER}" --quiet')
    all_dirs = set()
    for d in tree:
        parts = d.split("/") if d else []
        for i in range(len(parts) + 1):
            all_dirs.add("/".join(parts[:i]))

    def dir_id(d: str) -> str:
        return "INSTALLDIR" if d == "" else _id("d", d)

    # nested <Directory> elements
    children: dict[str, list[str]] = {}
    for d in all_dirs:
        if d:
            parent = d.rsplit("/", 1)[0] if "/" in d else ""
            children.setdefault(parent, []).append(d)

    def dir_xml(d: str, indent: str) -> str:
        out_ = []
        for c in sorted(children.get(d, [])):
            name = c.rsplit("/", 1)[-1]
            out_.append(f'{indent}<Directory Id="{dir_id(c)}" Name="{escape(name, {chr(34): "&quot;"})}">')
            out_.append(dir_xml(c, indent + "  "))
            out_.append(f"{indent}</Directory>")
        return "\n".join(x for x in out_ if x)

    # one component per folder: files + a per-user registry key path + RemoveFolder (per-user install rules)
    comps, refs = [], []
    for d in sorted(all_dirs):
        cid = _id("c", "dir:" + d)
        refs.append(f'      <ComponentRef Id="{cid}" />')
        files = []
        for f in tree.get(d, []):
            fid = ({"Jarvis.exe": "JarvisExe", UNINSTALLER: "JarvisUninstallExe"}.get(f.name) if d == "" else None) \
                or _id("f", str(f.relative_to(app_dir)))
            files.append(f'      <File Id="{fid}" Source="{escape(str(f))}" />')
        comps.append(
            f'    <Component Id="{cid}" Directory="{dir_id(d)}" Guid="{_guid("dir:" + d)}">\n'
            f'      <RegistryValue Root="HKCU" Key="Software\\Jarvis\\Installed" Name="{cid}" Type="integer" Value="1" KeyPath="yes" />\n'
            f'      <RemoveFolder Id="rm{cid}" On="uninstall" />\n' + "\n".join(files) + "\n    </Component>")

    lic = _license_rtf(out.parent / "License.rtf")
    icon = ROOT / "assets" / "Jarvis_Lite_Logo.ico"
    wxs = f'''<?xml version="1.0" encoding="UTF-8"?>
<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi" xmlns:util="http://schemas.microsoft.com/wix/UtilExtension">
  <Product Id="*" Name="Jarvis" Language="1033" Version="{version}" Manufacturer="{escape(publisher)}" UpgradeCode="{UPGRADE_CODE}">
    <Package InstallerVersion="500" Compressed="yes" InstallScope="perUser" InstallPrivileges="limited"
             Description="Jarvis personal AI assistant" Comments="{escape(copyright_text)}" Manufacturer="{escape(publisher)}" />
    <MajorUpgrade DowngradeErrorMessage="A newer version of Jarvis is already installed." />
    <MediaTemplate EmbedCab="yes" CompressionLevel="high" />
    <Icon Id="JarvisIcon" SourceFile="{escape(str(icon))}" />
    <Property Id="ARPPRODUCTICON" Value="JarvisIcon" />
    <Property Id="ALLUSERS" Value="2" />
    <Property Id="MSIINSTALLPERUSER" Value="1" />
    <!-- one Apps & Features entry (ours, below) that opens the uninstaller; hide the MSI's plain one -->
    <Property Id="ARPSYSTEMCOMPONENT" Value="1" />
    <WixVariable Id="WixUILicenseRtf" Value="{escape(str(lic))}" />
    <UIRef Id="WixUI_Minimal" />

    <Directory Id="TARGETDIR" Name="SourceDir">
      <Directory Id="LocalAppDataFolder">
        <Directory Id="ProgramsFolderLocal" Name="Programs">
          <Directory Id="INSTALLDIR" Name="Jarvis">
{dir_xml("", "            ")}
          </Directory>
        </Directory>
      </Directory>
      <Directory Id="ProgramMenuFolder" />
      <Directory Id="DesktopFolder" />
    </Directory>

    <ComponentGroup Id="AppFiles">
{chr(10).join(comps)}
    </ComponentGroup>

    <Component Id="Shortcuts" Directory="INSTALLDIR" Guid="{_guid("shortcuts")}">
      <Shortcut Id="StartMenuShortcut" Directory="ProgramMenuFolder" Name="Jarvis" Target="[INSTALLDIR]Jarvis.exe"
                WorkingDirectory="INSTALLDIR" Icon="JarvisIcon" Description="Jarvis - {escape(copyright_text)}">
        <ShortcutProperty Key="System.AppUserModel.ID" Value="{APP_ID}" />
      </Shortcut>
      <Shortcut Id="DesktopShortcut" Directory="DesktopFolder" Name="Jarvis" Target="[INSTALLDIR]Jarvis.exe"
                WorkingDirectory="INSTALLDIR" Icon="JarvisIcon" />
      <Shortcut Id="UninstallShortcut" Directory="ProgramMenuFolder" Name="Uninstall Jarvis"
                Target="[INSTALLDIR]{UNINSTALLER}" WorkingDirectory="INSTALLDIR" Icon="JarvisIcon"
                Description="Remove Jarvis from this PC" />
      <!-- Apps & Features: Uninstall opens Jarvis-Uninstall.exe (or msiexec if it's gone) -->
      <RegistryKey Root="HKCU" Key="{ARP_KEY}">
        <RegistryValue Name="DisplayName" Type="string" Value="Jarvis" />
        <RegistryValue Name="DisplayVersion" Type="string" Value="{version}" />
        <RegistryValue Name="Publisher" Type="string" Value="{_q(publisher)}" />
        <RegistryValue Name="Comments" Type="string" Value="{_q(copyright_text)}" />
        <RegistryValue Name="DisplayIcon" Type="string" Value="[INSTALLDIR]Jarvis.exe" />
        <RegistryValue Name="InstallLocation" Type="string" Value="[INSTALLDIR]" />
        <RegistryValue Name="UninstallString" Type="string" Value="{uninstall_cmd}" />
        <RegistryValue Name="QuietUninstallString" Type="string" Value="{quiet_cmd}" />
        <RegistryValue Name="ProductCode" Type="string" Value="[ProductCode]" />
        <RegistryValue Name="EstimatedSize" Type="integer" Value="{size_kb}" />
        <RegistryValue Name="NoModify" Type="integer" Value="1" />
        <RegistryValue Name="NoRepair" Type="integer" Value="1" />
      </RegistryKey>
      <!-- files Jarvis created in its own folder at run time (JARVISDIR is set only for a real Programs/Jarvis folder) -->
      <util:RemoveFolderEx On="uninstall" Property="JARVISDIR" />
      <RegistryValue Root="HKCU" Key="Software\\Jarvis\\Installed" Name="Shortcuts" Type="integer" Value="1" KeyPath="yes" />
      <!-- things Jarvis registers for itself at run time; removed on uninstall (the owner's data is kept) -->
      <RemoveRegistryKey Root="HKCU" Key="Software\\Classes\\jarvis" Action="removeOnUninstall" />
      <RemoveRegistryKey Root="HKCU" Key="Software\\Classes\\AppUserModelId\\{APP_ID}" Action="removeOnUninstall" />
      <RemoveRegistryKey Root="HKCU" Key="Software\\Jarvis" Action="removeOnUninstall" />
      <RemoveRegistryValue Root="HKCU" Key="Software\\Microsoft\\Windows\\CurrentVersion\\Run" Name="Jarvis" />
    </Component>

    <SetProperty Id="JARVISDIR" Value="[INSTALLDIR]" Before="WixRemoveFoldersEx" Sequence="execute"><![CDATA[INSTALLDIR >> "\\Programs\\Jarvis\\"]]></SetProperty>

    <!-- safety net for a direct `msiexec /x`: the uninstaller's cleanup, keeping the owner's data.
         Never on an upgrade; skipped when the uninstaller window already did it; never blocks. -->
    <CustomAction Id="JarvisCleanup" FileKey="JarvisUninstallExe" Execute="deferred" Impersonate="yes" Return="ignore"
                  ExeCommand="--cleanup --keep-data --quiet --no-admin --program-dir &quot;[INSTALLDIR].&quot;" />
    <InstallExecuteSequence>
      <Custom Action="JarvisCleanup" After="InstallInitialize"><![CDATA[REMOVE="ALL" AND NOT UPGRADINGPRODUCTCODE AND NOT JARVIS_CLEANED]]></Custom>
    </InstallExecuteSequence>

    <Feature Id="Main" Title="Jarvis" Level="1">
      <ComponentGroupRef Id="AppFiles" />
      <ComponentRef Id="Shortcuts" />
    </Feature>
  </Product>
</Wix>
'''
    out.write_text(wxs, encoding="utf-8")
    return out


def build(app_dir: Path, dist: Path, *, version: str, publisher: str, copyright_text: str) -> Path:
    work = ROOT / "build" / "msi"
    work.mkdir(parents=True, exist_ok=True)
    wxs = write_wxs(app_dir, work / "Jarvis.wxs", version, publisher, copyright_text)
    obj = work / "Jarvis.wixobj"
    msi = dist / f"Jarvis-{version}.msi"
    r = subprocess.run([str(WIX / "candle.exe"), "-nologo", "-arch", "x64", "-ext", "WixUIExtension", "-ext", "WixUtilExtension",
                        "-out", str(obj), str(wxs)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("WiX compile failed:\n" + r.stdout[-3000:] + r.stderr[-2000:])
    r = subprocess.run([str(WIX / "light.exe"), "-nologo", "-ext", "WixUIExtension", "-ext", "WixUtilExtension",
                        "-cultures:en-us", "-sval",
                        "-spdb", "-out", str(msi), str(obj)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("WiX link failed:\n" + r.stdout[-3000:] + r.stderr[-2000:])
    return msi
