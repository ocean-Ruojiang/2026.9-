"""Portable source-checkout GUI launcher."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
if __name__ == "__main__":
    from problem2_gui import main
    main()
