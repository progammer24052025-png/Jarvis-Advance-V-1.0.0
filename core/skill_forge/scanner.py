"""Deterministic code scanner: no AI involved, so it can't be talked out of its job.

Reads the skill's source as a syntax tree and lists everything it could do.
Anything outside the approved permissions is a violation, and any violation
rejects the skill.
"""

from __future__ import annotations

import ast
import re
from urllib.parse import urlparse

# Modules a skill may import without special permission (pure computation / documents).
SAFE_MODULES = {
    "json", "re", "math", "cmath", "statistics", "datetime", "time", "random", "string",
    "collections", "itertools", "functools", "operator", "decimal", "fractions", "csv",
    "textwrap", "unicodedata", "html", "base64", "hashlib", "hmac", "uuid", "dataclasses",
    "typing", "enum", "pathlib", "io", "difflib", "calendar", "zoneinfo", "bisect", "heapq",
    "copy", "pprint", "numbers", "secrets", "struct", "zlib", "gzip", "zipfile",
    "urllib.parse", "xml.etree.ElementTree", "html.parser",
    # installed document / data libraries
    "bs4", "openpyxl", "docx", "pptx", "PIL", "reportlab", "numpy",
}
NETWORK_MODULES = {"requests", "urllib.request", "urllib3", "http.client"}
# Never allowed: process control, raw sockets, native code, interpreter internals, persistence tricks.
FORBIDDEN_MODULES = {
    "os", "sys", "subprocess", "socket", "ssl", "ctypes", "cffi", "winreg", "_winapi", "msvcrt",
    "importlib", "imp", "builtins", "pickle", "marshal", "shelve", "dill", "shutil", "tempfile",
    "threading", "multiprocessing", "concurrent", "asyncio", "signal", "inspect", "code",
    "codeop", "gc", "atexit", "runpy", "pkgutil", "site", "sysconfig", "platform", "getpass",
    "keyring", "win32api", "win32con", "win32crypt", "pywintypes", "webbrowser", "smtplib",
    "ftplib", "telnetlib", "paramiko", "pyautogui", "pynput", "keyboard", "mouse",
}
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "globals", "locals", "vars",
                   "breakpoint", "input", "help", "memoryview", "setattr", "delattr"}
SECRET_HINTS = ("api_keys", "api_key", "telegram_bot", "discord_bot", "smart_home.key",
                "long_term.json", "workspace_store", "identity.json", "security_baseline",
                "skill_approvals", "guardian", ".ssh", "credentials", "cookies", "login data")
URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.I)
IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


def _top(name: str) -> str:
    return name.split(".")[0]


def _module_allowed(name: str, network: bool) -> str | None:
    """Return a violation message, or None if the import is fine."""
    if name in FORBIDDEN_MODULES or _top(name) in FORBIDDEN_MODULES:
        return f"imports forbidden module '{name}'"
    if name in NETWORK_MODULES or _top(name) in {_top(m) for m in NETWORK_MODULES}:
        if name == "urllib.parse":
            return None
        return None if network else f"imports network module '{name}' but has no internet permission"
    if name in SAFE_MODULES or _top(name) in {_top(m) for m in SAFE_MODULES}:
        return None
    return f"imports unapproved module '{name}'"


def _domain_ok(host: str, allowed: list[str]) -> bool:
    host = (host or "").lower().strip(".")
    return any(host == d or host.endswith("." + d) for d in allowed)


def scan(source: str, permissions: dict, *, extra_allowed: tuple = (), require_run: bool = True) -> dict:
    """Return {"ok": bool, "violations": [...], "facts": {...}} for the given skill source."""
    perms = permissions or {}
    domains = [d.lower().strip() for d in perms.get("network_domains", []) if d]
    network = bool(domains)
    violations: list[str] = []
    facts = {"imports": set(), "urls": set(), "file_ops": 0, "defines_run": False}

    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return {"ok": False, "violations": [f"syntax error: {exc}"], "facts": {}}

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                facts["imports"].add(a.name)
                if a.name in extra_allowed or _top(a.name) in extra_allowed:
                    continue
                if (v := _module_allowed(a.name, network)):
                    violations.append(v)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                violations.append("uses relative imports")
            facts["imports"].add(mod)
            if mod in extra_allowed or _top(mod) in extra_allowed:
                continue
            if (v := _module_allowed(mod, network)):
                violations.append(v)
        elif isinstance(node, ast.Call):
            fn = node.func
            name = fn.id if isinstance(fn, ast.Name) else (fn.attr if isinstance(fn, ast.Attribute) else "")
            if name in FORBIDDEN_CALLS:
                violations.append(f"calls forbidden function '{name}()'")
            if name == "getattr" and len(node.args) >= 2:
                arg = node.args[1]
                if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str) and not arg.value.startswith("_")):
                    violations.append("uses getattr() with a dynamic or private name")
            if name in {"open", "write_text", "write_bytes", "read_text", "read_bytes", "unlink",
                        "rmdir", "rename", "replace", "touch", "mkdir", "save"}:
                facts["file_ops"] += 1
            if name in {"unlink", "rmdir", "rmtree"}:
                violations.append(f"deletes files ('{name}'); skills may not delete anything")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("__") and node.attr.endswith("__") and node.attr not in {"__name__", "__init__", "__str__", "__repr__", "__eq__", "__len__", "__iter__", "__next__", "__enter__", "__exit__", "__dict__"}:
                violations.append(f"touches interpreter internals ('{node.attr}')")
            if node.attr == "__dict__":
                violations.append("touches interpreter internals ('__dict__')")
        elif isinstance(node, ast.Name) and node.id in {"__builtins__", "__loader__", "__spec__"}:
            violations.append(f"touches interpreter internals ('{node.id}')")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            low = node.value.lower()
            for hint in SECRET_HINTS:
                if hint in low:
                    violations.append(f"mentions a protected Jarvis/secret file ('{hint}')")
            for url in URL_RE.findall(node.value):
                host = urlparse(url).hostname or ""
                facts["urls"].add(host)
                if IP_RE.match(host):
                    violations.append(f"contacts a raw IP address ({host}); only named domains are allowed")
                elif not network:
                    violations.append(f"contains a web address ({host}) but has no internet permission")
                elif not _domain_ok(host, domains):
                    violations.append(f"contacts '{host}', which is not in its approved domains {domains}")
        elif isinstance(node, (ast.Global, ast.Nonlocal)):
            pass
        if isinstance(node, ast.FunctionDef) and node.name == "run" and node.col_offset == 0:
            facts["defines_run"] = True

    if require_run and not facts["defines_run"]:
        violations.append("does not define a top-level run(params) function")
    if facts["file_ops"] and not (perms.get("read_paths") or perms.get("write_paths")):
        violations.append("works with files but was not granted any folder")

    out_facts = {k: (sorted(v) if isinstance(v, set) else v) for k, v in facts.items()}
    return {"ok": not violations, "violations": sorted(set(violations)), "facts": out_facts}
