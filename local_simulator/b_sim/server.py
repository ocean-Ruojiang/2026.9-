import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json

from .core import SessionClosed, decode_request


class Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, simulator, port=0):
        self.simulator = simulator
        super().__init__(("127.0.0.1", port), Handler)
        self.url = f"http://127.0.0.1:{self.server_port}"

    def start(self):
        thread = threading.Thread(target=self.serve_forever, kwargs={"poll_interval": .05}, daemon=True)
        thread.start()
        return thread


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(5)

    def log_message(self, *args):
        pass

    def reply(self, status, obj=None):
        data = json.dumps(obj if obj is not None else self.server.simulator.response(False),
                          ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True
        self.wfile.write(data)

    def do_POST(self):
        if self.path not in {"/enter", "/measure", "/clear", "/exit"}:
            return self.reject(404)
        content_type = self.headers.get("Content-Type", "").lower().replace(" ", "")
        if content_type not in {"application/json", "application/json;charset=utf-8", 'application/json;charset="utf-8"'}:
            return self.reject(415)
        if self.headers.get("Content-Encoding", "identity").lower() != "identity":
            return self.reject(415)
        if self.headers.get("Transfer-Encoding"):
            return self.reply(400)
        try:
            length = int(self.headers.get("Content-Length", "-1"))
            if length > 65536:
                return self.reject(413)
            if length < 0:
                return self.reply(400)
            body = self.rfile.read(length)
            if len(body) != length:
                return self.reply(400)
            body = decode_request(body)
        except (ValueError, UnicodeError, RecursionError):
            return self.reply(400)
        except OSError:
            self.close_connection = True
            return
        try:
            status, response = self.server.simulator.handle(self.path, body)
            self.reply(status, response)
        except SessionClosed:
            self.close_connection = True

    def reject(self, status):
        # Drain modest invalid requests so Windows does not reset the connection
        # before the client receives its error response. Never allocate unbounded data.
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if 0 < length <= 1_048_576:
                self.rfile.read(length)
        except (ValueError, OSError):
            pass
        self.reply(status)

    def do_GET(self):
        self.reply(405 if self.path in {"/enter", "/measure", "/clear", "/exit"} else 404)

    do_PUT = do_GET
    do_DELETE = do_GET
    do_PATCH = do_GET
    do_HEAD = do_GET
    do_OPTIONS = do_GET
