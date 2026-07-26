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


def main() -> int:
    passed, failed = 0, 0
    for name, fn in sorted(vars(test_kdl_edit).items()):
        if not name.startswith("test_") or not callable(fn):
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
