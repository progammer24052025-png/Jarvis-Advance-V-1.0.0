"""Turn dist\\Jarvis into Jarvis-<version>.msi with the WiX Toolset v3 (build\\wix).

  • per-user install into %LOCALAPPDATA%\\Programs\\Jarvis: no admin rights needed
  • Start-menu and desktop shortcuts (tagged with Jarvis's Windows app id, so notifications are branded)
  • shows the licence first; listed in Apps & Features with the developer as publisher
  • upgrades replace older versions (fixed UpgradeCode)
  • uninstall removes the program, the jarvis:// link handler and start-with-Windows entry, and KEEPS the
    owner's data in %APPDATA%\\Jarvis (keys, memory, rules) unless they delete it themselves
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


def write_wxs(app_dir: Path, out: Path, version: str, publisher: str, copyright_text: str) -> Path:
    tree = _dirs_and_files(app_dir)
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
            fid = "JarvisExe" if (d == "" and f.name == "Jarvis.exe") else _id("f", str(f.relative_to(app_dir)))
            files.append(f'      <File Id="{fid}" Source="{escape(str(f))}" />')
        comps.append(
            f'    <Component Id="{cid}" Directory="{dir_id(d)}" Guid="{_guid("dir:" + d)}">\n'
            f'      <RegistryValue Root="HKCU" Key="Software\\Jarvis\\Installed" Name="{cid}" Type="integer" Value="1" KeyPath="yes" />\n'
            f'      <RemoveFolder Id="rm{cid}" On="uninstall" />\n' + "\n".join(files) + "\n    </Component>")

    lic = _license_rtf(out.parent / "License.rtf")
    icon = ROOT / "assets" / "Jarvis_Lite_Logo.ico"
    wxs = f'''<?xml version="1.0" encoding="UTF-8"?>
<Wix xmlns="http://schemas.microsoft.com/wix/2006/wi">
  <Product Id="*" Name="Jarvis" Language="1033" Version="{version}" Manufacturer="{escape(publisher)}" UpgradeCode="{UPGRADE_CODE}">
    <Package InstallerVersion="500" Compressed="yes" InstallScope="perUser" InstallPrivileges="limited"
             Description="Jarvis personal AI assistant" Comments="{escape(copyright_text)}" Manufacturer="{escape(publisher)}" />
    <MajorUpgrade DowngradeErrorMessage="A newer version of Jarvis is already installed." />
    <MediaTemplate EmbedCab="yes" CompressionLevel="high" />
    <Icon Id="JarvisIcon" SourceFile="{escape(str(icon))}" />
    <Property Id="ARPPRODUCTICON" Value="JarvisIcon" />
    <Property Id="ALLUSERS" Value="2" />
    <Property Id="MSIINSTALLPERUSER" Value="1" />
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
      <RegistryValue Root="HKCU" Key="Software\\Jarvis\\Installed" Name="Shortcuts" Type="integer" Value="1" KeyPath="yes" />
      <!-- things Jarvis registers for itself at run time; removed on uninstall (the owner's data is kept) -->
      <RemoveRegistryKey Root="HKCU" Key="Software\\Classes\\jarvis" Action="removeOnUninstall" />
      <RemoveRegistryKey Root="HKCU" Key="Software\\Classes\\AppUserModelId\\{APP_ID}" Action="removeOnUninstall" />
      <RemoveRegistryKey Root="HKCU" Key="Software\\Jarvis" Action="removeOnUninstall" />
      <RemoveRegistryValue Root="HKCU" Key="Software\\Microsoft\\Windows\\CurrentVersion\\Run" Name="Jarvis" />
    </Component>

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
    r = subprocess.run([str(WIX / "candle.exe"), "-nologo", "-arch", "x64", "-ext", "WixUIExtension",
                        "-out", str(obj), str(wxs)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("WiX compile failed:\n" + r.stdout[-3000:] + r.stderr[-2000:])
    r = subprocess.run([str(WIX / "light.exe"), "-nologo", "-ext", "WixUIExtension", "-cultures:en-us", "-sval",
                        "-spdb", "-out", str(msi), str(obj)], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("WiX link failed:\n" + r.stdout[-3000:] + r.stderr[-2000:])
    return msi
