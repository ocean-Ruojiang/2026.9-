import argparse
import json
import sys
from .config import Config
from .experiments import simulate, benchmark
from .client import HTTPTransport
from .runner import run
from .local_sim import add_arguments, run_batches, resolve_live


def main():
    if hasattr(sys.stdout,"reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="策略2：仅用于 B 第三问全向目标")
    sub = parser.add_subparsers(dest="command",required=True)
    s = sub.add_parser("simulate",help="Run a local synthetic case, no official connection")
    s.add_argument("--config")
    s.add_argument("--seed",type=int,default=11)
    s.add_argument("--scenario",choices=["uniform","edge","clustered","near","smooth"],default="uniform")
    s.add_argument("--output",required=True)
    b = sub.add_parser("benchmark",help="Paired local experiments using the same synthetic cases")
    b.add_argument("--configs",nargs="+",required=True)
    b.add_argument("--seeds",default="11,12,13")
    b.add_argument("--scenarios",default="uniform,edge,clustered")
    b.add_argument("--output",required=True)
    live = sub.add_parser("live",help="Connect once to an HTTP simulator; supports SIM_* environment variables")
    live.add_argument("--config")
    live.add_argument("--url",help="Overrides SIM_BASE_URL; manual default is http://127.0.0.1:2026")
    live.add_argument("--robot-id",help="Overrides SIM_ROBOT_ID; required outside the local harness")
    live.add_argument("--output",help="Overrides SIM_RUN_DIR/strategy2; required outside the local harness")
    add_arguments(sub.add_parser("local-sim",help="Run strategy2 in the project's local_simulator, with paired variants"))
    args = parser.parse_args()
    if args.command == "local-sim":
        try:
            result = run_batches(args)
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if not result["all_runs_ok"]:
            raise SystemExit(2)
        return
    if args.command == "benchmark":
        result = benchmark(args.configs,[int(s) for s in args.seeds.split(",")],
                           args.scenarios.split(","),args.output)
    elif args.command == "simulate":
        result = simulate(Config.load(args.config),args.seed,args.scenario,args.output)
    else:
        try:
            url, robot_id, output = resolve_live(args.url,args.robot_id,args.output)
        except ValueError as exc:
            parser.error(str(exc))
        result = run(Config.load(args.config),HTTPTransport(url),robot_id,output)
    failed = (result.get("reason") != "complete" or result.get("false_completion",False)
              if isinstance(result,dict) else
              any(r["completion_rate"] < 1 or r["false_completions"] for r in result))
    if isinstance(result,dict):
        result = {k:result[k] for k in ("strategy","variant","q2_provider","reason","error",
                  "complete_certificate","cleared_count","virtual_total_s","real_runtime_s")}
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if failed:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
