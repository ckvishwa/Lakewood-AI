"""
T-030 Part 3 — local trace viewer / live session server.

Run:  python scripts/viewer/server.py [--port 8765]

Stdlib `http.server` only, deliberately — this is dev tooling, not the T-001
API layer (see docs/NEXT_TASKS.md T-001, and T-030's own scope note: adding
FastAPI or any framework here would pre-empt an architectural decision that
belongs to that task, for a tool that needs neither routing complexity nor
persistence). Binds to 127.0.0.1 ONLY — this can trigger real paid API
calls, and has no auth story, by design (see T-030's scope constraints).

This module drives sessions through the exact same `lakewood.chat.run_turn`
/`run_turn_traced` path every other caller uses (`lakewood/chat.py`'s own
CLI, `evals/runner.py`'s scorer). It never computes a price, never mutates
a cart directly, and never re-executes a replayed trace — replay only ever
reads a trace file already on disk.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "evals"))

from lakewood import orders as oe  # noqa: E402
from lakewood.chat import ChatState, run_turn_traced  # noqa: E402
from lakewood.interpreter import LLMInterpreter, RuleBasedInterpreter  # noqa: E402
from lakewood.llm_provider import ProviderConfigError  # noqa: E402
import catalog as ev_catalog  # noqa: E402

TRACE_DIR = os.path.join(REPO, "evals", "traces")
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# session_id -> (ChatState, TextInterpreter, meta dict). In-memory only, per
# the T-030 scope note (no persistence): the viewer is a rehearsal for the
# voice layer's frontend boundary, not a stand-in for T-005's persistence work.
_SESSIONS: dict[str, tuple] = {}


def new_live_session(provider: str, model: str | None) -> dict:
    sess = oe.Session(call_id="VIEWER", store_id="STORE-001", from_number="+10000000000")
    oe.set_order_type(sess, "pickup")
    chat = ChatState(session=sess)

    if provider == "rule_based":
        interpreter = RuleBasedInterpreter()
        model_name = None
    elif provider in ("ollama", "experiential"):
        # Single-process, localhost, one dev at a time — matches every other
        # constraint in this task. Not safe for concurrent different-provider
        # sessions; documented, not solved, per this task's own scope.
        os.environ["LAKEWOOD_LLM_PROVIDER"] = provider
        if model:
            os.environ["LAKEWOOD_LLM_MODEL"] = model
        interpreter = LLMInterpreter()
        model_name = interpreter.provider.model
    else:
        raise ValueError(f"unknown provider {provider!r}")

    session_id = uuid.uuid4().hex
    meta = {"provider": provider, "model": model_name}
    _SESSIONS[session_id] = (chat, interpreter, meta)
    return {"session_id": session_id, "provider": provider, "model": model_name}


def take_live_turn(session_id: str, utterance: str) -> dict:
    entry = _SESSIONS.get(session_id)
    if entry is None:
        raise KeyError(session_id)
    chat, interpreter, meta = entry
    _, trace = run_turn_traced(chat, interpreter, utterance)
    trace["provider"] = meta["provider"]
    trace["model"] = meta["model"]
    return trace


def list_trace_files() -> list[dict]:
    out = []
    for path in sorted(glob.glob(os.path.join(TRACE_DIR, "*.jsonl")), reverse=True):
        with open(path, encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        meta, n_cases = {}, len(lines)
        if lines:
            first = json.loads(lines[0])
            if "_meta" in first:
                meta, n_cases = first["_meta"], n_cases - 1
        out.append({"file": os.path.basename(path), "cases": n_cases, **meta})
    return out


def read_trace_file(filename: str) -> list[dict]:
    safe = os.path.basename(filename)  # never allow path traversal
    path = os.path.join(TRACE_DIR, safe)
    if not os.path.isfile(path):
        raise FileNotFoundError(safe)
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep stdout clean; never risk logging a body containing a key

    def _json(self, obj, status=200):
        body = json.dumps(obj, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        return json.loads(raw or b"{}")

    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self._serve_static("index.html")
        elif self.path == "/catalog.json":
            self._json(ev_catalog.build_catalog())
        elif self.path == "/api/traces":
            self._json(list_trace_files())
        elif self.path.startswith("/api/traces/"):
            filename = self.path[len("/api/traces/"):]
            try:
                self._json(read_trace_file(filename))
            except FileNotFoundError:
                self._json({"error": "not found"}, status=404)
        else:
            self._json({"error": "not found"}, status=404)

    def do_POST(self):
        try:
            body = self._read_json_body()
            if self.path == "/api/live/session":
                self._json(new_live_session(body.get("provider", "rule_based"),
                                            body.get("model")))
            elif self.path == "/api/live/turn":
                self._json(take_live_turn(body["session_id"], body["utterance"]))
            else:
                self._json({"error": "not found"}, status=404)
        except KeyError as e:
            self._json({"error": f"unknown session {e}"}, status=404)
        except ProviderConfigError as e:
            self._json({"error": str(e)}, status=400)
        except Exception as e:  # last resort — never leak a stack trace with env data
            self._json({"error": f"{type(e).__name__}: {e}"}, status=500)

    def _serve_static(self, name: str):
        path = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(path):
            self._json({"error": "not found"}, status=404)
            return
        with open(path, "rb") as f:
            body = f.read()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Lakewood trace viewer — http://127.0.0.1:{args.port}/  (localhost only, Ctrl+C to stop)")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
