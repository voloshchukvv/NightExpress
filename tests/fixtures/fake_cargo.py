"""Stand-in for `cargo` in tests: replays a recorded build.

Usage: python fake_cargo.py <scenario> build <cargo args...>
"""

import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "cargo"
EXIT_CODES = {"success": 0, "warning": 0, "compile_error": 101, "link_error": 101, "library": 0}

scenario, *cargo_args = sys.argv[1:]
sys.stderr.write(f"ARGS {' '.join(cargo_args)}\n")
sys.stderr.flush()
if scenario == "slow":  # for cancellation tests
    import time

    time.sleep(30)
    sys.exit(0)
sys.stderr.write((FIXTURES / f"{scenario}.stderr.txt").read_text(encoding="utf-8"))
sys.stdout.write((FIXTURES / f"{scenario}.jsonl").read_text(encoding="utf-8"))
sys.exit(EXIT_CODES[scenario])
