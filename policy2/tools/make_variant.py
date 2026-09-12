"""Create a validated single-factor variant without hand-editing several fields."""
import argparse
import json
import sys
from pathlib import Path
from dataclasses import asdict, replace, fields
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from strategy2.config import Config


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--base",required=True)
    p.add_argument("--name",required=True)
    p.add_argument("--field",required=True)
    p.add_argument("--value",required=True,help='JSON literal, e.g. 45, 0.1, or "\"module:factory\""')
    p.add_argument("--output",required=True)
    a=p.parse_args()
    if a.field=="name" or a.field not in {f.name for f in fields(Config)}:
        p.error("field must name one strategy parameter")
    base=Config.load(a.base)
    variant=replace(base,name=a.name,**{a.field:json.loads(a.value)}).validate()
    differences=[k for k,v in asdict(base).items() if k!="name" and v!=getattr(variant,k)]
    if differences != [a.field]:
        p.error("Variant must change exactly one parameter")
    path=Path(a.output)
    with path.open("x",encoding="utf-8") as out:
        json.dump(asdict(variant),out,ensure_ascii=False,indent=2)
    print(f"{a.field}: {getattr(base,a.field)} -> {getattr(variant,a.field)}")


if __name__=="__main__":
    main()
