"""Launch the same simulator core without a desktop."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
if __name__ == "__main__":
    from simulator_cli import main
    raise SystemExit(main())
