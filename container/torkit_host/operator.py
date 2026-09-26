from __future__ import annotations

"""Temporary operator console; never expose it on an onion service."""

import hmac
import json
import secrets
import sys
import threading
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .model import SERVICES, TorKitError
from .runtime import TorKitRuntime

HOST = "127.0.0.1"
PORT = 8769
MAX_REQUEST_BYTES = 1024
ASSETS = Path(__file__).with_name("operator_assets")
ASSET_TYPES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
}


class OperatorHandler(BaseHTTPRequestHandler):
    """Token-protected, loopback-only control channel for the TorKit owner."""

    def log_message(self, _format: str, *args: object) -> None:
        # No request bodies, cookies or operator tokens in logs.
        pass

    def _respond(
        self, status: HTTPStatus, content: bytes, content_type: str,
        cookie: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; base-uri 'none'; form-action 'self'; "
            "frame-ancestors 'none'",
        )
        if cookie is not None:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(content)

    def _json(self, status: HTTPStatus, data: dict) -> None:
        self._respond(
            status, json.dumps(data).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _host_ok(self) -> bool:
        return self.headers.get("Host") == f"{HOST}:{self.server.server_port}"

    def _authorized(self) -> bool:
        cookies = SimpleCookie()
        try:
            cookies.load(self.headers.get("Cookie", ""))
        except CookieError:
            return False
        value = cookies.get("torkit_operator")
        return value is not None and hmac.compare_digest(
            value.value, self.server.session_token,
        )

    def _body(self) -> dict | None:
        if self.headers.get("Content-Type") != "application/json":
            return None
        length = self.headers.get("Content-Length", "")
        if not length.isdecimal() or not (0 < int(length) <= MAX_REQUEST_BYTES):
            return None
        try:
            data = json.loads(self.rfile.read(int(length)))
        except (UnicodeDecodeError, ValueError):
            return None
        return data if isinstance(data, dict) else None

    def do_GET(self) -> None:
        if not self._host_ok():
            self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid host."})
        elif self.path in ASSET_TYPES:
            filename, content_type = ASSET_TYPES[self.path]
            try:
                content = (ASSETS / filename).read_bytes()
            except OSError:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR,
                           {"error": "Console assets unavailable."})
                return
            self._respond(HTTPStatus.OK, content, content_type)
        elif self.path in {"/api/status", "/api/job"}:
            if not self._authorized():
                self._json(
                    HTTPStatus.UNAUTHORIZED,
                    {"error": "Operator authentication required."},
                )
            else:
                if self.path == "/api/job":
                    self._json(HTTPStatus.OK, self.server.job.copy())
                else:
                    self._json(HTTPStatus.OK, self.server.runtime.health_snapshot())
        else:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})

    def do_POST(self) -> None:
        if (not self._host_ok()
            or self.headers.get("Origin") != f"http://{HOST}:{self.server.server_port}"
            or self.headers.get("X-TorKit-Action") != "1"):
            self._json(HTTPStatus.FORBIDDEN, {"error": "Invalid request origin."})
            return
        data = self._body()
        if data is None:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid JSON request."})
            return

        if self.path == "/api/login":
            token = data.get("token")
            if not isinstance(token, str) or not hmac.compare_digest(
                token, self.server.login_token,
            ):
                self._json(HTTPStatus.UNAUTHORIZED,
                           {"error": "Invalid operator token."})
                return
            cookie = (
                "torkit_operator=" + self.server.session_token
                + "; Path=/; HttpOnly; SameSite=Strict; Max-Age=3600"
            )
            self._respond(
                HTTPStatus.OK, b'{"ok": true}',
                "application/json; charset=utf-8", cookie=cookie,
            )
            return

        if self.path not in {"/api/action", "/api/access"}:
            self._json(HTTPStatus.NOT_FOUND, {"error": "Not found."})
            return
        if not self._authorized():
            self._json(HTTPStatus.UNAUTHORIZED,
                       {"error": "Operator authentication required."})
            return

        if self.path == "/api/access":
            service_name = data.get("service")
            if not isinstance(service_name, str) or service_name not in SERVICES:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid service."})
                return
            if not self.server.action_lock.acquire(blocking=False):
                self._json(HTTPStatus.CONFLICT,
                           {"error": "A service operation is in progress."})
                return
            try:
                selected = SERVICES[service_name]
                if not self.server.runtime.ready(selected):
                    self._json(HTTPStatus.CONFLICT,
                               {"error": "Service is not ready; access unavailable."})
                    return
                access = self.server.runtime.access(selected)
                address = access.get("ONION_ADDRESS", "")
                key = access.get("ACCESS_KEY", "")
                if not address or not key:
                    self._json(HTTPStatus.CONFLICT,
                               {"error": "Service access information unavailable."})
                    return
                self._json(HTTPStatus.OK, {
                    "onion_address": address,
                    "authorization_key": key,
                })
                return
            finally:
                self.server.action_lock.release()

        action = data.get("action")
        confirmation = data.get("confirmation")
        if not isinstance(confirmation, str):
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Confirmation required."})
            return
        confirmation = confirmation.strip().casefold()
        if action == "launch":
            service = data.get("service")
            if not isinstance(service, str) or service not in SERVICES:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid service."})
                return
            selected = SERVICES[service]
            if not self.server.action_lock.acquire(blocking=False):
                self._json(HTTPStatus.CONFLICT, {"error": "Another operation is active."})
                return
            try:
                self.server.runtime.ensure([selected])
            except (TorKitError, OSError) as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                return
            finally:
                self.server.action_lock.release()
            self._json(HTTPStatus.OK, {"message": f"{selected.label} is ready."})
            return
        if action == "stop":
            service = data.get("service")
            if not isinstance(service, str) or service not in SERVICES:
                self._json(HTTPStatus.BAD_REQUEST, {"error": "Invalid service."})
                return
            selected = SERVICES[service]
            if self.server.runtime.status(selected) not in {"running", "restarting"}:
                self._json(HTTPStatus.CONFLICT, {"error": "Service is not running."})
                return
            if not self.server.action_lock.acquire(blocking=False):
                self._json(HTTPStatus.CONFLICT, {"error": "Another operation is active."})
                return
            try:
                self.server.runtime.stop_many([selected])
            except (TorKitError, OSError) as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})
                return
            finally:
                self.server.action_lock.release()
            self._json(HTTPStatus.OK,
                       {"message": f"Stopped {selected.label}; identity preserved."})
            return
        if action == "clear_board":
            if data.get("service") != "board" or confirmation != "clear":
                self._json(HTTPStatus.BAD_REQUEST,
                           {"error": "Select Board and type CLEAR."})
                return
            if not self.server.action_lock.acquire(blocking=False):
                self._json(HTTPStatus.CONFLICT,
                           {"error": "Another operation is active."})
                return
            try:
                resumed = self.server.runtime.clear_board()
            except (TorKitError, OSError) as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR,
                           {"error": str(exc)})
                return
            finally:
                self.server.action_lock.release()
            self._json(HTTPStatus.OK, {
                "message": "Board posts cleared. Onion identity preserved."
                + (" Board is back online." if resumed else ""),
            })
            return
        if action == "burn":
            service = data.get("service")
            if not isinstance(service, str) or service not in SERVICES or confirmation != "burn":
                self._json(HTTPStatus.BAD_REQUEST,
                           {"error": "Select a valid service and type BURN."})
                return
            if not self.server.action_lock.acquire(blocking=False):
                self._json(HTTPStatus.CONFLICT, {"error": "Another operation is active."})
                return
            try:
                self.server.runtime.burn([SERVICES[service]])
            except (TorKitError, OSError) as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR,
                           {"error": str(exc)})
                return
            finally:
                self.server.action_lock.release()
            self._json(HTTPStatus.OK,
                       {"message": f"Burned {SERVICES[service].label}."})
        elif action == "nuke":
            if confirmation != "nuke":
                self._json(HTTPStatus.BAD_REQUEST,
                           {"error": "Type NUKE to confirm."})
                return
            if not self.server.action_lock.acquire(blocking=False):
                self._json(HTTPStatus.CONFLICT, {"error": "Another operation is active."})
                return
            try:
                self.server.runtime.nuke()
            except (TorKitError, OSError) as exc:
                self._json(HTTPStatus.INTERNAL_SERVER_ERROR,
                           {"error": str(exc)})
                return
            finally:
                self.server.action_lock.release()
            self._json(
                HTTPStatus.OK,
                {"message": "TorKit runtime destroyed. Operator console closed."},
            )
            # HTTPServer.shutdown must not execute on the serving thread.
            threading.Thread(target=self.server.shutdown, daemon=True).start()
        else:
            self._json(HTTPStatus.BAD_REQUEST, {"error": "Unknown action."})


def serve_operator(runtime: TorKitRuntime) -> None:
    """Bind only to VPS loopback. Require an interactive operator terminal."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise TorKitError("operator console requires an interactive terminal")
    try:
        server = ThreadingHTTPServer((HOST, PORT), OperatorHandler)
    except OSError as exc:
        raise TorKitError(f"cannot start localhost operator console: {exc}") from exc

    server.runtime = runtime
    server.login_token = secrets.token_urlsafe(32)
    server.session_token = secrets.token_urlsafe(32)
    server.action_lock = threading.Lock()
    server.job = {"state": "idle", "message": ""}
    print(f"TorKit operator console: http://{HOST}:{PORT}", flush=True)
    print("Use an SSH local forward. Do not expose this port publicly.")
    print(f"Temporary operator token: {server.login_token}", flush=True)
    print("Ctrl-C closes the operator console.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nOperator console stopped.")
    finally:
        server.server_close()
