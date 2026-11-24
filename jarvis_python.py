"""jarvis-python.exe: the small Python that ships next to Jarvis.exe in an installed copy.

An installed Jarvis has no separate Python, but a few features run Python code in their own process:
Skill Forge's sandbox, generated helper scripts, and the website preview server. They call this
instead of `python`. It understands the usual forms:

    jarvis-python.exe script.py [args]      jarvis-python.exe -m module [args]
    jarvis-python.exe -c "code"             (-I, -E, -s, -u, -B and -X opt are accepted and ignored)

Installing packages (-m pip) isn't possible in an installed copy, so it explains that and exits.
"""

from __future__ import annotations

import os
import runpy
import sys


def main(argv: list[str]) -> int:
    args = list(argv[1:])
    while args and args[0] in ("-I", "-E", "-s", "-u", "-B", "-S", "-X"):
        flag = args.pop(0)
        if flag == "-X" and args:
            args.pop(0)                      # e.g. -X utf8
    if not args:
        print("jarvis-python: no script given", file=sys.stderr)
        return 2
    if args[0] == "-c" and len(args) >= 2:
        sys.argv = ["-c", *args[2:]]
        exec(compile(args[1], "<string>", "exec"), {"__name__": "__main__"})
        return 0
    if args[0] == "-m" and len(args) >= 2:
        mod = args[1]
        if mod == "pip":
            print("Installing Python packages isn't available in the installed Jarvis. "
                  "The common packages are already included.", file=sys.stderr)
            return 3
        sys.argv = [mod, *args[2:]]
        runpy.run_module(mod, run_name="__main__", alter_sys=True)
        return 0
    script = os.path.abspath(args[0])
    sys.argv = [script, *args[1:]]
    sys.path.insert(0, os.path.dirname(script))
    runpy.run_path(script, run_name="__main__")
    return 0


if __name__ == "__main__":
    try:
        code = main(sys.argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    sys.exit(code)
