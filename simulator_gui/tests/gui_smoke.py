"""GUI integration test. Opens a withdrawn test window and ephemeral local HTTP port."""
import json
from pathlib import Path
import socket
import sys
import tempfile
from unittest.mock import patch
from urllib.request import Request, build_opener, ProxyHandler

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from interference_simulator_gui import InterferenceSimulatorApp

def main():
    app = InterferenceSimulatorApp()
    app.withdraw()
    try:
        app.random_sources()
        assert 10 <= len(app.sources) <= 16
        assert all(s.kind == "omni" for s in app.sources)
        first = [s.to_dict() for s in app.sources]
        app.random_sources()
        assert [s.to_dict() for s in app.sources] == first
        app.random_mixed_sources()
        assert 10 <= len(app.sources) <= 16
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "sources.json"
            with patch("interference_simulator_gui.filedialog.asksaveasfilename", return_value=str(target)):
                app.export_sources()
            assert len(json.loads(target.read_text(encoding="utf-8"))) == len(app.sources)
            with patch("interference_simulator_gui.filedialog.askopenfilename", return_value=str(ROOT / "examples" / "sources.json")):
                app.import_sources()
            assert len(app.sources) == 2
        app.fit_view()
        app._redraw_map()
        assert app.canvas.find_all()
        assert app._config_from_ui().random_seed == int(app.random_seed_var.get())
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        app.port_var.set(str(port))
        app.robot_id_var.set("local")
        with patch("interference_simulator_gui.messagebox.showerror", side_effect=AssertionError):
            app.start_server()
        assert app._server_is_running()
        opener = build_opener(ProxyHandler({}))
        for index, (path, extra) in enumerate([
            ("/enter", {}), ("/measure", {"position": {"x": 0, "y": 0}, "channel": 1}),
            ("/clear", {"position": {"x": 100, "y": 0}, "channel": 1}), ("/exit", {}),
        ]):
            body = dict(arena_id="default", robot_id="local", request_id=str(index), **extra)
            request = Request(f"http://127.0.0.1:{port}{path}", data=json.dumps(body).encode(),
                              headers={"Content-Type": "application/json"})
            with opener.open(request, timeout=3) as response:
                assert json.load(response)["accepted"]
        app._poll_events()
        app._redraw_map()
        assert len(app.state.behavior_history()) == 3  # enter, measure, clear; exit adds no movement
        app.reset_field()
        assert not app._server_is_running()
        assert all(not s.cleared for s in app.sources)
        print("GUI PASS: seeded layouts, mixed sources, import/export, map, HTTP actions, trajectory, reset")
    finally:
        app._on_close()

if __name__ == "__main__":
    main()
