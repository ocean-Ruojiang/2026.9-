"""Standard-library-only HTTP simulator and subprocess batch harness."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import csv
from datetime import datetime
import json
import math
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import time

from b_sim.core import Simulator, generate_scenario
from b_sim.server import Server

ROOT = Path(__file__).resolve().parent


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def save_run(folder, sim, extra=None):
    sim.expired()
    summary = sim.summary()
    summary.update(extra or {})
    # Ground truth is written by the harness AFTER the strategy has terminated.
    write_json(folder / "scenario.json", sim.scenario)
    write_json(folder / "settings.json", dict(noise=sim.noise, noise_cell_m=sim.noise_cell_m,
               real_limit_s=sim.real_limit_s, window_limit_s=sim.window_limit_s,
               virtual_limit_s=sim.virtual_limit_us / 1_000_000))
    write_json(folder / "summary.json", summary)
    with (folder / "actions.jsonl").open("w", encoding="utf-8") as f:
        for event in sim.events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    return summary


def build_sim(args, seed):
    scenario = (json.loads(Path(args.scenario).read_text(encoding="utf-8-sig")) if args.scenario
                else generate_scenario(seed, args.count, args.layout, args.radius_mode, args.directional_count))
    return Simulator(scenario, args.robot_id, args.noise, args.noise_cell,
                     args.real_limit, args.window_limit, args.virtual_limit)


def stop_child(proc):
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def one_case(args, index, output):
    folder = output / f"case_{index+1:04d}"
    folder.mkdir()
    sim = build_sim(args, args.seed + index)
    server = None
    proc = None
    extra = dict(case_index=index+1, process_exit_code=None, harness_error=None)
    try:
        server = Server(sim, args.port)
        server.start()
        command = args.command or [sys.executable, str(ROOT / "examples" / "coverage_strategy.py")]
        replacements = {"{python}": sys.executable, "{base_url}": server.url,
                        "{robot_id}": args.robot_id, "{run_dir}": str(folder.resolve())}
        command = [str(part) for part in command]
        for token, value in replacements.items():
            command = [p.replace(token, value) for p in command]
        env = os.environ.copy()
        env.update(SIM_BASE_URL=server.url, SIM_ROBOT_ID=args.robot_id,
                   SIM_RUN_DIR=str(folder.resolve()), PYTHONIOENCODING="utf-8",
                   PYTHONUNBUFFERED="1")
        env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        spawn_options = ({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt"
                         else {"start_new_session": True})
        with (folder / "strategy.stdout.log").open("w", encoding="utf-8") as stdout, \
                (folder / "strategy.stderr.log").open("w", encoding="utf-8") as stderr:
            proc = subprocess.Popen(command, cwd=args.cwd or str(ROOT), env=env,
                                    stdout=stdout, stderr=stderr, **spawn_options)
            launched = time.monotonic()
            while proc.poll() is None:
                if sim.expired():
                    break
                if sim.started_at is None and time.monotonic()-launched >= args.startup_timeout:
                    sim.finish("startup_timeout")
                    break
                time.sleep(.02)
            if proc.poll() is None:
                try:
                    proc.wait(timeout=2 if sim.end_reason == "user_exit" else .1)
                except subprocess.TimeoutExpired:
                    stop_child(proc)
            extra["process_exit_code"] = proc.returncode
        if sim.end_reason is None:
            sim.finish("strategy_returned_without_exit" if sim.started_at is not None else "strategy_never_entered")
    except Exception as e:
        extra["harness_error"] = f"{type(e).__name__}: {e}"
        sim.finish("harness_error")
    finally:
        if proc is not None:
            stop_child(proc)
        if server is not None:
            server.shutdown()
            server.server_close()
    extra["run_ok"] = (sim.end_reason == "user_exit" and extra["process_exit_code"] == 0
                       and len(sim.cleared) == len(sim.sources))
    return save_run(folder, sim, extra)


def batch(args, output):
    if args.workers < 1 or args.cases < 1:
        raise ValueError("cases and workers must be positive")
    if args.port and args.workers > 1:
        raise ValueError("Parallel runs require --port 0; a fixed port supports one worker only")
    rows = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(one_case, args, i, output) for i in range(args.cases)]
        for future in as_completed(futures):
            row = future.result()
            rows.append(row)
            print(f"[{len(rows)}/{args.cases}] case={row['case_index']} "
                  f"cleared={row['cleared_count']}/{row['target_count']} "
                  f"virtual={row['virtual_time_s']:.2f}s real={row['real_time_s']:.2f}s "
                  f"end={row['end_reason']}", flush=True)
    rows.sort(key=lambda r: r["case_index"])
    write_json(output / "results.json", rows)
    with (output / "results.csv").open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    completed = [r for r in rows if r["run_ok"]]
    count = sum(r["cleared_count"] for r in rows)
    total_time = sum(r["virtual_time_s"] for r in rows)
    summary = dict(cases=len(rows), successful_runs=len(completed), success_rate=len(completed)/len(rows),
                   overall_clearance_ratio=count / sum(r["target_count"] for r in rows),
                   mean_case_clearance_ratio=statistics.mean(r["clearance_ratio"] for r in rows),
                   total_virtual_time_s=total_time,
                   pooled_time_per_cleared_s=total_time/count if count else None,
                   mean_successful_virtual_time_s=statistics.mean(r["virtual_time_s"] for r in completed) if completed else None,
                   mean_successful_time_per_target_s=statistics.mean(r["average_time_per_cleared_s"] for r in completed) if completed else None,
                   mean_real_time_s=statistics.mean(r["real_time_s"] for r in rows),
                   note="Compare speed together with completeness; failed runs are retained in results.")
    write_json(output / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if len(completed) == len(rows) else 1


def serve(args, output):
    sim = build_sim(args, args.seed)
    server = Server(sim, args.port)
    server.start()
    print(f"Ready: {server.url}  robot_id={args.robot_id}\nOne session; restart for another case.", flush=True)
    try:
        while not sim.expired():
            time.sleep(.05)
    except KeyboardInterrupt:
        sim.finish("manual_stop")
    finally:
        server.shutdown()
        server.server_close()
    result = save_run(output, sim)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def main():
    parser = argparse.ArgumentParser(description="B problem local simulator (Python 3.10+, no dependencies)")
    subs = parser.add_subparsers(dest="mode", required=True)
    for name in ["serve", "batch"]:
        p = subs.add_parser(name)
        p.add_argument("--seed", type=int, default=20260912)
        p.add_argument("--count", type=int, choices=range(10,17))
        p.add_argument("--layout", choices=["uniform", "edge", "clustered", "mixed"], default="uniform")
        p.add_argument("--radius-mode", choices=["random", "minimum", "maximum"], default="random")
        p.add_argument("--directional-count", type=int, default=0, help="0 for Q3; optional Q4 testing")
        p.add_argument("--noise", choices=["smooth", "hash", "zero"], default="smooth")
        p.add_argument("--noise-cell", type=float, default=20.)
        p.add_argument("--robot-id", default="local")
        p.add_argument("--port", type=int, default=2027 if name == "serve" else 0)
        p.add_argument("--real-limit", type=float, default=1200.)
        p.add_argument("--window-limit", type=float, default=1500.)
        p.add_argument("--virtual-limit", type=float, default=360000.)
        p.add_argument("--scenario", help="Replay a previously exported scenario.json")
        p.add_argument("--out", help="New, empty output directory")
        if name == "batch":
            p.add_argument("--cases", type=int, default=30)
            p.add_argument("--workers", type=int, default=1)
            p.add_argument("--startup-timeout", type=float, default=60.)
            p.add_argument("--cwd", help="Working directory for external strategy")
            p.add_argument("--command", nargs=argparse.REMAINDER, help="Strategy command; must be last option")
    args = parser.parse_args()
    output = Path(args.out).resolve() if args.out else ROOT / "results" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    if output.exists() and any(output.iterdir()):
        parser.error("Output directory is not empty; use a new directory to preserve prior runs")
    output.mkdir(parents=True, exist_ok=True)
    try:
        # Validate configuration before launching workers or strategies.
        build_sim(args, args.seed)
        if not 0 <= args.port <= 65535:
            raise ValueError("Port must be 0..65535")
        if args.mode == "batch" and (not math.isfinite(args.startup_timeout) or args.startup_timeout <= 0
                                    or not args.command and any(s.direction is not None for s in build_sim(args,args.seed).sources.values())):
            raise ValueError("Use positive startup timeout; the included demo supports Q3 only")
        result = batch(args, output) if args.mode == "batch" else serve(args, output)
    except (ValueError, OSError) as e:
        parser.error(str(e))
    print(f"Results: {output}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
