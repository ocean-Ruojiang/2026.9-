from dataclasses import asdict
from pathlib import Path
import csv
import json
import statistics
from .config import Config
from .simulator import LocalTransport
from .runner import run


def simulate(cfg, seed, scenario, output):
    env = LocalTransport(seed,scenario)
    result = run(cfg,env,"local-research",output)
    # Truth is only released to the experiment evaluator after policy termination.
    truth = env.evaluation_after_run()
    result.update(truth)
    result["clear_ratio"] = truth["true_cleared"]/truth["true_count"]
    result["false_completion"] = bool(result["complete_certificate"] and
                                      truth["true_cleared"] != truth["true_count"])
    Path(output,"evaluation.json").write_text(
        json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    return result


def benchmark(config_paths, seeds, scenarios, output):
    root = Path(output)
    root.mkdir(parents=True,exist_ok=False)
    configs = [Config.load(p) for p in config_paths]
    if len({c.name for c in configs}) != len(configs):
        raise ValueError("Experiment configuration names must be unique")
    # Record config differences explicitly, even for comparisons that are not ablations.
    base = asdict(configs[0])
    differences = {c.name:{k:{"base":base[k],"value":v} for k,v in asdict(c).items()
                           if k!="name" and base[k]!=v} for c in configs}
    (root/"config_differences.json").write_text(
        json.dumps(differences,ensure_ascii=False,indent=2),encoding="utf-8")
    rows = []
    for scenario in scenarios:
        for index,seed in enumerate(seeds):
            # Interleave order, keep scene seeds and algorithm seeds distinct.
            order = configs[index%len(configs):] + configs[:index%len(configs)]
            for cfg in order:
                out = root/f"{cfg.name}_{scenario}_{seed}"
                row = simulate(cfg,seed,scenario,out)
                rows.append(row)
                print(json.dumps({k:row[k] for k in (
                    "variant","local_scenario","environment_seed","reason",
                    "cleared_count","true_count","virtual_total_s","real_runtime_s")},
                    ensure_ascii=False),flush=True)
    columns = ["variant","config_hash","q2_provider","local_scenario","environment_seed",
               "reason","complete_certificate","false_completion","true_count",
               "cleared_count","clear_ratio","virtual_total_s","avg_clear_s",
               "real_runtime_s","planning_time_s","path_length_m","measure_count",
               "clear_failed","coverage_done_s","last_discovery_s","geometry_inconsistent"]
    with (root/"runs.csv").open("w",encoding="utf-8-sig",newline="") as f:
        writer = csv.DictWriter(f,fieldnames=columns,extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    summary = []
    for cfg in configs:
        group = [r for r in rows if r["variant"]==cfg.name]
        completed = [r for r in group if r["complete_certificate"] and not r["false_completion"]]
        summary.append({
            "variant":cfg.name,"runs":len(group),"completed":len(completed),
            "completion_rate":len(completed)/len(group),
            "mean_clear_ratio_all_runs":statistics.mean(r["clear_ratio"] for r in group),
            "mean_virtual_s_completed":statistics.mean(r["virtual_total_s"] for r in completed) if completed else None,
            "median_real_s_all_runs":statistics.median(r["real_runtime_s"] for r in group),
            "false_completions":sum(r["false_completion"] for r in group),
            "errors":[r["error"] for r in group if r["error"]]})
    (root/"comparison.json").write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding="utf-8")
    return summary
