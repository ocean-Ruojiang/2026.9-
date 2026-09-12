"""Portable process-parallel batch launcher."""
import multiprocessing
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
if __name__ == "__main__":
    multiprocessing.freeze_support()
    from problem2_parallel import _cli_main
    raise SystemExit(_cli_main())
