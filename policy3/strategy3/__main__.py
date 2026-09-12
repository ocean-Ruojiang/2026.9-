import argparse
import sys


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0]=='local-sim':
        from .local_sim import main as local_main
        return local_main(argv[1:])
    p=argparse.ArgumentParser(description='Policy 3: staged directional search and clearing')
    sub=p.add_subparsers(dest='command',required=True)
    run=sub.add_parser('run',help='Connect to an already running simulator over HTTP')
    run.add_argument('--config')
    run.add_argument('--output')
    run.add_argument('--base-url')
    run.add_argument('--robot-id')
    sub.add_parser('verify-layout',help='Verify the continuous 22-station coverage certificate')
    args=p.parse_args(argv)
    if args.command=='verify-layout':
        from .coverage import load_verified_layout
        stations,leaves=load_verified_layout()
        print(f'Verified {len(stations)} stations and {len(leaves)} continuous coverage cells.')
        return 0
    from .runner import run
    return run(args.config,args.output,args.base_url,args.robot_id)


if __name__=='__main__':
    raise SystemExit(main())
