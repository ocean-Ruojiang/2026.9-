"""Execute the fixed Q4 acceptance suite: 10 random cases and 5 special cases."""
from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from strategy3.local_sim import acceptance_cases, run_suite


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "configs" / "default.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--seed", type=int, default=20260913)
    parser.add_argument("--real-limit", type=float, default=600.)
    parser.add_argument("--virtual-limit", type=float, default=360000.)
    args = parser.parse_args(argv)
    output = args.output or ROOT / "results" / ("acceptance_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    try:
        return run_suite(acceptance_cases(args.seed), args.config, output, args.real_limit, args.virtual_limit)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
