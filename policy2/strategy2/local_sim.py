"""Launch the independent local_simulator. Strategy workers use HTTP only.

This module does not import b_sim or provide simulator truth to runner.run.
Post-run comparison reads exported cases only after their workers have exited.
"""
from dataclasses import asdict
from datetime import datetime
import csv
import hashlib
import json
import math
import os
from pathlib import Path
import statistics
import subprocess
import sys

from .config import Config

CODE_ROOT = Path(__file__).resolve().parents[1]
STRATEGY_ROOT = CODE_ROOT
WORKSPACE = STRATEGY_ROOT.parent


def resolve_live(url=None, robot_id=None, output=None, environ=None):
    """Explicit options > harness environment > manual URL default.

    An incomplete harness environment must never silently connect to port 2026.
    Each worker gets a fresh child directory, not the harness's existing case dir.
    """
    env = os.environ if environ is None else environ
    harness = any(env.get(k) for k in ("SIM_BASE_URL", "SIM_ROBOT_ID", "SIM_RUN_DIR"))
    url = url or env.get("SIM_BASE_URL")
    robot_id = robot_id or env.get("SIM_ROBOT_ID")
    if output is None and env.get("SIM_RUN_DIR"):
        output = str(Path(env["SIM_RUN_DIR"]) / "strategy2")
    if not url and harness:
        raise ValueError("Incomplete simulator environment: supply SIM_BASE_URL or --url")
    if not robot_id or not output:
        raise ValueError("Supply --robot-id and --output, or launch through local_simulator (SIM_* variables)")
    return url or "http://127.0.0.1:2026", robot_id, str(output)


def add_arguments(parser):
    parser.add_argument("--configs", nargs="+", default=[str(CODE_ROOT / "configs" / "quick.json")])
    parser.add_argument("--cases", type=int, default=3)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260912)
    parser.add_argument("--count", type=int, choices=range(10, 17))
    parser.add_argument("--layout", choices=["uniform", "edge", "clustered", "mixed"], default="uniform")
    parser.add_argument("--radius-mode", choices=["random", "minimum", "maximum"], default="random")
    parser.add_argument("--noise", choices=["smooth", "hash", "zero"], default="smooth")
    parser.add_argument("--noise-cell", type=float, default=20.)
    parser.add_argument("--real-limit", type=float, default=1200.)
    parser.add_argument("--window-limit", type=float, default=1500.)
    parser.add_argument("--virtual-limit", type=float, default=360000.)
    parser.add_argument("--startup-timeout", type=float, default=60.)
    parser.add_argument("--robot-id", default="local")
    parser.add_argument("--simulator", default=str(WORKSPACE / "local_simulator"), help="Directory containing run.py")
    parser.add_argument("--output", help="New experiment directory; defaults to policy2/results/local_simulator/timestamp")


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")


def case_fingerprint(folder):
    """Verify paired truth AND simulator settings after the strategy has exited."""
    data = [json.loads((folder / name).read_text(encoding="utf-8-sig"))
            for name in ("scenario.json", "settings.json")]
    return hashlib.sha256(json.dumps(data, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def compare_rows(variants):
    """Failures remain in the denominator; speed deltas use jointly successful pairs."""
    baseline = variants[0] if variants else None
    result = []
    for variant in variants:
        rows = variant["cases"]
        successful = [r for r in rows if r["run_ok"]]
        paired = []
        matching = True
        if baseline:
            matching = (len(rows) == len(baseline["cases"]) and bool(rows))
            baseline_cases = {r["case_index"]: r for r in baseline["cases"]}
            for row in rows:
                other = baseline_cases.get(row["case_index"])
                same = bool(other and row["fingerprint"] == other["fingerprint"])
                matching = matching and same
                if same and row["run_ok"] and other["run_ok"]:
                    paired.append(row["virtual_time_s"] - other["virtual_time_s"])
        result.append(dict(
            variant=variant["variant"], config_digest=variant["config_digest"],
            cases=len(rows), expected_cases=variant["expected_cases"],
            successful_runs=len(successful),
            success_rate=len(successful) / variant["expected_cases"],
            cleared_count=sum(r["cleared_count"] for r in rows),
            target_count=sum(r["target_count"] for r in rows),
            mean_successful_virtual_time_s=statistics.mean(r["virtual_time_s"] for r in successful) if successful else None,
            mean_real_time_s=statistics.mean(r["real_time_s"] for r in rows) if rows else None,
            paired_cases_match=matching,
            jointly_successful_pairs=len(paired),
            mean_paired_delta_s=statistics.mean(paired) if paired else None,
            wins_vs_first=sum(d < -1e-6 for d in paired),
            harness_exit_code=variant["harness_exit_code"],
            diagnostics=variant["diagnostics"],
        ))
    return result


def run_batches(args):
    """One external batch per config; all configs use identical case generation args."""
    simulator = Path(args.simulator).resolve()
    if not (simulator / "run.py").is_file():
        raise ValueError(f"Missing local simulator: {simulator / 'run.py'}")
    if args.cases < 1 or args.workers < 1:
        raise ValueError("cases and workers must be positive")
    for name in ("noise_cell", "real_limit", "window_limit", "virtual_limit", "startup_timeout"):
        value = getattr(args, name)
        if not math.isfinite(value) or value <= 0:
            raise ValueError(f"{name} must be finite and positive")
    configs = [(Path(p).resolve(), Config.load(p)) for p in args.configs]
    output = (Path(args.output).resolve() if args.output else
              STRATEGY_ROOT / "results" / "local_simulator" / datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    # Always fresh: never overwrite previous results or simulator exports.
    output.mkdir(parents=True, exist_ok=False)
    shared = ["--cases", str(args.cases), "--workers", str(args.workers),
              "--seed", str(args.seed), "--layout", args.layout, "--radius-mode", args.radius_mode,
              "--noise", args.noise, "--noise-cell", str(args.noise_cell),
              "--real-limit", str(args.real_limit), "--window-limit", str(args.window_limit),
              "--virtual-limit", str(args.virtual_limit), "--startup-timeout", str(args.startup_timeout),
              "--robot-id", args.robot_id, "--port", "0", "--directional-count", "0"]
    if args.count is not None:
        shared.extend(["--count", str(args.count)])
    manifest = dict(engine="local_simulator HTTP subprocess", python=sys.executable,
                    simulator=str(simulator), shared_arguments=shared, configurations=[])
    variants = []
    base_config = asdict(configs[0][1])
    print(f"Results: {output}", flush=True)
    for index, (original, cfg) in enumerate(configs, 1):
        tag = f"{index:02d}_{original.stem}"
        config_snapshot = output / f"{tag}.config.json"
        write_json(config_snapshot, asdict(cfg))
        differences = {k: {"first": base_config[k], "current": v} for k, v in asdict(cfg).items()
                       if v != base_config[k]}
        folder = output / tag
        command = [sys.executable, str(simulator / "run.py"), "batch", *shared,
                   "--out", str(folder), "--cwd", str(CODE_ROOT),
                   "--command", "{python}", "-m", "strategy2", "live", "--config", str(config_snapshot)]
        manifest["configurations"].append(dict(variant=tag, original=str(original),
                    snapshot=str(config_snapshot), config_digest=cfg.digest(),
                    differences_from_first=differences, command=command))
        write_json(output / "manifest.json", manifest)
        print(f"Variant {index}/{len(configs)}: {tag}", flush=True)
        env = os.environ.copy()
        env.update(PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        process = subprocess.run(command, cwd=CODE_ROOT, env=env, check=False)
        rows = []
        diagnostics = []
        results_file = folder / "results.json"
        if results_file.is_file():
            rows = json.loads(results_file.read_text(encoding="utf-8-sig"))
            for row in rows:
                case_dir = folder / f"case_{row['case_index']:04d}"
                row["fingerprint"] = case_fingerprint(case_dir)
                strategy_summary = case_dir / "strategy2" / "summary.json"
                # Harness truth is the authority. Also reject a missing/invalid certificate.
                if strategy_summary.is_file():
                    strategy = json.loads(strategy_summary.read_text(encoding="utf-8-sig"))
                    row["strategy_reason"] = strategy["reason"]
                    row["certificate_consistent"] = bool(
                        strategy["complete_certificate"] and strategy["reason"] == "complete"
                        and strategy["cleared_count"] == row["cleared_count"])
                    row["clock_delta_s"] = strategy["virtual_total_s"] - row["virtual_time_s"]
                else:
                    row["strategy_reason"] = "missing_summary"
                    row["certificate_consistent"] = False
                    row["clock_delta_s"] = None
                row["harness_run_ok"] = row["run_ok"]
                row["run_ok"] = row["run_ok"] and row["certificate_consistent"]
        else:
            diagnostics.append("Simulator did not export results.json; inspect its terminal error")
        variants.append(dict(variant=tag, config_digest=cfg.digest(), expected_cases=args.cases,
                             cases=rows, harness_exit_code=process.returncode, diagnostics=diagnostics))
        comparison = compare_rows(variants)
        write_json(output / "comparison.json", comparison)
        write_json(output / "case_comparison.json", variants)
        with (output / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(comparison[0]))
            writer.writeheader()
            writer.writerows(comparison)
    all_ok = all(r["success_rate"] == 1 and r["harness_exit_code"] == 0 and r["paired_cases_match"]
                 for r in comparison)
    report = dict(output=str(output), all_runs_ok=all_ok, comparison=comparison)
    write_json(output / "summary.json", report)
    return report
