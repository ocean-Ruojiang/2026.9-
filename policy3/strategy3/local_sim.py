"""Run policy3 against the unmodified HTTP simulator in a separate process.

Scenario generation and truth inspection belong to this harness only.  The policy
process receives an HTTP URL, its robot ID and its output directory, never a seed
or a scenario file.  Truth is exported after that process has terminated.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import csv
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import statistics
import subprocess
import sys
import time
from typing import Any, Sequence


POLICY_ROOT = Path(__file__).resolve().parents[1]
SIMULATOR_ROOT = POLICY_ROOT.parent / "local_simulator"


@dataclass
class CaseSpec:
    name: str
    scenario: dict[str, Any]
    category: str = "random"
    noise: str = "smooth"
    description: str = ""
    non_typical_stress: bool = False

    def metadata(self) -> dict[str, Any]:
        sources = self.scenario["sources"]
        return {
            "name": self.name,
            "category": self.category,
            "seed": self.scenario.get("seed"),
            "target_count": len(sources),
            "directional_count": sum(s["direction"] is not None for s in sources),
            "layout": self.scenario.get("layout"),
            "radius_mode": self.scenario.get("radius_mode"),
            "noise": self.noise,
            "description": self.description,
            "non_typical_stress": self.non_typical_stress,
        }


def simulator_api():
    """Load the sibling simulator, without copying or changing its physics."""
    simulator_path = str(SIMULATOR_ROOT)
    if simulator_path not in sys.path:
        sys.path.insert(0, simulator_path)
    from b_sim.core import Simulator, Source, generate_scenario, validate_scenario
    from b_sim.server import Server

    spec = importlib.util.spec_from_file_location("_policy3_simulator_harness", SIMULATOR_ROOT / "run.py")
    assert spec is not None and spec.loader is not None
    harness = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(harness)
    return Simulator, Source, generate_scenario, validate_scenario, Server, harness


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def source_fingerprint() -> dict[str, Any]:
    """Hash code and coverage assets with stable relative names and raw bytes."""
    package = POLICY_ROOT / "strategy3"
    paths = set(package.rglob("*.py")) | set((package / "assets").rglob("*.json"))
    files = {path.relative_to(POLICY_ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted(paths, key=lambda path: path.relative_to(POLICY_ROOT).as_posix())}
    combined = hashlib.sha256()
    for name, digest in files.items():
        combined.update(name.encode("utf-8"))
        combined.update(b"\0")
        combined.update(bytes.fromhex(digest))
    return {"algorithm": "sha256(relative_path_utf8 + NUL + file_sha256_bytes), sorted paths",
            "sha256": combined.hexdigest(), "file_count": len(files), "files": files}


def _polar(radius: float, angle_deg: float) -> tuple[float, float]:
    angle = math.radians(angle_deg)
    return radius * math.cos(angle), radius * math.sin(angle)


def acceptance_cases(seed: int = 20260913) -> list[CaseSpec]:
    """Fixed 10-random + 5-edge Q4 suite; no case is an omnidirectional-only Q3 run."""
    _, Source, generate, validate, _, _ = simulator_api()
    counts = [10, 11, 12, 13, 14, 15, 16, 12, 14, 16]
    directional = [3, 4, 5, 6, 7, 8, 9, 4, 7, 10]
    layouts = ["uniform", "mixed", "clustered", "uniform", "mixed", "clustered", "uniform", "mixed", "uniform", "mixed"]
    cases: list[CaseSpec] = []
    for i, (count, dcount, layout) in enumerate(zip(counts, directional, layouts)):
        mode = "minimum" if i == 8 else "random"
        scenario = generate(seed + 997 * i, count, layout, mode, dcount)
        cases.append(CaseSpec(
            f"random_{i + 1:02d}_{layout}", scenario, noise="hash" if i == 9 else "smooth",
            description=f"Fixed-seed {layout} random positions; {dcount}/{count} directional sources; radii {mode}."))

    def edge_case(name: str, index: int, count: int, dcount: int, kind: str, noise: str = "smooth") -> CaseSpec:
        case_seed = seed + 100_000 + 997 * index
        rng = random.Random(case_seed)
        channels = rng.sample(range(1, 21), count)
        sources = []
        for j, channel in enumerate(channels):
            angle = (11.3 + j * 360 / max(dcount, 1)) % 360
            is_directional = j < dcount
            if kind in {"outward", "inward", "tangent"} and is_directional:
                radius = [1800., 1799.5, 1780.][j % 3]
                x, y = _polar(radius, angle)
                if kind == "outward":
                    direction = angle
                elif kind == "inward":
                    direction = (angle + 180) % 360
                else:
                    direction = (angle + (90 if j % 2 == 0 else -90) + (0.02 if j % 3 == 0 else -0.02)) % 360
            elif kind == "clustered":
                # Alternate an interior cluster and a near-edge cluster, with
                # distinct source locations and deliberately varied beam directions.
                cx, cy = ((250., -120.) if j % 2 == 0 else (1690., 150.))
                while True:
                    x, y = rng.gauss(cx, 32), rng.gauss(cy, 32)
                    if math.hypot(x, y) <= 1800:
                        break
                direction = (j * 137.507764 + 21) % 360 if is_directional else None
            elif kind == "all_directional_hash":
                radius = [0., 35., 500., 900., 1400., 1799., 1800.][j % 7]
                angle = (j * 137.507764 + 7.1) % 360
                x, y = _polar(radius, angle)
                direction = angle if radius >= 1799 else (angle + 73 + j * 11) % 360
            else:
                x, y = _polar(1100 * math.sqrt(rng.random()), rng.uniform(0, 360))
                direction = None
            sources.append(asdict(Source(channel, x, y, 1000., direction)))
        scenario = {"schema_version": 1, "seed": case_seed, "layout": name,
                    "radius_mode": "minimum", "sources": sources}
        descriptions = {
            "outward": "Minimum radius, near-boundary directional sources face outward; internal observation cannot be assumed sufficient.",
            "inward": "Minimum radius, near-boundary directional sources face inward; outer巡检 cannot replace required internal coverage.",
            "tangent": "Minimum radius, near-boundary tangential beams with +/-0.02 degree perturbations; tests grazing visibility.",
            "clustered": "Minimum radius, two dense clusters, one close to the boundary; source channels remain unique.",
            "all_directional_hash": "Non-typical stress case: all 16 sources directional, minimum radii, center/boundary positions and fixed spatial hash bearing errors.",
        }
        return CaseSpec(name, scenario, "edge", noise, descriptions[kind], kind == "all_directional_hash")

    cases.extend([
        edge_case("edge_outward_minimum", 0, 12, 8, "outward"),
        edge_case("edge_inward_minimum", 1, 12, 8, "inward"),
        edge_case("edge_tangent_minimum", 2, 14, 10, "tangent"),
        edge_case("edge_dense_clusters", 3, 16, 8, "clustered"),
        edge_case("stress_all_directional_hash", 4, 16, 16, "all_directional_hash", "hash"),
    ])
    for case in cases:
        validate(case.scenario)
    return cases


def random_cases(cases: int, seed: int, directional_count: int, count: int | None,
                 layout: str, radius_mode: str, noise: str) -> list[CaseSpec]:
    if cases < 1:
        raise ValueError("cases must be positive")
    _, _, generate, _, _, _ = simulator_api()
    result = []
    for i in range(cases):
        # When count is omitted, select a legal count that accommodates all beams.
        n = count if count is not None else random.Random(seed + i).randint(max(10, directional_count), 16)
        scenario = generate(seed + i, n, layout, radius_mode, directional_count)
        result.append(CaseSpec(f"random_{i + 1:04d}", scenario, noise=noise))
    return result


def _read_strategy_summary(folder: Path) -> tuple[dict[str, Any], str | None]:
    path = folder / "strategy3" / "summary.json"
    if not path.exists():
        return {}, "strategy summary was not written"
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(result, dict):
            raise ValueError("summary must be an object")
        return result, None
    except (OSError, ValueError) as exc:
        return {}, f"{type(exc).__name__}: {exc}"


def run_case(case: CaseSpec, index: int, output: Path, config: Path, real_limit: float,
             virtual_limit: float = 360000., startup_timeout: float = 60.) -> dict[str, Any]:
    Simulator, _, _, _, Server, simulator_harness = simulator_api()
    folder = output / f"case_{index + 1:04d}"
    folder.mkdir()
    strategy_output = folder / "strategy3"
    strategy_output.mkdir()
    sim = Simulator(case.scenario, robot_id="local", noise=case.noise,
                    real_limit_s=real_limit, window_limit_s=real_limit + startup_timeout + 30,
                    virtual_limit_s=virtual_limit)
    server = None
    process = None
    extra: dict[str, Any] = {"case_index": index + 1, **case.metadata(),
                             "process_exit_code": None, "harness_error": None}
    launched = time.perf_counter()
    try:
        server = Server(sim, 0)
        server.start()
        command = [sys.executable, "-m", "strategy3", "run", "--config", str(config.resolve()),
                   "--output", str(strategy_output.resolve())]
        # Do not propagate stray simulator test/debug variables from the caller.
        env = {key: value for key, value in os.environ.items() if not key.startswith("SIM_")}
        env.update(SIM_BASE_URL=server.url, SIM_ROBOT_ID="local", SIM_RUN_DIR=str(folder.resolve()),
                   PYTHONIOENCODING="utf-8", PYTHONUNBUFFERED="1")
        env["PYTHONPATH"] = str(POLICY_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        with (folder / "strategy.stdout.log").open("w", encoding="utf-8") as stdout, \
                (folder / "strategy.stderr.log").open("w", encoding="utf-8") as stderr:
            process = subprocess.Popen(command, cwd=str(POLICY_ROOT), env=env,
                                       stdout=stdout, stderr=stderr, **options)
            launched = time.perf_counter()
            while process.poll() is None:
                if sim.expired():
                    break
                if sim.started_at is None and time.perf_counter() - launched >= startup_timeout:
                    sim.finish("startup_timeout")
                    break
                time.sleep(.02)
            if process.poll() is None:
                try:
                    process.wait(timeout=5 if sim.end_reason == "user_exit" else .1)
                except subprocess.TimeoutExpired:
                    simulator_harness.stop_child(process)
            extra["process_exit_code"] = process.returncode
        if sim.end_reason is None:
            sim.finish("strategy_returned_without_exit" if sim.started_at is not None else "strategy_never_entered")
    except Exception as exc:
        extra["harness_error"] = f"{type(exc).__name__}: {exc}"
        sim.finish("harness_error")
    finally:
        if process is not None:
            simulator_harness.stop_child(process)
        extra["process_wall_time_s"] = time.perf_counter() - launched
        if server is not None:
            server.shutdown()
            server.server_close()

    # Read reported computations only after the independent policy process stops.
    strategy, summary_error = _read_strategy_summary(folder)
    extra.update(
        strategy_summary_error=summary_error,
        strategy_complete=strategy.get("complete"),
        strategy_reason=strategy.get("reason"),
        strategy_cpu_time_s=strategy.get("strategy_cpu_time_s"),
        planning_time_s=strategy.get("planning_time_s"),
        strategy_real_runtime_s=strategy.get("real_runtime_s"),
        strategy_discovered_count=strategy.get("discovered_count"),
        config_digest=strategy.get("config_digest"),
        discovered_count=len(sim.detected),
        run_ok=(sim.end_reason == "user_exit" and extra["process_exit_code"] == 0
                and len(sim.cleared) == len(sim.sources)),
    )
    # save_run writes scenario.json here, after the child has terminated.
    result = simulator_harness.save_run(folder, sim, extra)
    _write_json(folder / "case_metadata.json", case.metadata())
    return result


def _mean(rows: Sequence[dict[str, Any]], key: str) -> float | None:
    values = [row[key] for row in rows if isinstance(row.get(key), (float, int))]
    return statistics.mean(values) if values else None


def summarize(rows: Sequence[dict[str, Any]]) -> dict[str, Any]:
    successes = [row for row in rows if row["run_ok"]]
    total = sum(row["target_count"] for row in rows)
    cleared = sum(row["cleared_count"] for row in rows)
    return {
        "cases": len(rows), "successful_runs": len(successes),
        "success_rate": len(successes) / len(rows) if rows else None,
        "total_sources": total, "total_cleared": cleared,
        "total_discovered": sum(row["discovered_count"] for row in rows),
        "overall_clearance_ratio": cleared / total if total else None,
        "mean_virtual_time_s": _mean(rows, "virtual_time_s"),
        "mean_successful_virtual_time_s": _mean(successes, "virtual_time_s"),
        "mean_real_time_s": _mean(rows, "real_time_s"),
        "mean_process_wall_time_s": _mean(rows, "process_wall_time_s"),
        "mean_strategy_cpu_time_s": _mean(rows, "strategy_cpu_time_s"),
        "cpu_time_available_cases": sum(row.get("strategy_cpu_time_s") is not None for row in rows),
        "mean_planning_time_s": _mean(rows, "planning_time_s"),
        "mean_move_distance_m": _mean(rows, "move_distance_m"),
        "mean_measurements": _mean(rows, "measure"),
        "failed_case_indices": [row["case_index"] for row in rows if not row["run_ok"]],
        "workers": 1,
        "note": "All cases, including failures and the non-typical stress case, remain in overall statistics. CPU time is the strategy process time; simulator real time includes HTTP and planning waits.",
    }


def _write_results(output: Path, rows: Sequence[dict[str, Any]]) -> None:
    _write_json(output / "results.json", list(rows))
    _write_json(output / "summary.json", summarize(rows))
    fields = ["case_index", "name", "category", "seed", "target_count", "directional_count",
              "discovered_count", "cleared_count", "run_ok", "virtual_time_s", "real_time_s",
              "strategy_cpu_time_s", "planning_time_s", "process_wall_time_s", "move_distance_m",
              "measure", "no_signal", "clear_failure", "end_reason", "strategy_reason", "process_exit_code",
              "harness_error", "strategy_summary_error", "non_typical_stress"]
    with (output / "results.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    lines = ["# Policy3 本地模拟器实验结果", "", "单进程顺序执行；失败局不剔除。虚拟时间来自原模拟器，计算时间来自策略进程。", "",
             "| 局 | 场景 | 清除/总数 | 发现 | 虚拟时间/s | 现实时间/s | 策略CPU/s | 规划时间/s | 成功 |",
             "|---:|---|---:|---:|---:|---:|---:|---:|:---:|"]
    def number(value):
        return "缺失" if value is None else f"{value:.3f}"
    for row in rows:
        lines.append(f"| {row['case_index']} | {row['name']} | {row['cleared_count']}/{row['target_count']} | {row['discovered_count']} | {number(row.get('virtual_time_s'))} | {number(row.get('real_time_s'))} | {number(row.get('strategy_cpu_time_s'))} | {number(row.get('planning_time_s'))} | {'是' if row['run_ok'] else '否'} |")
    lines.extend(["", "现实时间是模拟器会话持续时间；策略 CPU 时间不包含等待 HTTP 和模拟器计算。规划时间按策略记录口径单独报告。", "",
                  "每局目录保留原始 actions.jsonl、scenario.json、settings.json、summary.json、标准输出/错误日志及 strategy3 下的策略日志。", ""])
    (output / "实验结果.md").write_text("\n".join(lines), encoding="utf-8")


def run_suite(cases: Sequence[CaseSpec], config: Path, output: Path, real_limit: float = 600.,
              virtual_limit: float = 360000.) -> int:
    if not cases or not math.isfinite(real_limit) or real_limit <= 0:
        raise ValueError("Require at least one case and a finite positive real time limit")
    config = config.resolve()
    parsed_config = json.loads(config.read_text(encoding="utf-8-sig"))
    _, _, _, validate, _, _ = simulator_api()
    for case in cases:
        validate(case.scenario)
    output = output.resolve()
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be new or empty; previous runs are never overwritten")
    output.mkdir(parents=True, exist_ok=True)
    frozen_config = output / "config.used.json"
    _write_json(frozen_config, parsed_config)
    config_hash = hashlib.sha256(frozen_config.read_bytes()).hexdigest()
    source_start = source_fingerprint()
    source_changed = False
    rows = []
    started = time.perf_counter()
    try:
        for index, case in enumerate(cases):
            row = run_case(case, index, output, frozen_config, real_limit, virtual_limit)
            rows.append(row)
            _write_results(output, rows)
            print(f"[{index + 1}/{len(cases)}] {case.name}: cleared={row['cleared_count']}/{row['target_count']} "
                  f"discovered={row['discovered_count']} virtual={row['virtual_time_s']:.2f}s "
                  f"real={row['real_time_s']:.2f}s CPU={row.get('strategy_cpu_time_s')} "
                  f"end={row['end_reason']} ok={row['run_ok']}", flush=True)
    finally:
        # Metadata with seeds is not materialized until all child processes stop.
        source_end = source_fingerprint()
        source_changed = source_start["sha256"] != source_end["sha256"]
        changed_files = sorted(name for name in set(source_start["files"]) | set(source_end["files"])
                               if source_start["files"].get(name) != source_end["files"].get(name))
        final_summary = summarize(rows)
        final_summary.update(source_changed=source_changed,
                             acceptance_eligible=not source_changed and len(rows) == len(cases),
                             source_sha256_start=source_start["sha256"], source_sha256_end=source_end["sha256"],
                             source_changed_files=changed_files)
        if source_changed:
            final_summary["note"] += " SOURCE CHANGED DURING THE BATCH: retained for diagnosis, ineligible as a frozen-version acceptance run."
            with (output / "实验结果.md").open("a", encoding="utf-8") as handle:
                handle.write("\n**本批次运行期间策略源码或覆盖资产发生变化，结果保留用于诊断，不作为最终固定版本验收。**\n")
            print("WARNING: strategy source/assets changed during this batch; results are retained but are not final acceptance evidence.", flush=True)
        _write_json(output / "summary.json", final_summary)
        _write_json(output / "manifest.json", {
            "suite": "policy3_q4", "created_at": datetime.now().astimezone().isoformat(),
            "python": sys.executable, "python_version": sys.version,
            "simulator_root": str(SIMULATOR_ROOT), "config_sha256": config_hash,
            "real_limit_s": real_limit, "virtual_limit_s": virtual_limit,
            "workers": 1, "batch_wall_time_s": time.perf_counter() - started,
            "source_fingerprint_start": source_start, "source_fingerprint_end": source_end,
            "source_changed": source_changed, "source_changed_files": changed_files,
            "planned_cases": len(cases), "completed_cases": len(rows),
            "cases": [dict(case_index=i + 1, **case.metadata()) for i, case in enumerate(cases)],
        })
    print(f"Results: {output}", flush=True)
    return 0 if all(row["run_ok"] for row in rows) and len(rows) == len(cases) and not source_changed else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run policy3 against the sibling local simulator (one worker).")
    parser.add_argument("--cases", type=int, default=1)
    parser.add_argument("--seed", type=int, default=20260913)
    parser.add_argument("--count", type=int, choices=range(10, 17))
    parser.add_argument("--directional-count", type=int, default=6)
    parser.add_argument("--layout", choices=["uniform", "mixed", "clustered", "edge"], default="mixed")
    parser.add_argument("--radius-mode", choices=["random", "minimum", "maximum"], default="random")
    parser.add_argument("--noise", choices=["smooth", "hash", "zero"], default="smooth")
    parser.add_argument("--config", type=Path, default=POLICY_ROOT / "configs" / "default.json")
    parser.add_argument("--output", "--out", type=Path)
    parser.add_argument("--real-limit", type=float, default=600.)
    parser.add_argument("--virtual-limit", type=float, default=360000.)
    args = parser.parse_args(argv)
    if not 0 <= args.directional_count <= (args.count if args.count is not None else 16):
        parser.error("directional-count must be between zero and the source count")
    output = args.output or POLICY_ROOT / "results" / ("local_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    try:
        cases = random_cases(args.cases, args.seed, args.directional_count, args.count,
                             args.layout, args.radius_mode, args.noise)
        return run_suite(cases, args.config, output, args.real_limit, args.virtual_limit)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
