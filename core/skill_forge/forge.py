"""The Skill Forge: owner request → spec → tests → code → checks → review → owner approval."""

from __future__ import annotations

from core.paths import DATA_DIR  # owner data lives apart from the program
import html
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

from . import brain, registry, scanner
from .sandbox import run_in_sandbox

BASE_DIR = registry.BASE_DIR
AUDIT_LOG = DATA_DIR / "config" / "skill_audit.log"
MAX_FIX_ROUNDS = 5
TEST_EXTRA_IMPORTS = ("skill", "unittest", "unittest.mock")
APPROVE_WORDS = ("approve", "approved", "i approve", "approve it", "manjoor", "approve karo")
CONFIRM_WORDS = ("confirm", "confirmed", "yes", "yeah", "yep", "go ahead", "do it", "build it",
                 "proceed", "haan", "kar do", "bana do", "correct", "looks good")
UNSAFE_WRITE_ROOTS = ("c:\\windows", "c:\\program files", "c:\\program files (x86)", "c:\\programdata")

SPEC_SYSTEM = """You are the specification architect for JARVIS, a personal AI assistant that runs on its owner's Windows PC.
The owner wants a new skill (a small Python function Jarvis can call). Your job is to pin down EXACTLY what they asked for:
no more, no less. You are precise, conservative and security-minded.

Rules:
- Scope: do only what was asked. Anything adjacent but not requested goes in "does_not".
- Least privilege: request only the permissions truly needed. Prefer no internet and no folders.
  network_domains: exact hostnames (e.g. "api.coingecko.com"), never wildcards, never IP addresses.
  read_paths / write_paths: specific folders, written like "~/Downloads" or "~/Documents/Reports". Never system folders,
  never the whole drive or home folder, never Jarvis's own folders. Skills can never delete files or run programs.
- Prefer free public APIs that need no key. A skill can never read Jarvis's API keys or the owner's credentials.
- If the request is ambiguous in a way that changes the result, put short questions in "questions".
- If it can't be done safely within these limits (e.g. needs to run programs, control the mouse, delete files,
  log into accounts), set feasible=false and explain in plain words.

Return JSON:
{"name": "Short Title Case Name", "summary": "one sentence",
 "does": ["..."], "does_not": ["..."],
 "params": {"param_name": {"type": "string|number|boolean|list", "description": "...", "required": true, "default": null}},
 "output": "what run() returns, in plain words",
 "permissions": {"network_domains": [], "read_paths": [], "write_paths": []},
 "risk": "low|medium|high", "risk_reason": "...",
 "questions": [], "feasible": true, "infeasible_reason": ""}"""

RULES_FOR_CODE = """Hard rules for the skill code (an automatic scanner and a sandbox enforce these; violations are rejected):
- Python 3.12. One file. Define top-level `def run(params: dict) -> str` (may also return a dict/list; a short str is best,
  because Jarvis will speak or show it). Read inputs only from `params`, apply the spec's defaults.
- Allowed imports only: json re math statistics datetime time random string collections itertools functools operator
  decimal fractions csv textwrap unicodedata html base64 hashlib uuid dataclasses typing enum pathlib io difflib calendar
  zoneinfo bisect heapq copy pprint numbers secrets struct zlib gzip zipfile urllib.parse xml.etree.ElementTree html.parser
  bs4 openpyxl docx pptx PIL reportlab numpy{net}
- NEVER import or use: os, sys, subprocess, socket, shutil, tempfile, threading, asyncio, importlib, ctypes, pickle, platform.
  No eval/exec/compile/__import__/globals/getattr-with-dynamic-names/setattr. No dunder access. No deleting/renaming files.
- Files: use pathlib only, and only inside the approved folders (expand "~" with Path.home()). Folder paths may also be
  given in params; never touch any other location.
- Internet: only `requests` to the approved domains, always with timeout=15, and handle errors.
- Never mention or open Jarvis's config, keys, memory or credential files.
- Be correct first: handle empty/invalid input gracefully with a clear message instead of crashing.
- No prints needed; return the result."""

TESTS_SYSTEM = """You are a meticulous test engineer. Write the tests BEFORE the code exists, from the approved spec only.
The tests define what "correct" means, so they must check exactly the spec's behaviour (including the does_not limits
where testable) and edge cases. They must be deterministic."""

TESTS_RULES = """Test file rules:
- File name test_skill.py. `from skill import run`. Plain functions named test_* using assert. 4-8 tests. No pytest import.
- You may also use `import skill` and `from unittest import mock` (e.g. mock.patch.object(skill.requests, "get", ...))
  to fake network responses: tests must NOT use the real internet.
- File tests: create files only under the current working directory (e.g. Path("t_in")), and pass those folders to run()
  via params. Never use absolute paths, ~, or real user folders.
- Only the imports allowed for skills plus skill/unittest.mock. Return the file in one ```python block."""

CODE_SYSTEM = """You are a senior Python engineer writing a small, correct, secure skill for JARVIS.
Think carefully about correctness and edge cases before writing. Implement EXACTLY the spec: nothing extra."""

REVIEW_SYSTEM = """You are an independent security and correctness reviewer. You did not write this code.
Compare it strictly against the approved spec. Look for: scope creep (anything not asked for), hidden behaviour
(unexpected network calls, data leaving the PC, file access outside the approved folders), attempts to evade the
sandbox, and real bugs. Be specific and honest; do not invent problems."""


def _audit(event: str, **data) -> None:
    try:
        AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(timespec="seconds"), "event": event, **data},
                               ensure_ascii=False, default=str) + "\n")
    except Exception:
        pass


def _contains_any(text: str, words) -> bool:
    import re
    low = (text or "").lower()
    return any(re.search(r"(?<![\w'])" + re.escape(w) + r"(?![\w'])", low) for w in words)


def _expand_paths(paths) -> tuple[list[str], list[str]]:
    """Expand ~ and refuse unsafe roots. Returns (ok_paths, problems)."""
    ok, bad = [], []
    home = Path.home().resolve()
    for p in paths or []:
        raw = str(p).strip()
        if not raw:
            continue
        path = Path(os.path.expandvars(raw)).expanduser().resolve()
        low = str(path).lower()
        if (path == home or len(path.parts) <= 1 or low.startswith(UNSAFE_WRITE_ROOTS)
                or str(path).lower().startswith(str(BASE_DIR).lower())):
            bad.append(raw)
        else:
            ok.append(str(path))
    return ok, bad


def _owner():
    from core.guardian import get_guardian
    return get_guardian(DATA_DIR).owner_state()


class SkillForge:
    def __init__(self, notify: Optional[Callable[[str], None]] = None, opener: Optional[Callable[[str], None]] = None):
        self.notify = notify or (lambda text: print(f"[SkillForge] {text}"))
        self.opener = opener or (lambda path: os.startfile(path))  # type: ignore[attr-defined]
        self._proposals: dict[str, dict] = {}
        self._building: Optional[str] = None
        self._lock = threading.Lock()

    # ── 1. propose ───────────────────────────────────────────────────────
    def propose_async(self, request: str) -> str:
        """Owner checks happen now; the (slow) deep-thinking plan is made in the background."""
        state = _owner()
        if not state["trusted_source"] or state["source"] == "routine":
            _audit("PROPOSE_REFUSED", source=state["source"], request=(request or "")[:200])
            return "I only build skills when you ask me directly, not from routines or outside messages."
        request = (request or "").strip()
        if len(request) < 8:
            return "Tell me in a sentence or two what the skill should do."

        def worker():
            try:
                self.notify(self.propose(request, owner_turn=state["turn"]))
            except Exception as exc:
                self.notify(f"I couldn't draw up the skill plan: {str(exc)[:200]}")

        threading.Thread(target=worker, daemon=True, name="skill-plan").start()
        return "On it. I'm drawing up a precise plan with my deep-thinking model; I'll read it to you in under a minute."

    def propose(self, request: str, owner_turn: int | None = None) -> str:
        state = _owner()
        if owner_turn is None:
            if not state["trusted_source"] or state["source"] == "routine":
                _audit("PROPOSE_REFUSED", source=state["source"], request=request[:200])
                return "I only build skills when you ask me directly, not from routines or outside messages."
            owner_turn = state["turn"]
        request = (request or "").strip()
        if len(request) < 8:
            return "Tell me in a sentence or two what the skill should do."
        spec, model = brain.think_json(SPEC_SYSTEM, f"Owner's request:\n{request}")
        if not spec.get("feasible", True):
            _audit("PROPOSE_INFEASIBLE", request=request[:200], reason=spec.get("infeasible_reason"))
            return f"I can't build that safely as a skill: {spec.get('infeasible_reason') or 'it needs powers skills are not allowed to have.'}"
        perms = spec.setdefault("permissions", {})
        perms["network_domains"] = sorted({str(d).lower().strip().strip(".") for d in perms.get("network_domains", []) if d})
        for key in ("read_paths", "write_paths"):
            ok, bad = _expand_paths(perms.get(key))
            perms[key] = ok
            if bad:
                spec.setdefault("questions", []).append(f"I refused access to {', '.join(bad)} (too broad or protected). Which specific folder should I use instead?")
        slug = registry.slugify(spec.get("name", "skill"))
        if (registry.load_manifest(slug) or {}).get("status") == "approved":
            slug = registry.slugify(f"{slug}_v{int(time.time()) % 10000}")
        spec["slug"] = slug
        spec["request"] = request
        self._proposals[slug] = {"spec": spec, "turn": max(owner_turn, state["turn"]), "model": model, "at": time.time()}
        _audit("PROPOSED", slug=slug, model=model, permissions=perms)
        return self._spec_text(spec) + (
            "\n\nQuestions first: " + " ".join(spec["questions"]) if spec.get("questions") else
            "\n\nSay 'confirm' and I'll build it, or tell me what to change.")

    @staticmethod
    def _spec_text(spec: dict) -> str:
        p = spec.get("permissions", {})
        access = []
        if p.get("network_domains"):
            access.append("internet: " + ", ".join(p["network_domains"]))
        if p.get("read_paths"):
            access.append("read: " + ", ".join(p["read_paths"]))
        if p.get("write_paths"):
            access.append("write: " + ", ".join(p["write_paths"]))
        return (f"Skill plan: {spec.get('name')}. {spec.get('summary')}\n"
                f"It will: {'; '.join(spec.get('does', []))}.\n"
                f"It will NOT: {'; '.join(spec.get('does_not', [])) or 'anything beyond the above'}.\n"
                f"Access: {', '.join(access) if access else 'none (no internet, no files)'}. Risk: {spec.get('risk', 'low')}.")

    # ── 2. build (background) ────────────────────────────────────────────
    def build(self, slug: str = "") -> str:
        prop = self._find_proposal(slug)
        if prop is None:
            return "There's no skill plan waiting. Describe the skill first and I'll draw up a plan."
        state = _owner()
        if state["source"] == "routine" or not state["trusted_source"]:
            return "Building a skill needs your direct go-ahead."
        if state["turn"] <= prop["turn"] or not _contains_any(state["text"], CONFIRM_WORDS):
            return "I need you to confirm the plan first. Say 'confirm' to build it."
        if prop["spec"].get("questions"):
            return "The plan still has open questions. Answer them so I can update the plan first."
        with self._lock:
            if self._building:
                return f"I'm already building '{self._building}'. One at a time keeps it careful."
            self._building = prop["spec"]["slug"]
        threading.Thread(target=self._build_worker, args=(prop,), daemon=True, name="skill-forge").start()
        _audit("BUILD_STARTED", slug=prop["spec"]["slug"])
        return (f"Building '{prop['spec']['name']}' now with a deep-thinking model: tests first, then code, "
                "then security checks and a review. This usually takes one to three minutes. I'll tell you when it's ready.")

    def _find_proposal(self, slug: str) -> dict | None:
        if slug and registry.slugify(slug) in self._proposals:
            return self._proposals[registry.slugify(slug)]
        if self._proposals:
            return max(self._proposals.values(), key=lambda p: p["at"])
        return None

    def _build_worker(self, prop: dict) -> None:
        spec = prop["spec"]
        slug = spec["slug"]
        d = registry.skill_dir(slug)
        report = {"rounds": [], "models": []}
        try:
            version = registry.snapshot_version(slug)
            d.mkdir(parents=True, exist_ok=True)
            perms = spec["permissions"]
            net = " requests" if perms.get("network_domains") else ""
            rules = RULES_FOR_CODE.replace("{net}", net)
            spec_json = json.dumps({k: v for k, v in spec.items() if k not in ("request",)}, indent=2)

            # tests first
            tests_src = None
            for _ in range(3):
                text, model = brain.think(TESTS_SYSTEM, f"Approved spec:\n{spec_json}\n\n{TESTS_RULES}\n\n{rules}")
                report["models"].append(f"tests: {model}")
                cand = brain.extract_code(text)
                t_scan = scanner.scan(cand, perms, extra_allowed=TEST_EXTRA_IMPORTS, require_run=False)
                if t_scan["ok"]:
                    tests_src = cand
                    break
            if tests_src is None:
                raise RuntimeError("Couldn't produce safe tests for this spec: " + "; ".join(t_scan["violations"][:3]))
            (d / "test_skill.py").write_text(tests_src, encoding="utf-8")

            # code, then scan + sandbox-test + fix loop
            text, model = brain.think(CODE_SYSTEM, f"Approved spec:\n{spec_json}\n\n{rules}\n\nThese tests must pass "
                                      f"(do not change them):\n```python\n{tests_src}```\n\nReturn skill.py in one ```python block.")
            report["models"].append(f"code: {model}")
            code = brain.extract_code(text)
            passed = False
            scan_result: dict = {}
            test_result: dict = {}
            for rnd in range(1, MAX_FIX_ROUNDS + 1):
                scan_result = scanner.scan(code, perms)
                if scan_result["ok"]:
                    (d / "skill.py").write_text(code, encoding="utf-8")
                    test_result = run_in_sandbox(d, "test", perms, timeout=90)
                    tests = test_result.get("tests") or []
                    failing = [t for t in tests if not t.get("passed")]
                    report["rounds"].append({"round": rnd, "scan": "ok", "passed": len(tests) - len(failing),
                                             "total": len(tests), "error": test_result.get("error")})
                    if test_result.get("ok") and tests and not failing:
                        passed = True
                        break
                    problem = (test_result.get("error") or "") + "\n" + "\n".join(
                        f"- {t['test']}: {t.get('error')}\n{t.get('trace', '')}" for t in failing)
                else:
                    report["rounds"].append({"round": rnd, "scan": "rejected", "violations": scan_result["violations"]})
                    problem = "The security scanner rejected the code:\n" + "\n".join("- " + v for v in scan_result["violations"])
                if rnd == MAX_FIX_ROUNDS:
                    break
                text, model = brain.think(CODE_SYSTEM, f"Approved spec:\n{spec_json}\n\n{rules}\n\nTests (fixed, do not change):\n"
                                          f"```python\n{tests_src}```\n\nYour current skill.py:\n```python\n{code}```\n\n"
                                          f"Problem to fix:\n{problem[:4000]}\n\nThink about the root cause, then return the full "
                                          "corrected skill.py in one ```python block.")
                report["models"].append(f"fix {rnd}: {model}")
                code = brain.extract_code(text)
            (d / "skill.py").write_text(code, encoding="utf-8")

            # independent review (prefer a different model than the builder)
            review = {"verdict": "fail", "plain_summary": "Not reviewed because the build did not pass."}
            if passed:
                review, rmodel = brain.think_json(
                    REVIEW_SYSTEM,
                    f"Approved spec:\n{spec_json}\n\nCode:\n```python\n{code}```\n\nTests:\n```python\n{tests_src}```\n\n"
                    f"Scanner facts: {json.dumps(scan_result.get('facts', {}))}\n\nReturn JSON: "
                    '{"verdict": "pass|concerns|fail", "matches_spec": true, "scope_creep": [], "hidden_behaviour": [], '
                    '"bugs": [], "plain_summary": "2-3 sentences for the owner"}')
                report["models"].append(f"review: {rmodel}")

            status = "ready_for_review" if passed and review.get("verdict") != "fail" else "failed"
            manifest = {
                "name": spec.get("name"), "slug": slug, "version": version, "summary": spec.get("summary"),
                "spec": spec, "permissions": perms, "risk": spec.get("risk", "low"), "status": status,
                "params": spec.get("params", {}), "built_at": datetime.now().isoformat(timespec="seconds"),
                "build": report, "scan": scan_result, "tests": test_result.get("tests") or [],
                "review": review, "sha256": registry.sha256_file(d / "skill.py"),
                "ready_turn": _owner()["turn"],
            }
            registry.save_manifest(slug, manifest)
            page = d / "REVIEW.html"
            page.write_text(render_review(manifest, code, tests_src), encoding="utf-8")
            _audit("BUILD_DONE", slug=slug, status=status, rounds=len(report["rounds"]), review=review.get("verdict"))
            self._proposals.pop(slug, None)
            try:
                self.opener(str(page))
            except Exception:
                pass
            if status == "ready_for_review":
                n = len(manifest["tests"])
                self.notify(f"Skill '{spec['name']}' is built: {n} of {n} tests pass, security scan clean, "
                            f"review verdict '{review.get('verdict')}'. {review.get('plain_summary', '')} "
                            f"I've opened the review page. Say 'approve {spec['name']}' when you're happy with it.")
            else:
                why = review.get("plain_summary") if passed else "it didn't pass all its tests or security checks"
                self.notify(f"I couldn't finish '{spec['name']}' to my standard: {why}. Nothing was enabled. "
                            "The review page shows exactly what went wrong.")
        except Exception as exc:
            _audit("BUILD_ERROR", slug=slug, error=str(exc)[:300])
            self.notify(f"The skill build for '{spec.get('name')}' failed: {str(exc)[:200]}. Nothing was enabled.")
        finally:
            with self._lock:
                self._building = None

    # ── 4. approve ───────────────────────────────────────────────────────
    def approve(self, slug: str) -> str:
        m = self._resolve(slug)
        if m is None:
            return "I couldn't find that skill."
        state = _owner()
        if state["source"] == "routine" or not state["trusted_source"]:
            _audit("APPROVE_REFUSED", slug=m["slug"], source=state["source"])
            return "Only you can approve a skill, directly. Routines and outside messages can't."
        if not _contains_any(state["text"], APPROVE_WORDS) or state["turn"] <= int(m.get("ready_turn", -1)):
            _audit("APPROVE_REFUSED", slug=m["slug"], reason="no explicit owner approval")
            return f"To enable '{m['name']}', say in your own words: 'approve {m['name']}'."
        if m.get("status") != "ready_for_review":
            return f"'{m['name']}' can't be approved: its status is {m.get('status')}."
        if registry.sha256_file(registry.skill_dir(m["slug"]) / "skill.py") != m.get("sha256"):
            return "The code changed since it was reviewed. I won't approve it. Rebuild it to review again."
        digest = registry.approve(m["slug"])
        _audit("APPROVED", slug=m["slug"], sha256=digest, by=state["source"])
        return f"Approved. '{m['name']}' is now one of my skills, locked to fingerprint {digest[:12]}."

    def _resolve(self, key: str) -> dict | None:
        key = (key or "").strip()
        m = registry.load_manifest(key)
        if m:
            return m
        low = key.lower()
        for s in registry.list_skills():
            if low and (low in s.get("name", "").lower() or low in s.get("slug", "")):
                return s
        pending = [s for s in registry.list_skills() if s.get("status") == "ready_for_review"]
        return pending[-1] if (not key and pending) else None

    # ── 5. run ───────────────────────────────────────────────────────────
    def run_skill(self, slug: str, params: dict) -> str:
        m = self._resolve(slug)
        if m is None:
            return "I don't have a skill by that name. Say 'list my skills' to see them."
        ok, why = registry.verify(m["slug"])
        if not ok:
            _audit("RUN_BLOCKED", slug=m["slug"], reason=why)
            return why
        r = run_in_sandbox(registry.skill_dir(m["slug"]), "run", m.get("permissions") or {}, params or {}, timeout=60)
        _audit("RUN", slug=m["slug"], ok=r.get("ok"), error=r.get("error"))
        if not r.get("ok"):
            return f"The skill '{m['name']}' hit a problem: {r.get('error')}"
        res = r.get("result")
        return res if isinstance(res, str) else json.dumps(res, ensure_ascii=False, default=str)[:4000]

    # ── management ───────────────────────────────────────────────────────
    def list_text(self) -> str:
        skills = registry.list_skills()
        if not skills:
            return "No skills yet. Ask me to build one."
        return "\n".join(f"- {s['name']} ({s['slug']}): {s.get('summary', '')} [{s.get('status')}]" for s in skills)

    def approved_catalog(self) -> str:
        lines = []
        for s in registry.list_skills():
            if s.get("status") == "approved":
                params = ", ".join(f"{k} ({v.get('type', 'string')})" for k, v in (s.get("params") or {}).items())
                lines.append(f"- {s['slug']}: {s.get('summary', '')} Params: {params or 'none'}")
        return "\n".join(lines)

    def handle(self, args: dict) -> str:
        action = str(args.get("action") or "list").lower()
        target = str(args.get("skill") or args.get("name") or "")
        if action == "propose":
            return self.propose_async(str(args.get("request") or ""))
        if action == "build":
            return self.build(target)
        if action == "approve":
            return self.approve(target)
        if action in ("reject", "disable"):
            m = self._resolve(target)
            if not m:
                return "I couldn't find that skill."
            registry.revoke(m["slug"])
            m["status"] = "disabled" if action == "disable" else "rejected"
            registry.save_manifest(m["slug"], m)
            _audit(action.upper(), slug=m["slug"])
            return f"'{m['name']}' is {m['status']}. It won't run."
        if action in ("remove", "delete"):
            m = self._resolve(target)
            if not m or not registry.remove(m["slug"]):
                return "I couldn't find that skill."
            _audit("REMOVED", slug=m["slug"])
            return f"Removed '{m['name']}'. A copy is kept in skills/.removed in case you want it back."
        if action == "show":
            m = self._resolve(target)
            if not m:
                return "I couldn't find that skill."
            page = registry.skill_dir(m["slug"]) / "REVIEW.html"
            if page.exists():
                try:
                    self.opener(str(page))
                except Exception:
                    pass
            return self._spec_text(m.get("spec") or m) + f"\nStatus: {m.get('status')}."
        return self.list_text()


def render_review(m: dict, code: str, tests: str) -> str:
    e = html.escape
    spec = m.get("spec", {})
    perms = m.get("permissions", {})
    review = m.get("review", {})
    scan = m.get("scan", {})
    tests_res = m.get("tests", [])
    passed = sum(1 for t in tests_res if t.get("passed"))
    ok = m.get("status") == "ready_for_review"
    verdict = review.get("verdict", "n/a")

    def items(xs):
        return "".join(f"<li>{e(str(x))}</li>" for x in xs) or "<li class=muted>none</li>"

    def badge(label, good):
        return f'<span class="badge {"good" if good else "bad"}">{e(label)}</span>'

    perm_rows = "".join(
        f"<tr><td>{e(k)}</td><td>{e(', '.join(v) if v else 'none')}</td></tr>"
        for k, v in (("Internet", perms.get("network_domains", [])), ("Read folders", perms.get("read_paths", [])),
                     ("Write folders", perms.get("write_paths", []))))
    test_rows = "".join(
        f"<tr><td>{'✅' if t.get('passed') else '❌'}</td><td>{e(t.get('test', ''))}</td><td>{e(t.get('error', '') or '')}</td></tr>"
        for t in tests_res) or "<tr><td colspan=3 class=muted>no test results</td></tr>"
    rounds = "".join(f"<li>Round {r['round']}: " + (f"tests {r.get('passed')}/{r.get('total')}" if r.get('scan') == 'ok'
                     else "scanner rejected: " + e('; '.join(r.get('violations', [])))) + "</li>" for r in m.get("build", {}).get("rounds", []))
    return f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Skill review: {e(m.get('name', ''))}</title>
<style>
:root{{--bg:#0b0d12;--card:#141824;--line:#232a3a;--text:#e6e9f0;--muted:#8a93a6;--good:#3ddc97;--bad:#ff5d5d;--warn:#ffb020;--accent:#ffbe1a}}
body{{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 "Segoe UI",system-ui,sans-serif}}
main{{max-width:960px;margin:0 auto;padding:28px 16px 60px}}
h1{{margin:0 0 4px;font-size:26px}} h2{{font-size:16px;margin:0 0 10px;color:var(--accent);letter-spacing:.02em}}
.sub{{color:var(--muted);margin-bottom:18px}} .muted{{color:var(--muted)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin:14px 0}}
.badges{{display:flex;flex-wrap:wrap;gap:8px;margin:12px 0}}
.badge{{padding:4px 10px;border-radius:999px;font-weight:600;font-size:13px;border:1px solid}}
.good{{color:var(--good);border-color:var(--good)}} .bad{{color:var(--bad);border-color:var(--bad)}}
table{{width:100%;border-collapse:collapse}} td{{padding:6px 8px;border-top:1px solid var(--line);vertical-align:top}}
pre{{background:#0a0c10;border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto;font:13px/1.45 Consolas,monospace;white-space:pre}}
.grid{{display:grid;grid-template-columns:1fr 1fr;gap:14px}} @media(max-width:700px){{.grid{{grid-template-columns:1fr}}}}
.cta{{border-color:var(--accent)}} code{{color:var(--accent)}}
</style></head><body><main>
<h1>{e(m.get('name', ''))}</h1>
<div class="sub">{e(m.get('summary', ''))} · version {m.get('version')} · built {e(m.get('built_at', ''))}</div>
<div class="badges">{badge(f"Tests {passed}/{len(tests_res)}", tests_res and passed == len(tests_res))}{badge("Security scan clean" if scan.get('ok') else "Security scan FAILED", scan.get('ok'))}{badge(f"Review: {verdict}", verdict == 'pass')}{badge("Ready for your approval" if ok else "Not approvable", ok)}</div>
<div class="card cta"><h2>Your decision</h2>{"Read this page. If it's what you asked for, tell Jarvis: <code>approve " + e(m.get('name', '')) + "</code>. Nothing runs until you do." if ok else "This build did not meet the bar, so it cannot be approved. Ask Jarvis to try again or adjust the plan."}</div>
<div class="card"><h2>What you asked for</h2><p>{e(spec.get('request', ''))}</p></div>
<div class="grid"><div class="card"><h2>It will</h2><ul>{items(spec.get('does', []))}</ul></div>
<div class="card"><h2>It will NOT</h2><ul>{items(spec.get('does_not', []))}</ul></div></div>
<div class="card"><h2>Permissions (enforced by the sandbox)</h2><table>{perm_rows}</table>
<p class="muted">Never allowed for any skill: running programs, deleting files, reading Jarvis's keys, memory or config.</p></div>
<div class="card"><h2>Independent review</h2><p>{e(review.get('plain_summary', ''))}</p>
<div class="grid"><div><b>Scope creep</b><ul>{items(review.get('scope_creep', []))}</ul><b>Hidden behaviour</b><ul>{items(review.get('hidden_behaviour', []))}</ul></div>
<div><b>Bugs</b><ul>{items(review.get('bugs', []))}</ul></div></div></div>
<div class="card"><h2>Automatic security scan (no AI)</h2><ul>{items(scan.get('violations', [])) if scan.get('violations') else '<li>No violations.</li>'}</ul>
<p class="muted">Imports: {e(', '.join(scan.get('facts', {}).get('imports', [])) or 'none')} · Web addresses: {e(', '.join(scan.get('facts', {}).get('urls', [])) or 'none')}</p></div>
<div class="card"><h2>Tests (written before the code)</h2><table>{test_rows}</table><ul class="muted">{rounds}</ul></div>
<div class="card"><h2>Code</h2><pre>{e(code)}</pre></div>
<div class="card"><h2>Tests code</h2><pre>{e(tests)}</pre></div>
<p class="muted">Built with: {e(', '.join(m.get('build', {}).get('models', [])))} · Fingerprint (SHA-256): {e(m.get('sha256', ''))}</p>
</main></body></html>"""


_FORGE: SkillForge | None = None


def get_forge(**kwargs) -> SkillForge:
    global _FORGE
    if _FORGE is None:
        _FORGE = SkillForge(**kwargs)
    else:
        for k, v in kwargs.items():
            if v is not None:
                setattr(_FORGE, k, v)
    return _FORGE
