"""Export completed simulator logs to a standalone, offline trajectory viewer.

python tools/visualize.py --input results/default_sweep --output results/replay.html
Only exported logs are read. The viewer never connects to a running simulator.
"""
import argparse
from collections import defaultdict
import json
import math
from pathlib import Path
import sys

OPERATIONS=("move","measure","clear_success","clear_failure")


def read_jsonl(path):
    with path.open(encoding="utf-8-sig") as file:
        for line_number,line in enumerate(file,1):
            if line.strip():
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise ValueError(f"Malformed JSONL: {path}:{line_number}") from exc


def discover_cases(path):
    path=Path(path).resolve()
    if path.is_file():
        path=path.parent
    if (path/"actions.jsonl").exists():
        return [path]
    runs=path/"runs.json"
    if runs.is_file():
        return list(dict.fromkeys(path/r["case_dir"] for r in json.loads(runs.read_text(encoding="utf-8-sig"))))
    return [p.parent for p in sorted(path.rglob("actions.jsonl"))]


def load_case(folder):
    folder=Path(folder)
    summary_path=folder/"summary.json"
    if not summary_path.is_file():
        raise ValueError(f"Only completed/exported cases can be viewed: {folder}")
    summary=json.loads(summary_path.read_text(encoding="utf-8-sig"))
    metadata={}
    pending={}
    event_path=folder/"strategy3"/"events.jsonl"
    if event_path.exists():
        for event in read_jsonl(event_path):
            if event.get("event")=="decision":
                action=event.get("action",{})
                details=action.get("details",{})
                pending=dict(mode=details.get("phase",event.get("phase","")),
                             reason=action.get("reason",""),
                             template=details.get("template",""),
                             budget=details.get("budget_remaining_before"))
            elif event.get("event")=="request" and event.get("path") in {"/measure","/clear"}:
                metadata[event["request"]["request_id"]]=pending.copy()
    actions=[]
    seen=set()
    previous=[0.,0.]
    previous_time=0.
    for event in read_jsonl(folder/"actions.jsonl"):
        request,response=event["request"],event["response"]
        if not response.get("accepted"):
            continue
        request_id=request.get("request_id")
        if request_id in seen:
            continue
        seen.add(request_id)
        if event["path"] not in {"/measure","/clear"}:
            continue
        position=request["position"]
        point=[float(position["x"]),float(position["y"])]
        if not all(math.isfinite(v) for v in point):
            raise ValueError(f"Non-finite action coordinates: {folder}")
        kind=event["path"][1:]
        outcome=response.get("measure_result") if kind=="measure" else response.get("clear_result")
        category="measure" if kind=="measure" else "clear_success" if outcome=="success" else "clear_failure"
        total=float(response["virtual_time_s"])
        actions.append(dict(index=len(actions)+1,point=point,previous=previous,kind=kind,
                    category=category,channel=int(request["channel"]),result=outcome,
                    bearing=response.get("svd_deg"),time=total,elapsed=total-previous_time,
                    distance=math.dist(point,previous),**metadata.get(request_id,{"mode":""})))
        previous,previous_time=point,total
    scenario_path=folder/"scenario.json"
    scenario=json.loads(scenario_path.read_text(encoding="utf-8-sig")) if scenario_path.exists() else {}
    config_path=folder/"strategy3"/"config.json"
    config=json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}
    strategy_path=folder/"strategy3"/"summary.json"
    strategy=json.loads(strategy_path.read_text(encoding="utf-8-sig")) if strategy_path.exists() else {}
    info_path=folder/"case_metadata.json"
    info=json.loads(info_path.read_text(encoding="utf-8-sig")) if info_path.exists() else {}
    return dict(label=info.get("name",folder.name),
                seed=scenario.get("seed"),variant=config.get("name",folder.parent.name),
                layout=scenario.get("layout"),radius_mode=scenario.get("radius_mode"),
                target_count=summary.get("target_count"),cleared_count=summary.get("cleared_count"),
                virtual_time=summary.get("virtual_time_s"),run_ok=summary.get("cleared_count")==summary.get("target_count") and strategy.get("complete",False),
                program_time=strategy.get("real_runtime_s"),planning_time=strategy.get("planning_time_s"),
                sources=scenario.get("sources",[]),actions=actions)


def sample_cases(paths,per_variant):
    """Deterministic stratified sampling, independent of performance or outcomes.

    For the 10+5 sweep and per_variant=3: random case 1 and 6, edge case 1.
    Same indices across variants preserve visual comparability.
    """
    if per_variant is None:return paths
    if per_variant<1:raise ValueError("sample-per-variant must be positive")
    variants=defaultdict(lambda:defaultdict(list))
    for path in paths:
        variants[path.parent.name][path.parent.parent.name].append(path)
    chosen=set()
    for groups in variants.values():
        total=sum(len(group) for group in groups.values())
        target=min(total,per_variant)
        quotas={name:target*len(group)/total for name,group in groups.items()}
        counts={name:math.floor(q) for name,q in quotas.items()}
        for name in sorted(groups,key=lambda name:(-(quotas[name]-counts[name]),name))[:target-sum(counts.values())]:
            counts[name]+=1
        for name,group in groups.items():
            group=sorted(group)
            for i in range(counts[name]):chosen.add(group[i*len(group)//counts[name]])
    return [path for path in paths if path in chosen]


def export(input_path,output_path,case_index=1,operations=OPERATIONS,channel=0,sample_per_variant=None):
    paths=discover_cases(input_path)
    available=len(paths)
    paths=sample_cases(paths,sample_per_variant)
    if not paths:raise ValueError("No actions.jsonl found; supply a case or experiment directory")
    if not 1<=case_index<=len(paths):raise ValueError(f"case must be 1..{len(paths)}")
    if set(operations)-set(OPERATIONS):raise ValueError(f"operations must be from {OPERATIONS}")
    if not 0<=channel<=20:raise ValueError("channel must be 0 (all) or 1..20")
    data=dict(cases=[load_case(p) for p in paths],initialCase=case_index-1,
              availableCases=available,samplePerVariant=sample_per_variant,
              operations=list(operations),channel=channel)
    data["stations"]=json.loads((Path(__file__).resolve().parents[1]/"strategy3"/"assets"/"stations_22.json").read_text(encoding="utf-8"))["stations"]
    # Script data stays inert even if an imported label contains HTML or </script>.
    encoded=json.dumps(data,ensure_ascii=False,allow_nan=False,separators=(",",":"))
    encoded=encoded.replace("&","\\u0026").replace("<","\\u003c").replace(">","\\u003e")
    template=Path(__file__).with_name("trajectory_viewer.html").read_text(encoding="utf-8")
    html=template.replace("__TRAJECTORY_DATA__",encoded)
    output=Path(output_path).resolve()
    if output.suffix.lower()!=".html":raise ValueError("output must end in .html")
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(html,encoding="utf-8")
    selection=dict(available_cases=available,selected_cases=len(paths),sample_per_variant=sample_per_variant,
                   method="Deterministic proportional stratum allocation; evenly spaced indices, no outcome selection",
                   cases=[dict(variant=p.parent.name,group=p.parent.parent.name,case=p.name) for p in paths])
    output.with_suffix(".selection.json").write_text(json.dumps(selection,ensure_ascii=False,indent=2),encoding="utf-8")
    return dict(output=str(output),available_cases=available,cases=len(paths),actions=sum(len(c["actions"]) for c in data["cases"]))


def main():
    if hasattr(sys.stdout,"reconfigure"):sys.stdout.reconfigure(encoding="utf-8")
    parser=argparse.ArgumentParser(description="Offline replay: choose case, channel, operations and action step")
    parser.add_argument("--input",required=True,help="Completed case, batch or sweep directory")
    parser.add_argument("--output",required=True,help="HTML viewer; this generated file may be regenerated")
    parser.add_argument("--case",type=int,default=1,help="Initial case, one-based")
    parser.add_argument("--operations",default=",".join(OPERATIONS),help="Comma-separated move,measure,clear_success,clear_failure")
    parser.add_argument("--channel",type=int,default=0,help="0=all, otherwise 1..20")
    parser.add_argument("--sample-per-variant",type=int,help="Export this many stratified cases per variant; 3 for the 15-case sweep")
    args=parser.parse_args()
    try:
        result=export(args.input,args.output,args.case,args.operations.split(","),args.channel,args.sample_per_variant)
    except (ValueError,OSError) as exc:
        parser.error(str(exc))
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=="__main__":main()
