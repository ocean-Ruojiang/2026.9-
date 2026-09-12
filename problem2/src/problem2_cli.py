"""Single-case CLI with explicit inputs and portable JSON output."""
from __future__ import annotations
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time
import problem2_optimizer as p2

def solve(config: dict) -> dict:
    mode = config.get("mode", "both")
    if mode not in {"max", "mean", "both"}:
        raise ValueError("mode must be max, mean or both")
    point = config.get("first_point", [0.0, 0.0])
    bearing = config.get("first_bearing_deg", 0.0)
    params = dict(config.get("params", {}))
    started = time.perf_counter()
    try:
        if mode == "both":
            result = p2.finite_element_search_dual(point, bearing, **params)
        else:
            result = p2.finite_element_search(point, bearing, metric=mode, **params)
    except p2.EmptyCandidateRegionError as exc:
        return {"status": "empty_candidate_region", "input": config,
                "elapsed_seconds": time.perf_counter() - started,
                "possible_region": exc.possible_region.to_dict()}
    return {"status": "completed", "input": config,
            "elapsed_seconds": time.perf_counter() - started,
            "summary": result.summary(),
            "possible_region": result.possible_region.to_dict(),
            "candidate_region": result.candidate_region.to_dict(),
            "coarse": [asdict(item) for item in result.coarse],
            "refined": [asdict(item) for item in result.refined]}

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Problem 2 second-point optimizer")
    parser.add_argument("--config", type=Path, help="JSON case configuration")
    parser.add_argument("--mode", choices=["max", "mean", "both"], help="Override config mode")
    parser.add_argument("--output", type=Path, default=Path("results/problem2.json"))
    args = parser.parse_args(argv)
    try:
        config = json.loads(args.config.read_text(encoding="utf-8-sig")) if args.config else {}
        if args.mode:
            config["mode"] = args.mode
        payload = solve(config)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    except (OSError, ValueError, TypeError) as exc:
        parser.exit(2, f"Error: {exc}\n")
    print(f"status={payload['status']}; elapsed={payload['elapsed_seconds']:.3f}s")
    print(json.dumps(payload.get("summary", {}), ensure_ascii=False))
    print(f"output={args.output.resolve()}")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
