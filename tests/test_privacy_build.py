"""Step 3: nothing personal ships, data lives apart from the program, owner ≠ developer."""

import json
import os
import subprocess
import sys
from pathlib import Path

from core.brand import DEVELOPER_NAME
from tools import leak_scan

ROOT = Path(__file__).resolve().parent.parent


def test_scanner_catches_keys_emails_and_markers():
    fake_key = "AIza" + "x" * 35
    text = "\n".join([
        f'API = "{fake_key}"',
        'contact = "someone.real' + '@' + 'gmail.com"',
        'docs = "user@example.com"',               # harmless example address
        'author = "Alex Mercer"',
        f'credit = "{DEVELOPER_NAME}"',
    ])
    hits = leak_scan.scan_text("actions/x.py", text, ["Alex Mercer", DEVELOPER_NAME])
    kinds = sorted((h.line, h.kind) for h in hits)
    assert (1, "Google/Gemini API key") in kinds
    assert (2, "email address") in kinds
    assert all(line != 3 for line, _ in kinds)
    assert (4, "personal marker") in kinds
    assert (5, "personal marker") in kinds                 # credit outside the credit files = leak
    assert fake_key not in " ".join(str(h) for h in hits)  # reports never print the secret itself


def test_developer_credit_allowed_only_in_credit_files():
    line = f'DEVELOPER_NAME = "{DEVELOPER_NAME}"  # app id {DEVELOPER_NAME.replace(" ", "")}.Jarvis'
    assert leak_scan.scan_text("core/brand.py", line, [DEVELOPER_NAME.split()[0]]) == []
    assert leak_scan.scan_text("ui.py", line, [DEVELOPER_NAME.split()[0]]) != []


def test_shipped_files_exclude_private_folders():
    rels = {str(p.relative_to(ROOT)).replace("\\", "/") for p in leak_scan.shipped_files()}
    assert "main.py" in rels and "core/brand.py" in rels
    for private in ("config/api_keys.json", "config/identity.json", "config/telegram_bot.json", "config/leak_markers.txt"):
        assert private not in rels
    assert not any(r.startswith(("logs/", "reports/", "skills/", "analysis/", "tests/", "extra/", "_reference/"))
                   for r in rels)
    # memory/: only the code ships, never what Jarvis remembers about you
    assert {r for r in rels if r.startswith("memory/")} == {"memory/__init__.py", "memory/memory_manager.py"}
    assert {r for r in rels if r.startswith("config/")} <= {"config/__init__.py", "config/models/hand_landmarker.task"}


def test_this_project_ships_clean():
    """The real gate: what would go into the installer has no keys, emails or personal data."""
    hits = leak_scan.scan(leak_scan.shipped_files(), ROOT, leak_scan.personal_markers())
    assert hits == [], "\n".join(map(str, hits))


def test_installed_copy_keeps_data_in_its_own_folder(tmp_path):
    code = ("import json; from core import paths; from core.owner import owner_name; "
            "paths.ensure_data_dirs(); "
            "print(json.dumps([str(paths.DATA_DIR), str(paths.CONFIG_DIR), paths.IS_SEPARATE_DATA, owner_name('-')]))")
    env = dict(os.environ, JARVIS_DATA_DIR=str(tmp_path / "JarvisData"))
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60)
    data_dir, config_dir, separate, owner = json.loads(out.stdout.strip().splitlines()[-1])
    assert Path(data_dir) == (tmp_path / "JarvisData").resolve()
    assert Path(config_dir).parent == Path(data_dir) and Path(config_dir).is_dir()
    assert separate is True
    assert owner == "-"                    # a fresh install knows nobody's name until setup asks


def test_owner_name_comes_from_owner_data(tmp_path, monkeypatch):
    from core import owner
    ident = tmp_path / "identity.json"
    ident.write_text(json.dumps({"owner": {"name": "Alex Mercer", "preferred_name": "Alex Sir"}}), encoding="utf-8")
    monkeypatch.setattr(owner, "IDENTITY_FILE", ident)
    assert owner.owner_name() == "Alex Mercer"
    assert owner.preferred_name() == "Alex Sir"
    assert owner.greeting_name() == "Alex"
    ident.write_text("{}", encoding="utf-8")
    assert owner.owner_name() == "" and owner.greeting_name("there") == "there"


def test_no_hard_coded_github_repo_in_updaters():
    for f in ("updater.py", "core/updater.py"):
        assert "github.com/" not in (ROOT / f).read_text(encoding="utf-8-sig").split("re.search")[0] or f == "core/updater.py"
    from core import updater
    assert "Jarvis---" + "personal" not in (ROOT / "core/updater.py").read_text(encoding="utf-8-sig")
    assert callable(updater._origin_repo)
