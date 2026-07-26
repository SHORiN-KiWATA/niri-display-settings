"""Zero-dependency test runner (pytest-compatible test style).

Usage: python tests/run.py
"""
import inspect
import sys
import tempfile
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import test_kdl_edit  # noqa: E402
import test_layout  # noqa: E402

MODULES = [test_kdl_edit, test_layout]


def main() -> int:
    passed, failed = 0, 0
    tests = {f"{m.__name__}.{n}": fn
             for m in MODULES for n, fn in vars(m).items()}
    for name, fn in sorted(tests.items()):
        if not name.split(".")[-1].startswith("test_") or not callable(fn):
            continue
        params = inspect.signature(fn).parameters
        try:
            if "tmp_path" in params:
                with tempfile.TemporaryDirectory() as d:
                    fn(tmp_path=Path(d))
            else:
                fn()
            passed += 1
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"{passed} passed, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
