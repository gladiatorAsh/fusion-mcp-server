"""FusionMCPBridge: a Fusion add-in that exposes a small, authenticated JSON API on localhost.

The Fusion API is not thread-safe, so the HTTP server runs on a background thread and hands every
request to Fusion's main thread through a custom event, then waits for the result.

Install: Fusion > Utilities > Add-Ins > Scripts and Add-Ins > + > select this folder > Run.
"""

import json
import os
import queue
import secrets
import threading
import traceback
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import adsk.core
import adsk.fusion

EVENT_ID = "FusionMCPBridge_call"
HOST = "127.0.0.1"
PORT = int(os.environ.get("FUSION_MCP_PORT", "47120"))
STATE_DIR = Path.home() / ".fusion-mcp"
TIMEOUT_S = 300

_app = adsk.core.Application.get()
_handlers = []
_pending = {}  # call id -> (method, params, result queue)
_server = None
_token = ""


# ---------- commands (always run on Fusion's main thread) ----------


def _design() -> adsk.fusion.Design:
    design = adsk.fusion.Design.cast(_app.activeProduct)
    if not design:
        raise RuntimeError("No active Fusion design. Open or create a design first.")
    return design


def document_info(_params):
    design = _design()
    root = design.rootComponent
    return {
        "document": _app.activeDocument.name,
        "units": design.unitsManager.defaultLengthUnits,
        "components": design.allComponents.count,
        "bodies": root.bRepBodies.count,
        "sketches": root.sketches.count,
        "features": root.features.count,
        "user_parameters": design.userParameters.count,
    }


def list_parameters(_params):
    design = _design()
    out = []
    for i in range(design.allParameters.count):
        p = design.allParameters.item(i)
        out.append(
            {
                "name": p.name,
                "expression": p.expression,
                "value": p.value,
                "unit": p.unit,
                "comment": p.comment,
                "user": adsk.fusion.UserParameter.cast(p) is not None,
            }
        )
    return {"parameters": out}


def set_parameter(params):
    design = _design()
    p = design.allParameters.itemByName(params["name"])
    if not p:
        raise KeyError(f"No parameter named {params['name']!r}")
    before = p.expression
    p.expression = params["expression"]  # Fusion validates the expression and recomputes the model
    return {"name": p.name, "before": before, "after": p.expression, "value": p.value, "unit": p.unit}


def export(params):
    design = _design()
    fmt = params["format"].lower()
    path = str(Path(params["path"]).expanduser())
    em = design.exportManager
    if fmt == "step":
        options = em.createSTEPExportOptions(path)
    elif fmt == "stl":
        options = em.createSTLExportOptions(design.rootComponent, path)
    elif fmt == "f3d":
        options = em.createFusionArchiveExportOptions(path)
    else:
        raise ValueError(f"Unsupported format {fmt!r}; use step, stl or f3d")
    if not em.execute(options):
        raise RuntimeError("Fusion reported the export failed")
    return {"path": path, "format": fmt, "bytes": os.path.getsize(path) if os.path.exists(path) else None}


COMMANDS = {
    "document_info": document_info,
    "list_parameters": list_parameters,
    "set_parameter": set_parameter,
    "export": export,
}


# ---------- main-thread dispatch ----------


class _CallHandler(adsk.core.CustomEventHandler):
    def notify(self, args):
        call_id = json.loads(args.additionalInfo)["id"]
        method, params, result_q = _pending.pop(call_id)
        try:
            result_q.put({"ok": True, "result": COMMANDS[method](params)})
        except Exception as e:  # report every failure back to the caller instead of crashing Fusion
            result_q.put({"ok": False, "error": f"{type(e).__name__}: {e}"})


def _run_on_main_thread(method, params):
    result_q = queue.Queue(maxsize=1)
    call_id = uuid.uuid4().hex
    _pending[call_id] = (method, params, result_q)
    _app.fireCustomEvent(EVENT_ID, json.dumps({"id": call_id}))
    try:
        return result_q.get(timeout=TIMEOUT_S)
    except queue.Empty:
        _pending.pop(call_id, None)
        return {"ok": False, "error": f"Timed out after {TIMEOUT_S}s waiting for Fusion"}


# ---------- HTTP server (background thread) ----------


class _Handler(BaseHTTPRequestHandler):
    def _send(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"ok": True, "commands": sorted(COMMANDS)})
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        if self.headers.get("Authorization") != f"Bearer {_token}":
            return self._send(401, {"ok": False, "error": "bad or missing token"})
        if self.path != "/call":
            return self._send(404, {"ok": False, "error": "not found"})
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            method = body["method"]
        except (ValueError, KeyError):
            return self._send(400, {"ok": False, "error": "expected JSON body with 'method' and 'params'"})
        if method not in COMMANDS:
            return self._send(404, {"ok": False, "error": f"unknown method {method!r}"})
        self._send(200, _run_on_main_thread(method, body.get("params") or {}))

    def log_message(self, *_):  # keep Fusion's text console quiet
        pass


def run(_context):
    global _server, _token
    try:
        event = _app.registerCustomEvent(EVENT_ID)
        handler = _CallHandler()
        event.add(handler)
        _handlers.append(handler)

        # A fresh token per session, readable only by the current user, so other local programs can't drive Fusion.
        STATE_DIR.mkdir(exist_ok=True)
        _token = secrets.token_urlsafe(32)
        token_file = STATE_DIR / "token"
        token_file.write_text(_token)
        os.chmod(token_file, 0o600)

        _server = ThreadingHTTPServer((HOST, PORT), _Handler)
        threading.Thread(target=_server.serve_forever, daemon=True).start()
        _app.log(f"FusionMCPBridge listening on http://{HOST}:{PORT}")
    except Exception:
        _app.userInterface.messageBox(f"FusionMCPBridge failed to start:\n{traceback.format_exc()}")


def stop(_context):
    global _server
    if _server:
        _server.shutdown()
        _server = None
    _app.unregisterCustomEvent(EVENT_ID)
    _handlers.clear()
    try:
        (STATE_DIR / "token").unlink()
    except FileNotFoundError:
        pass
