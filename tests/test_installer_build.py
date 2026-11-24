"""Step 5: the installer recipe, the helper Python and the smarter leak scanner."""

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_helper_python_runs_scripts_code_and_refuses_pip(tmp_path):
    script = tmp_path / "hello.py"
    script.write_text("import sys; print('hi', sys.argv[1:])", encoding="utf-8")
    run = lambda *a: subprocess.run([sys.executable, str(ROOT / "jarvis_python.py"), *a],
                                    capture_output=True, text=True, timeout=60)
    r = run("-I", "-X", "utf8", str(script), "x")
    assert r.returncode == 0 and "hi ['x']" in r.stdout
    r = run("-c", "print(6*7)")
    assert r.stdout.strip() == "42"
    r = run("-m", "pip", "install", "anything")
    assert r.returncode == 3 and "isn't available" in r.stderr


def test_frozen_copy_never_runs_scripts_with_jarvis_exe(monkeypatch):
    from core import paths
    monkeypatch.setattr(paths, "FROZEN", True)
    assert paths.python_exe().endswith("jarvis-python.exe")
    monkeypatch.setattr(paths, "FROZEN", False)
    assert Path(paths.python_exe()).name.lower().startswith("python")


def test_msi_recipe_is_per_user_with_credit_and_clean_uninstall(tmp_path):
    from core.brand import APP_ID, COPYRIGHT, DEVELOPER_NAME
    from tools import wix_msi
    app = tmp_path / "Jarvis"
    (app / "core" / "skill_forge").mkdir(parents=True)
    (app / "Jarvis.exe").write_bytes(b"MZ")
    (app / "jarvis-python.exe").write_bytes(b"MZ")
    (app / "core" / "skill_forge" / "sandbox_runner.py").write_text("x", encoding="utf-8")
    wxs = wix_msi.write_wxs(app, tmp_path / "Jarvis.wxs", "1.2.3", DEVELOPER_NAME, COPYRIGHT)
    text = wxs.read_text(encoding="utf-8")
    ET.fromstring(text.encode("utf-8"))                                  # well-formed XML
    assert 'InstallScope="perUser"' in text and 'InstallPrivileges="limited"' in text
    assert f'Manufacturer="{DEVELOPER_NAME}"' in text and 'Version="1.2.3"' in text
    assert f'Value="{APP_ID}"' in text                                   # branded notifications
    assert "Software\\Classes\\jarvis" in text and 'Name="Jarvis" />' in text or "RemoveRegistryValue" in text
    assert 'Id="AppDataFolder"' not in text and "[AppDataFolder]" not in text   # %APPDATA%\Jarvis (owner data) untouched
    assert text.count("<File ") == 3
    assert wix_msi.UPGRADE_CODE in text


def test_scanner_ignores_library_text_but_catches_real_keys_and_owner_keys():
    from tools import leak_scan as ls
    header_only = "-----BEGIN RSA PRIVATE KEY----- -----END RSA PRIVATE KEY-----"
    real_key = "-----BEGIN RSA PRIVATE KEY-----\n" + "A" * 64
    assert ls.scan_text("PyQt6/x.dll", header_only, [], emails=False) == []
    assert ls.scan_text("PyQt6/x.dll", real_key.replace("\n", " "), [], emails=False)
    public = "AIza" + "Q" * 35
    assert ls.scan_text("PyQt6/devtools.pak", public, [], emails=False) == []      # a library's own public key
    assert ls.scan_text("core/x.py", public, [])                                    # in our code: flagged
    assert ls.scan_text("PyQt6/devtools.pak", "k=" + public, [public], emails=False)  # YOUR key: always flagged
    assert ls.scan_text("libcrypto.dll", "contact openssl-users" + "@" + "openssl.org", [], emails=False) == []
    assert ls._ours("core.brand") and ls._ours("actions/x.py") and not ls._ours("numpy._core")
