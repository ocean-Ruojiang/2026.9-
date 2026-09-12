"""Requires a desktop. Exercise the real GUI worker, rendering and JSON export."""
import json
from pathlib import Path
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from problem2_gui import Problem2App

def main():
    app = Problem2App()
    app.withdraw()
    try:
        app.coarse_width_var.set("400")
        app.refine_width_var.set("200")
        app.angle_step_var.set("30")
        app.circle_samples_var.set("72")
        app.max_constraints_var.set("96")
        app.coverage_spacing_var.set("200")
        app.safety_margin_var.set("100")
        for mode in ("max", "mean", "both"):
            app.metric_var.set(mode)
            with patch("problem2_gui.messagebox.showerror", side_effect=AssertionError):
                app.run_search()
                deadline = time.monotonic() + 90
                while app.running and time.monotonic() < deadline:
                    app.update()
                    time.sleep(0.01)
                assert not app.running, "GUI worker timeout"
                assert app.result is not None
                app.fit_view()
                app._redraw()
                assert app.canvas.find_all(), "Canvas rendered nothing"
                with tempfile.TemporaryDirectory() as temp:
                    output = Path(temp) / "gui.json"
                    with patch("problem2_gui.filedialog.asksaveasfilename", return_value=str(output)):
                        app.export_json()
                    saved = json.loads(output.read_text(encoding="utf-8"))
                    assert saved["summary"]["metric"] == mode
                    assert saved["coarse"] and saved["refined"]
                    assert "near_optimal" in saved
            app.clear_results()
        print("GUI PASS: max/mean/both computation, canvas, fit view, export, clear")
    finally:
        app.destroy()

if __name__ == "__main__":
    main()
