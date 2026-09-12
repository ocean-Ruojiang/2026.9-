"""Launch the portable desktop simulator from any working directory."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
if __name__ == "__main__":
    from interference_simulator_gui import main
    main()
