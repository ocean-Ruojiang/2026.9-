"""Optional strategy-side client; uses the same four HTTP endpoints as the contest."""
import itertools
import json
import os
import uuid
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, ProxyHandler


class Client:
    def __init__(self, base_url=None, robot_id=None, timeout=5, retries=2):
        self.base_url = (base_url or os.environ.get("SIM_BASE_URL", "http://127.0.0.1:2027")).rstrip("/")
        self.robot_id = robot_id or os.environ.get("SIM_ROBOT_ID", "local")
        self.timeout, self.retries = timeout, retries
        self.counter, self.prefix = itertools.count(), uuid.uuid4().hex
        self.opener = build_opener(ProxyHandler({}))

    def post(self, path, **values):
        payload = dict(arena_id="default", robot_id=self.robot_id,
                       request_id=f"{self.prefix}-{next(self.counter)}", **values)
        data = json.dumps(payload, allow_nan=False).encode()
        # Network retries reuse exactly the same payload and request id.
        for attempt in range(self.retries + 1):
            try:
                req = Request(self.base_url + path, data=data, headers={"Content-Type": "application/json"})
                with self.opener.open(req, timeout=self.timeout) as r:
                    result = json.load(r)
                if not result.get("accepted"):
                    raise RuntimeError(f"Simulator rejected {path}: {result}")
                return result
            except HTTPError:
                raise
            except (URLError, TimeoutError, ConnectionError):
                if attempt == self.retries:
                    raise

    def enter(self):
        return self.post("/enter")

    def measure(self, x, y, channel):
        return self.post("/measure", position={"x": x, "y": y}, channel=channel)

    def clear(self, x, y, channel):
        return self.post("/clear", position={"x": x, "y": y}, channel=channel)

    def exit(self):
        return self.post("/exit")
