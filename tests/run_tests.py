"""Run Jarvis's tests without installing pytest.

    .venv\\Scripts\\python.exe tests\\run_tests.py            # all test files
    .venv\\Scripts\\python.exe tests\\run_tests.py security   # files whose name contains "security"

Supports plain test_* functions, the tmp_path and monkeypatch fixtures, and
@pytest.mark.parametrize. (If you install pytest, `python -m pytest tests` works too.)
"""

import importlib
import os

# Tests must never re-encrypt the real key files on this PC.
os.environ.setdefault("JARVIS_SECRETS_MIGRATE", "0")
import inspect
import sys
import tempfile
import traceback
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

if "pytest" not in sys.modules:
    try:
        import pytest  # noqa: F401
    except ImportError:
        fake = types.ModuleType("pytest")

        class _Mark:
            def parametrize(self, names, values):
                def deco(fn):
                    fn._params = ([n.strip() for n in names.split(",")], values)
                    return fn
                return deco

        fake.mark = _Mark()
        sys.modules["pytest"] = fake


class MonkeyPatch:
    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value=None):
        if isinstance(target, str):  # "pkg.module.attr" form
            mod_name, attr = target.rsplit(".", 1)
            obj = importlib.import_module(mod_name)
            value, name = name, attr
        else:
            obj = target
        self._undo.append((obj, name, getattr(obj, name)))
        setattr(obj, name, value)

    def undo(self):
        for obj, name, val in reversed(self._undo):
            setattr(obj, name, val)


def main() -> int:
    filt = sys.argv[1] if len(sys.argv) > 1 else ""
    files = sorted(p.stem for p in (ROOT / "tests").glob("test_*.py") if filt in p.stem)
    passed = failed = 0
    for stem in files:
        try:
            mod = importlib.import_module(f"tests.{stem}")
        except Exception:
            failed += 1
            print(f"FAIL  tests.{stem} (import error)")
            traceback.print_exc()
            continue
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if not name.startswith("test_") or fn.__module__ != mod.__name__:
                continue
            cases = [{}]
            if hasattr(fn, "_params"):
                names, values = fn._params
                cases = [dict(zip(names, v if isinstance(v, (tuple, list)) else (v,))) for v in values]
            for case in cases:
                mp = MonkeyPatch()
                with tempfile.TemporaryDirectory() as td:
                    kwargs = dict(case)
                    params = inspect.signature(fn).parameters
                    if "tmp_path" in params:
                        kwargs["tmp_path"] = Path(td)
                    if "monkeypatch" in params:
                        kwargs["monkeypatch"] = mp
                    label = f"{stem}::{name}" + (f"{list(case.values())}" if case else "")
                    try:
                        fn(**kwargs)
                        passed += 1
                        print(f"PASS  {label}")
                    except Exception:
                        failed += 1
                        print(f"FAIL  {label}")
                        traceback.print_exc()
                    finally:
                        mp.undo()
    print(f"\n{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
