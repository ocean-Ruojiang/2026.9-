"""Headless launcher for the exact desktop simulator core."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import threading
from interference_simulator_core import SimulatorConfig, SimulatorState, load_sources_json, make_server

def main(argv=None):
    parser = argparse.ArgumentParser(description="Local interference simulator HTTP service")
    parser.add_argument("--sources", required=True, type=Path, help="JSON array of sources")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=2027, type=int, help="0 selects an available port")
    parser.add_argument("--robot-id", default="local")
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument("--problem-mode", choices=["custom", "problem3", "problem4"], default="custom")
    parser.add_argument("--real-limit", type=float, default=1200)
    parser.add_argument("--log", type=Path, help="Optional event JSONL file")
    args = parser.parse_args(argv)
    if not 0 <= args.port <= 65535:
        parser.error("port must be 0..65535")
    log_lock = threading.Lock()
    if args.log:
        args.log.parent.mkdir(parents=True, exist_ok=True)
    def event(kind, message):
        if args.log:
            record = {"time": datetime.now(timezone.utc).isoformat(), "kind": kind, "message": message}
            with log_lock, args.log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    try:
        config = SimulatorConfig(expected_robot_id=args.robot_id, random_seed=args.seed,
                                 max_real_duration_s=args.real_limit, problem_mode=args.problem_mode)
        state = SimulatorState(config, load_sources_json(str(args.sources)), event_sink=event)
        server = make_server(args.host, args.port, state)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    try:
        state.start_session()
        print(f"READY http://{args.host}:{server.server_address[1]} robot_id={args.robot_id}", flush=True)
        print("Stop with Ctrl+C; restart the service for a new session.", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop_session("server_stopped")
        server.server_close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
