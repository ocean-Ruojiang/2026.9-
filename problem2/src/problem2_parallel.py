from __future__ import annotations

"""Reusable process-level parallelism for Problem 2 without changing geometry.

Two granularities are provided:

1. Case-level parallelism: run many independent ``(S1, bearing, params)``
   searches with ``ProcessPoolExecutor``.
2. Grid-level parallelism: split one candidate grid into contiguous chunks and
   evaluate points in multiple processes.

The default behaviour remains serial. Multiprocessing is enabled only when a
``ParallelConfig`` with worker counts greater than one is passed.
"""

import argparse
import csv
import json
import math
import multiprocessing
import os
import random
import traceback
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Any, Iterable, Sequence

import problem2_optimizer as p2

_SPAWN = multiprocessing.get_context("spawn")


class ParallelConfigError(ValueError):
    """Invalid parallel configuration."""


class ParallelTaskError(RuntimeError):
    """A case task failed; the structured error is attached."""


@dataclass(frozen=True, slots=True)
class ParallelConfig:
    """Concurrency configuration for case- and grid-level parallelism.

    ``case_workers`` controls independent Problem-2 cases.
    ``grid_workers`` controls grid points inside one case.

    Both are rejected when greater than one because nested multi-processing can
    oversubscribe CPUs; choose one granularity per invocation.
    """

    case_workers: int = 1
    grid_workers: int = 1

    def __post_init__(self) -> None:
        if isinstance(self.case_workers, bool) or not isinstance(self.case_workers, int):
            raise ParallelConfigError("case_workers must be an integer")
        if isinstance(self.grid_workers, bool) or not isinstance(self.grid_workers, int):
            raise ParallelConfigError("grid_workers must be an integer")
        if self.case_workers < 1 or self.grid_workers < 1:
            raise ParallelConfigError("worker counts must be at least 1")
        if self.case_workers > 1 and self.grid_workers > 1:
            raise ParallelConfigError(
                "nested parallelism is disabled: set either case_workers=1 "
                "or grid_workers=1 to avoid process oversubscription"
            )

    def resolved_grid_workers(self) -> int:
        """Grid workers to use inside a case-level worker.

        Outer case parallelism forces inner grid parallelism off.
        """

        return 1 if self.case_workers > 1 else self.grid_workers

    def effective_case_workers(self) -> int:
        return self.case_workers


@dataclass(frozen=True, slots=True)
class CaseTask:
    """One independent Problem-2 case."""

    first_point: tuple[float, float]
    first_bearing_deg: float
    params: dict[str, object] | None = None
    mode: str | None = None
    label: str = ""


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    """Deterministic result slot for one case.

    ``ok=False`` means the task failed without crashing the batch.
    """

    index: int
    ok: bool
    result: object | None = None
    error_type: str | None = None
    error_message: str | None = None
    error_traceback: str | None = None
    label: str = ""


@dataclass(frozen=True, slots=True)
class GridChunk:
    """A contiguous slice of an ordered grid-point list."""

    offset: int
    points: tuple[tuple[float, float], ...]


def _validate_config(config: ParallelConfig) -> ParallelConfig:
    if config is None:
        config = ParallelConfig()
    if not isinstance(config, ParallelConfig):
        raise ParallelConfigError("config must be a ParallelConfig")
    return config


def _positive_workers(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ParallelConfigError(f"{name} must be an integer")
    if value < 1:
        raise ParallelConfigError(f"{name} must be at least 1")
    return value


def _check_grid_workers(grid_workers: int) -> int:
    grid_workers = _positive_workers(grid_workers, "grid_workers")
    cpu_count = os.cpu_count() or 1
    if grid_workers > cpu_count * 4:
        # Warn, but do not block legitimate high-throughput jobs.
        import warnings

        warnings.warn(
            f"grid_workers={grid_workers} greatly exceeds os.cpu_count()={cpu_count}",
            RuntimeWarning,
            stacklevel=2,
        )
    return grid_workers


def _check_case_workers(case_workers: int) -> int:
    case_workers = _positive_workers(case_workers, "case_workers")
    cpu_count = os.cpu_count() or 1
    if case_workers > cpu_count * 4:
        import warnings

        warnings.warn(
            f"case_workers={case_workers} greatly exceeds os.cpu_count()={cpu_count}",
            RuntimeWarning,
            stacklevel=2,
        )
    return case_workers


def _merge_params(base_params: dict[str, object] | None, task_params: dict[str, object] | None) -> dict[str, object]:
    merged = dict(base_params or {})
    if task_params:
        merged.update(task_params)
    return merged


def _resolve_mode(task: CaseTask, default_mode: str) -> str:
    mode = task.mode or default_mode
    if mode not in {"max", "mean", "both"}:
        raise p2.Problem2GeometryError("mode must be 'max', 'mean' or 'both'")
    return mode


def _execute_case_task(task: CaseTask, base_params: dict[str, object] | None, default_mode: str):
    """Execute one task in the current process.  This function is picklable."""

    import problem2_optimizer as optim

    mode = _resolve_mode(task, default_mode)
    params = _merge_params(base_params, task.params)
    first = (float(task.first_point[0]), float(task.first_point[1]))
    bearing = float(task.first_bearing_deg)
    if mode == "both":
        return optim.finite_element_search_dual(first, bearing, **params)
    return optim.finite_element_search(first, bearing, metric=mode, **params)


def _case_worker(task: CaseTask, base_params: dict[str, object] | None, default_mode: str):
    """Multiprocessing entry for a single case."""

    return _execute_case_task(task, base_params, default_mode)


def _chunks(values: Sequence[Any], count: int) -> list[list[Any]]:
    if count <= 1:
        return [list(values)]
    count = min(count, len(values))
    if count <= 1:
        return [list(values)]
    base = len(values) // count
    extra = len(values) % count
    result: list[list[Any]] = []
    cursor = 0
    for index in range(count):
        size = base + (1 if index < extra else 0)
        result.append(list(values[cursor : cursor + size]))
        cursor += size
    return result


def _collect_candidate_points(
    candidate_region: p2.CurvedRegion,
    width: float,
    bounds: tuple[float, float, float, float] | None = None,
) -> list[tuple[float, float]]:
    """Return candidate points in exactly the same order as the serial grid."""

    if bounds is None:
        bounds = candidate_region.bounds()
    xmin, xmax, ymin, ymax = bounds
    tol = width * 1e-9
    points: list[tuple[float, float]] = []
    for x, y in p2._grid_points((xmin, xmax, ymin, ymax), width):
        point = (float(x), float(y))
        if candidate_region.contains(p2._point(point), tol=tol):
            points.append(point)
    return points


def _evaluate_single_chunk(
    candidate_region: p2.CurvedRegion,
    possible_region: p2.CurvedRegion,
    points: Sequence[tuple[float, float]],
    bearing_error_deg: float,
    angle_step_deg: float,
    metric: str,
) -> list[p2.GridEvaluation]:
    import problem2_optimizer as optim

    results: list[p2.GridEvaluation] = []
    for x, y in points:
        point = optim._point((x, y))
        value = optim.angle_metric_at(
            point,
            possible_region,
            bearing_error_deg=bearing_error_deg,
            angle_step_deg=angle_step_deg,
            metric=metric,
        )
        results.append(p2.GridEvaluation(float(x), float(y), float(value), metric=metric))
    return results


def _evaluate_dual_chunk(
    candidate_region: p2.CurvedRegion,
    possible_region: p2.CurvedRegion,
    points: Sequence[tuple[float, float]],
    bearing_error_deg: float,
    angle_step_deg: float,
) -> list[p2.DualGridEvaluation]:
    import problem2_optimizer as optim

    results: list[p2.DualGridEvaluation] = []
    for x, y in points:
        point = optim._point((x, y))
        values = optim.diameter_values_at(
            point,
            possible_region,
            bearing_error_deg=bearing_error_deg,
            angle_step_deg=angle_step_deg,
        )
        if values:
            max_value = max(values)
            mean_value = math.fsum(values) / len(values)
        else:
            max_value = math.inf
            mean_value = math.inf
        results.append(
            p2.DualGridEvaluation(
                float(x),
                float(y),
                float(max_value),
                float(mean_value),
            )
        )
    return results


def _evaluate_grid_parallel(
    candidate_region: p2.CurvedRegion,
    possible_region: p2.CurvedRegion,
    points: Sequence[tuple[float, float]],
    *,
    width: float,
    bearing_error_deg: float,
    angle_step_deg: float,
    metric: str,
    grid_workers: int,
):
    """Evaluate ordered grid points, splitting into contiguous chunks."""

    grid_workers = _check_grid_workers(grid_workers)
    if not points:
        return tuple()
    if grid_workers <= 1:
        if metric == "both":
            return tuple(_evaluate_dual_chunk(candidate_region, possible_region, points, bearing_error_deg, angle_step_deg))
        return tuple(_evaluate_single_chunk(candidate_region, possible_region, points, bearing_error_deg, angle_step_deg, metric))

    effective = min(grid_workers, len(points))
    chunks = _chunks(points, effective)
    with ProcessPoolExecutor(max_workers=effective, mp_context=_SPAWN) as executor:
        if metric == "both":
            futures = [
                executor.submit(
                    _evaluate_dual_chunk,
                    candidate_region,
                    possible_region,
                    chunk,
                    bearing_error_deg,
                    angle_step_deg,
                )
                for chunk in chunks
            ]
        else:
            futures = [
                executor.submit(
                    _evaluate_single_chunk,
                    candidate_region,
                    possible_region,
                    chunk,
                    bearing_error_deg,
                    angle_step_deg,
                    metric,
                )
                for chunk in chunks
            ]
        ordered = [future.result() for future in futures]
    return tuple(chain.from_iterable(ordered))


def _clip_bounds(
    candidate: p2.CurvedRegion,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
) -> tuple[float, float, float, float]:
    bxmin, bxmax, bymin, bymax = candidate.bounds()
    return (
        max(bxmin, xmin),
        min(bxmax, xmax),
        max(bymin, ymin),
        min(bymax, ymax),
    )


def finite_element_search_parallel(
    first_point: object,
    first_bearing_deg: float,
    *,
    mode: str = "max",
    grid_workers: int = 1,
    target_radius: float = 1800.0,
    maximum_detection_radius: float = 1500.0,
    guaranteed_radius: float = 1000.0,
    bearing_error_deg: float = 1.0,
    angle_step_deg: float = 5.0,
    coarse_width: float = 100.0,
    refine_width: float = 20.0,
    refine_span: float | None = None,
    circle_samples: int = 360,
    maximum_constraints: int = 96,
    coverage_spacing: float = 50.0,
    safety_margin: float | None = None,
):
    """Same search as ``finite_element_search`` / ``_dual``, but grid points
    can be evaluated by multiple processes.

    ``grid_workers == 1`` keeps the serial path.  ``mode="both"`` invokes the
    dual-metric search.
    """

    if mode not in {"max", "mean", "both"}:
        raise p2.Problem2GeometryError("mode must be 'max', 'mean' or 'both'")
    workers = _check_grid_workers(grid_workers)

    possible = p2.possible_target_region(
        first_point,
        first_bearing_deg,
        target_radius=target_radius,
        maximum_detection_radius=maximum_detection_radius,
        bearing_error_deg=bearing_error_deg,
        circle_samples=circle_samples,
    )
    if possible.empty:
        raise p2.Problem2GeometryError("possible target region is empty")
    candidate = p2.guaranteed_detection_region(
        possible,
        first_point=first_point,
        guaranteed_radius=guaranteed_radius,
        coverage_spacing=coverage_spacing,
        safety_margin=safety_margin,
        circle_samples=max(72, circle_samples // 4),
        maximum_constraints=maximum_constraints,
    )
    if candidate.empty:
        raise p2.EmptyCandidateRegionError(possible)

    coarse_points = _collect_candidate_points(candidate, coarse_width)
    coarse = _evaluate_grid_parallel(
        candidate,
        possible,
        coarse_points,
        width=coarse_width,
        bearing_error_deg=bearing_error_deg,
        angle_step_deg=angle_step_deg,
        metric=mode,
        grid_workers=workers,
    )
    if not coarse:
        raise p2.Problem2GeometryError("coarse finite-element search found no valid points")

    if mode == "both":
        best_max_coarse = min(coarse, key=lambda item: item.max_value)
        best_mean_coarse = min(coarse, key=lambda item: item.mean_value)
        if refine_span is None:
            refined_span = max(2.5 * coarse_width, 2.0 * refine_width)
        else:
            refined_span = p2._finite(refine_span, "refine_span")
        xmin, xmax, ymin, ymax = _clip_bounds(
            candidate,
            min(best_max_coarse.x, best_mean_coarse.x) - refined_span,
            max(best_max_coarse.x, best_mean_coarse.x) + refined_span,
            min(best_max_coarse.y, best_mean_coarse.y) - refined_span,
            max(best_max_coarse.y, best_mean_coarse.y) + refined_span,
        )
        refined_points = _collect_candidate_points(candidate, refine_width, (xmin, xmax, ymin, ymax))
        refined = _evaluate_grid_parallel(
            candidate,
            possible,
            refined_points,
            width=refine_width,
            bearing_error_deg=bearing_error_deg,
            angle_step_deg=angle_step_deg,
            metric="both",
            grid_workers=workers,
        )
        if not refined:
            refined = (best_max_coarse, best_mean_coarse)
        best_max = min(refined, key=lambda item: item.max_value)
        best_mean = min(refined, key=lambda item: item.mean_value)
        return p2.DualFEMSearchResult(
            possible,
            candidate,
            tuple(coarse),
            tuple(refined),
            best_max_coarse,
            best_mean_coarse,
            best_max,
            best_mean,
            float(coarse_width),
            float(refine_width),
            float(angle_step_deg),
        )

    best_coarse = min(coarse, key=lambda item: item.value)
    if refine_span is None:
        refined_span = max(2.5 * coarse_width, 2.0 * refine_width)
    else:
        refined_span = p2._finite(refine_span, "refine_span")
    xmin, xmax, ymin, ymax = _clip_bounds(
        candidate,
        best_coarse.x - refined_span,
        best_coarse.x + refined_span,
        best_coarse.y - refined_span,
        best_coarse.y + refined_span,
    )
    refined_points = _collect_candidate_points(candidate, refine_width, (xmin, xmax, ymin, ymax))
    refined = _evaluate_grid_parallel(
        candidate,
        possible,
        refined_points,
        width=refine_width,
        bearing_error_deg=bearing_error_deg,
        angle_step_deg=angle_step_deg,
        metric=mode,
        grid_workers=workers,
    )
    if not refined:
        refined = (best_coarse,)
    best = min(refined, key=lambda item: item.value)
    return p2.FEMSearchResult(
        possible,
        candidate,
        tuple(coarse),
        tuple(refined),
        best_coarse,
        best,
        float(coarse_width),
        float(refine_width),
        float(angle_step_deg),
        mode,
    )


def run_cases_parallel(
    tasks: Sequence[CaseTask],
    config: ParallelConfig | None = None,
    *,
    mode: str = "both",
    base_params: dict[str, object] | None = None,
) -> tuple[CaseOutcome, ...]:
    """Run independent cases in input order.

    Each case is isolated: failures become ``CaseOutcome.ok=False`` entries.
    """

    config = _validate_config(config)
    case_workers = _check_case_workers(config.case_workers)
    tasks = tuple(tasks)
    if not tasks:
        return tuple()

    if case_workers <= 1:
        outcomes: list[CaseOutcome] = []
        for index, task in enumerate(tasks):
            try:
                result = _execute_case_task(task, base_params, mode)
                outcomes.append(CaseOutcome(index, True, result, label=tasks[index].label))
            except Exception as exc:
                outcomes.append(
                    CaseOutcome(
                        index,
                        False,
                        None,
                        type(exc).__name__,
                        str(exc),
                        traceback.format_exc(),
                        tasks[index].label,
                    )
                )
        return tuple(outcomes)

    with ProcessPoolExecutor(max_workers=case_workers, mp_context=_SPAWN) as executor:
        futures = [executor.submit(_case_worker, task, base_params, mode) for task in tasks]
        outcomes = []
        for index, future in enumerate(futures):
            try:
                result = future.result()
                outcomes.append(CaseOutcome(index, True, result, label=tasks[index].label))
            except Exception as exc:
                outcomes.append(
                    CaseOutcome(
                        index,
                        False,
                        None,
                        type(exc).__name__,
                        str(exc),
                        traceback.format_exc(),
                        tasks[index].label,
                    )
                )
        return tuple(outcomes)


def run_case_parallel(
    first_point: object,
    first_bearing_deg: float,
    *,
    mode: str = "both",
    params: dict[str, object] | None = None,
    config: ParallelConfig | None = None,
):
    """Run one case through the same parallel API and return its result.

    Raises ``ParallelTaskError`` on failure.
    """

    config = _validate_config(config)
    task = CaseTask(
        (float(p2._point(first_point)[0]), float(p2._point(first_point)[1])),
        float(first_bearing_deg),
        params=params,
        mode=mode,
        label="single",
    )
    outcome = run_cases_parallel([task], config, mode=mode, base_params=None)[0]
    if not outcome.ok:
        raise ParallelTaskError(
            f"{outcome.error_type}: {outcome.error_message}"
        ) from None
    return outcome.result


def generate_case_tasks(
    count: int,
    seed: int = 20260911,
    *,
    target_radius: float = 1800.0,
    fraction: float = 0.92,
    params: dict[str, object] | None = None,
    mode: str | None = None,
) -> tuple[CaseTask, ...]:
    """Generate deterministic random ``(S1, bearing)`` cases."""

    count = int(count)
    if count < 0:
        raise ParallelConfigError("count must be non-negative")
    rng = random.Random(seed)
    tasks: list[CaseTask] = []
    for index in range(count):
        angle = rng.uniform(0.0, 2.0 * math.pi)
        radius = math.sqrt(rng.random()) * float(target_radius) * float(fraction)
        s1 = (radius * math.cos(angle), radius * math.sin(angle))
        bearing = rng.uniform(0.0, 360.0)
        tasks.append(
            CaseTask(
                s1,
                bearing,
                params=params,
                mode=mode,
                label=f"case-{index + 1}",
            )
        )
    return tuple(tasks)


def _outcome_to_flat(outcome: CaseOutcome) -> dict[str, object]:
    label = outcome.label if isinstance(outcome, CaseOutcome) else ""
    row: dict[str, object] = {
        "index": outcome.index,
        "ok": outcome.ok,
        "label": getattr(outcome, "label", label),
    }
    if not outcome.ok:
        row.update(
            {
                "error_type": outcome.error_type,
                "error_message": outcome.error_message,
            }
        )
        return row
    result = outcome.result
    if isinstance(result, p2.DualFEMSearchResult):
        row.update(
            {
                "best_max_point": [result.best_max.x, result.best_max.y],
                "best_max_value": result.best_max.max_value,
                "best_mean_point": [result.best_mean.x, result.best_mean.y],
                "best_mean_value": result.best_mean.mean_value,
                "coarse_count": len(result.coarse),
                "refined_count": len(result.refined),
            }
        )
    elif isinstance(result, p2.FEMSearchResult):
        row.update(
            {
                "metric": result.metric,
                "best_point": [result.best.x, result.best.y],
                "best_value": result.best.value,
                "coarse_count": len(result.coarse),
                "refined_count": len(result.refined),
            }
        )
    else:
        row["result_type"] = type(result).__name__
    return row


def _parse_precision_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--circle-samples", type=int, default=360)
    parser.add_argument("--maximum-constraints", type=int, default=96)
    parser.add_argument("--coverage-spacing", type=float, default=50.0)
    parser.add_argument("--safety-margin", type=float, default=None)
    parser.add_argument("--coarse-width", type=float, default=100.0)
    parser.add_argument("--refine-width", type=float, default=20.0)
    parser.add_argument("--angle-step", type=float, default=5.0)


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Problem 2 parallel case runner")
    parser.add_argument("--cases", type=int, default=4)
    parser.add_argument("--seed", type=int, default=20260911)
    parser.add_argument("--mode", choices=["max", "mean", "both"], default="both")
    parser.add_argument("--case-workers", type=int, default=2)
    parser.add_argument("--grid-workers", type=int, default=1)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--csv", type=Path, default=None)
    _parse_precision_args(parser)
    return parser


def _cli_main(argv: Sequence[str] | None = None) -> int:
    parser = _build_cli_parser()
    args = parser.parse_args(argv)
    config = ParallelConfig(case_workers=args.case_workers, grid_workers=args.grid_workers)
    base_params = {
        "circle_samples": args.circle_samples,
        "maximum_constraints": args.maximum_constraints,
        "coverage_spacing": args.coverage_spacing,
        "safety_margin": args.safety_margin,
        "coarse_width": args.coarse_width,
        "refine_width": args.refine_width,
        "angle_step_deg": args.angle_step,
    }
    tasks = generate_case_tasks(args.cases, args.seed, params=base_params, mode=args.mode)
    outcomes = run_cases_parallel(tasks, config, mode=args.mode, base_params=base_params)
    rows = [_outcome_to_flat(outcome) for outcome in outcomes]
    ok_count = sum(1 for row in rows if row["ok"])
    payload = {
        "mode": args.mode,
        "config": {"case_workers": args.case_workers, "grid_workers": args.grid_workers},
        "ok_count": ok_count,
        "total_count": len(rows),
        "results": rows,
    }
    if args.json is None:
        args.json = Path.cwd() / "problem2_parallel_results.json"
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    if args.csv is not None:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        if rows:
            fieldnames: list[str] = []
            for row in rows:
                for key in row.keys():
                    if key not in fieldnames:
                        fieldnames.append(key)
            with args.csv.open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(rows)
    print(f"ok={ok_count}/{len(rows)}")
    print(f"json={args.json}")
    if args.csv is not None:
        print(f"csv={args.csv}")
    return 0 if ok_count == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(_cli_main())
