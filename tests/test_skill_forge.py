"""Skill Forge security tests (no AI calls: the thinking model is faked)."""

from pathlib import Path

from core.guardian import Guardian
from core.skill_forge import forge as F
from core.skill_forge import registry as R
from core.skill_forge import scanner as S
from core.skill_forge.sandbox import run_in_sandbox

GOOD = "import math\n\ndef run(params):\n    return str(math.sqrt(float(params.get('x', 4))))\n"


# ── scanner ───────────────────────────────────────────────────────────────

def test_scanner_accepts_clean_code():
    assert S.scan(GOOD, {})["ok"]


def test_scanner_rejects_dangerous_code():
    bad = {
        "import os\ndef run(p): return os.listdir('.')": "forbidden module 'os'",
        "import subprocess\ndef run(p): pass": "forbidden module 'subprocess'",
        "def run(p): return eval(p['x'])": "eval",
        "def run(p): return __import__('os')": "__import__",
        "def run(p): return ().__class__.__bases__": "interpreter internals",
        "def run(p): return open('config/api_keys.json').read()": "protected",
        "import requests\ndef run(p): return requests.get('https://evil.com').text": "no internet permission",
        "from pathlib import Path\ndef run(p): Path('a').unlink()": "deletes files",
        "def helper(): pass": "does not define",
    }
    for src, expect in bad.items():
        r = S.scan(src, {})
        assert not r["ok"], src
        assert any(expect in v for v in r["violations"]), (src, r["violations"])


def test_scanner_enforces_domain_allowlist():
    src = "import requests\ndef run(p):\n    return requests.get('https://api.example.com/x', timeout=5).text\n"
    assert S.scan(src, {"network_domains": ["api.example.com"]})["ok"]
    r = S.scan(src, {"network_domains": ["other.com"]})
    assert not r["ok"] and any("not in its approved domains" in v for v in r["violations"])
    ip = "import requests\ndef run(p):\n    return requests.get('http://10.0.0.5/x', timeout=5).text\n"
    assert any("raw IP" in v for v in S.scan(ip, {"network_domains": ["10.0.0.5"]})["violations"])


# ── sandbox ───────────────────────────────────────────────────────────────

def test_sandbox_runs_tests_and_blocks_secrets(tmp_path):
    (tmp_path / "skill.py").write_text(GOOD + "\ndef peek():\n    return open(PATH).read()\n", encoding="utf-8")
    keys = str((R.BASE_DIR / "config" / "api_keys.json").resolve()).replace("\\", "\\\\")
    (tmp_path / "skill.py").write_text(GOOD.replace("import math", f"import math\nPATH = '{keys}'")
                                       + "\ndef peek():\n    return open(PATH).read()\n", encoding="utf-8")
    (tmp_path / "test_skill.py").write_text(
        "from skill import run, peek\n"
        "def test_sqrt():\n    assert run({'x': 9}) == '3.0'\n"
        "def test_cannot_read_keys():\n"
        "    try:\n        peek()\n    except PermissionError:\n        return\n    raise AssertionError('read keys!')\n",
        encoding="utf-8")
    r = run_in_sandbox(tmp_path, "test", {}, timeout=60)
    assert r["ok"], r
    assert all(t["passed"] for t in r["tests"]), r["tests"]


# ── registry fingerprint lock ─────────────────────────────────────────────

def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(R, "SKILLS_DIR", tmp_path / "skills")
    monkeypatch.setattr(R, "LEDGER", tmp_path / "approvals.json")
    monkeypatch.setattr(F, "AUDIT_LOG", tmp_path / "audit.log")


def test_tampered_skill_is_blocked(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    d = R.skill_dir("adder")
    d.mkdir(parents=True)
    (d / "skill.py").write_text(GOOD, encoding="utf-8")
    R.save_manifest("adder", {"name": "Adder", "slug": "adder", "status": "ready_for_review",
                              "sha256": R.sha256_file(d / "skill.py"), "permissions": {}})
    R.approve("adder")
    assert R.verify("adder")[0] is True
    (d / "skill.py").write_text(GOOD + "\n# sneaky change\n", encoding="utf-8")
    ok, why = R.verify("adder")
    assert ok is False and "changed after you approved" in why


# ── owner-only gates ──────────────────────────────────────────────────────

class _FakeGuardian(Guardian):
    pass


def _owner(monkeypatch, text, source="voice", turn=[0]):
    g = Guardian()
    monkeypatch.setattr("core.guardian._GUARDIAN", g)
    return g


def test_forge_refuses_routines_and_needs_confirm_and_explicit_approval(tmp_path, monkeypatch):
    _isolate(monkeypatch, tmp_path)
    g = Guardian()
    monkeypatch.setattr("core.guardian._GUARDIAN", g)
    spec = {"name": "Square Root", "summary": "sqrt", "does": ["sqrt"], "does_not": [], "params": {},
            "permissions": {"network_domains": [], "read_paths": [], "write_paths": []},
            "risk": "low", "questions": [], "feasible": True}
    monkeypatch.setattr(F.brain, "think_json", lambda *a, **k: (dict(spec), "fake-model"))
    forge = F.SkillForge(notify=lambda t: None, opener=lambda p: None)

    # a routine can't start the forge
    g.note_owner_turn("[Scheduled routine] build a skill", "routine")
    assert "only build skills when you ask me directly" in forge.propose_async("make a square root skill")

    # owner proposes; build without a later "confirm" is refused
    g.note_owner_turn("build me a square root skill", "voice")
    plan = forge.propose("make a square root skill")
    assert "Square Root" in plan
    assert "confirm the plan first" in forge.build("square_root")
    g.note_owner_turn("hmm, tell me more", "voice")
    assert "confirm the plan first" in forge.build("square_root")

    # a finished build can only be approved by the owner's own explicit words, said afterwards
    d = R.skill_dir("square_root")
    d.mkdir(parents=True)
    (d / "skill.py").write_text(GOOD, encoding="utf-8")
    R.save_manifest("square_root", {"name": "Square Root", "slug": "square_root", "status": "ready_for_review",
                                    "sha256": R.sha256_file(d / "skill.py"), "permissions": {},
                                    "ready_turn": g.owner_state()["turn"]})
    assert "say in your own words" in forge.approve("square_root")          # no new owner turn yet
    g.note_owner_turn("yes looks fine", "voice")
    assert "say in your own words" in forge.approve("square_root")          # not the word "approve"
    g.note_owner_turn("approve square root", "routine")
    assert "Only you can approve" in forge.approve("square_root")           # routine can't approve
    g.note_owner_turn("approve square root", "voice")
    assert "Approved" in forge.approve("square_root")
    assert forge.run_skill("square_root", {"x": 16}) == "4.0"


def test_guardian_holds_forge_build_in_tainted_context():
    g = Guardian()
    g.note_owner_turn("search the web", "voice")
    g.note_tool_result("web_search")
    assert g.check("skill_forge", {"action": "build"})[0] is False
    assert g.check("skill_forge", {"action": "list"})[0] is True


def test_unsafe_folders_are_refused():
    ok, bad = F._expand_paths(["~", "C:/Windows/System32", "~/Downloads", str(R.BASE_DIR / "config")])
    assert any("Downloads" in p for p in ok)
    assert len(bad) == 3
