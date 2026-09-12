"""Serial HTTP client. The exact same serialized request survives retries."""
from dataclasses import dataclass
import json
import math
import time
import uuid
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError


class RejectedRequest(RuntimeError):
    pass


class UncertainRequest(RuntimeError):
    pass


@dataclass(frozen=True)
class Prepared:
    path: str
    body: bytes
    request_id: str

    def payload(self):
        return json.loads(self.body)


class Client:
    def __init__(self, transport, robot_id, cfg, journal):
        if not robot_id or len(robot_id) > 128 or any(ord(c)<32 for c in robot_id):
            raise ValueError("robot_id must be a valid team identifier")
        self.transport,self.robot_id,self.cfg,self.log = transport,robot_id,cfg,journal
        self.pending = None
        self.deadline = math.inf

    def prepare(self, path, p=None, op=None):
        if self.pending is not None:
            raise UncertainRequest("Previous request unresolved; do not issue a new ID")
        rid = uuid.uuid4().hex
        payload = {"arena_id":"default","robot_id":self.robot_id,"request_id":rid}
        if op:
            payload.update(position={"x":p[0],"y":p[1]},channel=op.channel)
        body = json.dumps(payload,ensure_ascii=False,allow_nan=False).encode("utf-8")
        self.pending = Prepared(path,body,rid)
        self.log("request_prepared",path=path,payload=payload)
        return self.pending

    def send(self, prepared):
        if prepared != self.pending:
            raise ValueError("Prepared request is not the current in-flight request")
        for attempt in range(self.cfg.http_retries+1):
            remaining = self.deadline-time.monotonic()
            if remaining <= 0:
                raise UncertainRequest("Deadline reached with pending request")
            try:
                result = self.transport.send(prepared,min(self.cfg.http_timeout_s,remaining))
                self.log("response",request_id=prepared.request_id,response=result,attempt=attempt)
                if result.get("accepted") is not True:
                    self.pending = None
                    raise RejectedRequest("Server rejected the action; world state unchanged")
                self.pending = None
                return result
            except HTTPError as exc:
                self.log("transport_error",request_id=prepared.request_id,attempt=attempt,error=str(exc))
                if exc.code < 500:
                    # HTTP errors do not update strategy state.
                    self.pending = None
                    raise RejectedRequest(f"HTTP {exc.code}") from exc
            except (URLError,TimeoutError,ConnectionError,OSError) as exc:
                self.log("transport_error",request_id=prepared.request_id,attempt=attempt,error=str(exc))
            if attempt < self.cfg.http_retries:
                time.sleep(min(.1*(attempt+1),max(0.,self.deadline-time.monotonic())))
        raise UncertainRequest("No definitive response; pending payload retained in journal")

    def call(self, path, p=None, op=None):
        prepared = self.prepare(path,p,op)
        return prepared.request_id,self.send(prepared)


class HTTPTransport:
    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")

    def send(self, prepared, timeout):
        request = Request(self.base_url+prepared.path,data=prepared.body,
                          headers={"Content-Type":"application/json"},method="POST")
        with urlopen(request,timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
