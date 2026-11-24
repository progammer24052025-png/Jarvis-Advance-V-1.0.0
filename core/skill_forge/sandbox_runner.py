"""Runs ONE skill (or its tests) inside a locked-down child process.

Started by sandbox.py as:  python -I sandbox_runner.py <skill_dir> <test|run> <workdir>
Params / manifest arrive as JSON on stdin; the result goes out as one JSON line
on stdout, prefixed with a marker so skill prints can't forge it.

Enforcement is a Python audit hook installed BEFORE the skill is imported. Audit
hooks can't be removed once added. It blocks:
  • opening files outside the approved folders (writes only into approved write
    folders or the private scratch folder), and anything under Jarvis's config/memory
  • network to any host that isn't an approved domain (no network at all if none)
  • launching programs, native code (ctypes), the registry, deleting files
The parent also kills the process on timeout and strips secrets from its environment.
"""

import io
import json
import os
import sys
import traceback

MARK = "@@SKILL_RESULT@@"


def _norm(p: str) -> str:
    return os.path.normcase(os.path.abspath(p))


def main() -> None:
    skill_dir, mode, workdir = sys.argv[1], sys.argv[2], sys.argv[3]
    payload = json.loads(sys.stdin.read() or "{}")
    perms = payload.get("permissions") or {}
    params = payload.get("params") or {}
    jarvis_root = payload.get("jarvis_root") or ""

    domains = [d.lower().strip(".") for d in perms.get("network_domains", []) if d]
    read_roots = [_norm(p) for p in perms.get("read_paths", []) if p]
    write_roots = [_norm(p) for p in perms.get("write_paths", []) if p] + [_norm(workdir)]
    py_roots = {_norm(p) for p in {sys.prefix, sys.base_prefix, sys.exec_prefix, sys.base_exec_prefix}}
    skill_root = _norm(skill_dir)
    data_root = payload.get("data_root") or jarvis_root
    protected = [_norm(os.path.join(jarvis_root, "core"))] if jarvis_root else []
    protected += [_norm(os.path.join(data_root, d)) for d in ("config", "memory", "logs", "skills")] if data_root else []
    protected = [p for p in protected if not skill_root.startswith(p)]  # the skill's own folder stays readable

    def under(path: str, roots) -> bool:
        return any(path == r or path.startswith(r + os.sep) for r in roots)

    def host_ok(host: str) -> bool:
        host = (host or "").lower().strip(".")
        return any(host == d or host.endswith("." + d) for d in domains)

    blocked_events = (
        "subprocess.Popen", "os.system", "os.exec", "os.spawn", "os.posix_spawn", "os.startfile",
        "ctypes.dlopen", "ctypes.dlsym", "ctypes.cdata", "winreg.", "os.remove", "os.unlink",
        "os.rmdir", "shutil.rmtree", "os.rename", "os.kill", "_winapi.CreateProcess",
        "sys.setprofile", "sys.settrace", "sys.addaudithook", "pickle.find_class",
    )

    def hook(event, args):
        if event == "open":
            path, mode_ = args[0], (args[1] or "r")
            flags = args[2] if len(args) > 2 else 0
            if isinstance(path, int):
                return
            p = _norm(os.fsdecode(path))
            writing = isinstance(mode_, str) and any(c in mode_ for c in "wax+")
            writing = writing or bool(flags and (flags & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND)))
            if under(p, protected):
                raise PermissionError(f"Skill blocked: {p} is protected")
            if writing:
                if not under(p, write_roots):
                    raise PermissionError(f"Skill blocked: writing {p} is outside its approved folders")
                return
            if under(p, py_roots) or under(p, [skill_root]) or under(p, read_roots) or under(p, write_roots):
                return
            raise PermissionError(f"Skill blocked: reading {p} is outside its approved folders")
        if event == "socket.getaddrinfo":
            host = args[0]
            host = host.decode() if isinstance(host, bytes) else str(host or "")
            if not domains:
                raise PermissionError("Skill blocked: no internet permission")
            if host not in ("", "localhost") and not host_ok(host):
                raise PermissionError(f"Skill blocked: '{host}' is not an approved domain")
            if host in ("localhost",):
                raise PermissionError("Skill blocked: local network access")
            return
        if event in ("socket.connect", "socket.bind", "socket.sendto"):
            if not domains:
                raise PermissionError("Skill blocked: no internet permission")
            if event == "socket.bind":
                raise PermissionError("Skill blocked: skills may not open listening sockets")
            return
        for b in blocked_events:
            if event == b or (b.endswith(".") and event.startswith(b)) or event.startswith(b + "."):
                raise PermissionError(f"Skill blocked: '{event}' is not allowed for skills")

    # --- capture the skill's own prints so they can't fake our result line
    real_stdout = sys.stdout
    captured = io.StringIO()

    def emit(obj):
        real_stdout.write(MARK + json.dumps(obj, default=str) + "\n")
        real_stdout.flush()

    os.chdir(workdir)
    sys.path.insert(0, skill_dir)
    sys.addaudithook(hook)
    sys.stdout = captured

    try:
        import importlib.util as ilu  # noqa: E402

        def load(name, file):
            spec = ilu.spec_from_file_location(name, os.path.join(skill_dir, file))
            mod = ilu.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
            return mod

        if mode == "run":
            skill = load("skill", "skill.py")
            result = skill.run(params)
            emit({"ok": True, "result": result, "stdout": captured.getvalue()[-2000:]})
        else:
            load("skill", "skill.py")
            tests = load("test_skill", "test_skill.py")
            results = []
            for name in sorted(n for n in dir(tests) if n.startswith("test_")):
                fn = getattr(tests, name)
                if not callable(fn):
                    continue
                try:
                    fn()
                    results.append({"test": name, "passed": True})
                except Exception as exc:  # noqa: BLE001
                    tb = traceback.format_exc(limit=4)
                    results.append({"test": name, "passed": False, "error": f"{type(exc).__name__}: {exc}", "trace": tb[-1200:]})
            emit({"ok": True, "tests": results, "stdout": captured.getvalue()[-2000:]})
    except Exception as exc:  # noqa: BLE001
        emit({"ok": False, "error": f"{type(exc).__name__}: {exc}", "trace": traceback.format_exc(limit=5)[-1500:],
              "stdout": captured.getvalue()[-1000:]})


if __name__ == "__main__":
    main()
