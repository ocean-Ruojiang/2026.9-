"""Protocol demonstration for sources.json; uses a known target, not a search strategy."""
import argparse
import json
import uuid
from urllib.request import Request, build_opener, ProxyHandler

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:2027")
    parser.add_argument("--robot-id", default="local")
    args = parser.parse_args()
    opener = build_opener(ProxyHandler({}))
    for path, extra in [
        ("/enter", {}),
        ("/measure", {"position": {"x": 0, "y": 0}, "channel": 1}),
        ("/clear", {"position": {"x": 100, "y": 0}, "channel": 1}),
        ("/exit", {}),
    ]:
        data = dict(arena_id="default", robot_id=args.robot_id, request_id=uuid.uuid4().hex, **extra)
        req = Request(args.base_url.rstrip("/") + path, data=json.dumps(data).encode(),
                      headers={"Content-Type": "application/json"}, method="POST")
        with opener.open(req, timeout=5) as response:
            result = json.load(response)
        print(path, json.dumps(result, ensure_ascii=False))
        if not result.get("accepted"):
            raise SystemExit("Request rejected; check robot ID, scene and session state.")

if __name__ == "__main__":
    main()
