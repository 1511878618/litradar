#!/usr/bin/env python3
"""Dependency-free test runner.

``python tests/run_tests.py`` discovers ``tests/test_*.py`` and calls every
``test_*`` function.  No pytest, no plugins: the suite must run on a bare Python
3.10+ install, because that is where litradar is supposed to work.
"""

from __future__ import annotations

import importlib.util
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for entry in (str(ROOT / "src"), str(TESTS)):
    if entry not in sys.path:
        sys.path.insert(0, entry)


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[path.stem] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main(argv: list[str]) -> int:
    selected = set(argv[1:])
    total = 0
    failures: list[tuple[str, str]] = []
    files = sorted(TESTS.glob("test_*.py"))
    if selected:
        files = [path for path in files if path.stem in selected or path.stem.replace("test_", "") in selected]
    for path in files:
        module = _load(path)
        tests = sorted(
            (name, value)
            for name, value in vars(module).items()
            if name.startswith("test_") and callable(value)
        )
        passed = 0
        for name, function in tests:
            total += 1
            try:
                function()
                passed += 1
            except Exception:  # noqa: BLE001 - a failing test must not stop the suite
                failures.append((f"{path.stem}.{name}", traceback.format_exc()))
        mark = "ok" if passed == len(tests) else "FAIL"
        print(f"  {path.name:<28} {passed:>2}/{len(tests):<2} {mark}")
    print()
    for label, trace in failures:
        print(f"FAIL {label}\n{trace}")
    print(f"TOTAL {total} passed" + (f", {len(failures)} failed" if failures else ""))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
