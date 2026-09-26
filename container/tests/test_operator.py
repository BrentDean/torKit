from __future__ import annotations

import http.client
import json
from http.server import HTTPServer
from threading import Lock, Thread
import unittest

from torkit_host.model import SERVICES
from torkit_host.operator import HOST, OperatorHandler


class FakeRuntime:
    def __init__(self):
        self.burned = []
        self.nuked = False
        self.cleared_board = False
        self.clear_result = True
        self.launched = []
        self.stopped = []
        self.statuses = {name: 'absent' for name in SERVICES}

    def health_snapshot(self):
        return {
            name: {
                "state": "running", "health": "healthy", "ready": True,
                "restarts": "0",
            }
            for name in SERVICES
        }

    def burn(self, selected):
        self.burned.extend(selected)

    def nuke(self):
        self.nuked = True

    def clear_board(self):
        self.cleared_board = True
        return self.clear_result

    def ensure(self, selected):
        self.launched.extend(selected)
        for service in selected:
            self.statuses[service.name] = "running"

    def status(self, service):
        return self.statuses[service.name]

    def ready(self, service):
        return self.statuses[service.name] == "running"

    def access(self, service):
        if not self.ready(service):
            return {}
        return {
            "ONION_ADDRESS": "http://" + "a" * 56 + ".onion",
            "ACCESS_KEY": "FAKE_TEST_AUTH_VALUE",
        }

    def stop_many(self, selected):
        self.stopped.extend(selected)
        for service in selected:
            self.statuses[service.name] = "exited"


class OperatorTests(unittest.TestCase):
    def setUp(self):
        self.server = HTTPServer((HOST, 0), OperatorHandler)
        self.server.runtime = FakeRuntime()
        self.server.login_token = "test-secret-operator-token"
        self.server.session_token = "test-cookie-session"
        self.server.action_lock = Lock()
        self.server.job = {"state": "idle", "message": ""}
        self.thread = Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method, path, data=None, cookie=False,
                origin=True, host=None):
        conn = http.client.HTTPConnection(HOST, self.server.server_port)
        headers = {"Host": host or f"{HOST}:{self.server.server_port}"}
        if data is not None:
            headers["Content-Type"] = "application/json"
            headers["X-TorKit-Action"] = "1"
            if origin:
                headers["Origin"] = f"http://{HOST}:{self.server.server_port}"
            if cookie:
                headers["Cookie"] = "torkit_operator=test-cookie-session"
            payload = json.dumps(data)
        else:
            payload = None
            if cookie:
                headers["Cookie"] = "torkit_operator=test-cookie-session"
        conn.request(method, path, payload, headers)
        response = conn.getresponse()
        output = response.read()
        code = response.status
        headers_out = dict(response.getheaders())
        conn.close()
        return code, output, headers_out

    def test_assets_exist_and_are_not_cached(self):
        for path in ("/", "/app.js", "/style.css"):
            code, data, headers = self.request("GET", path)
            self.assertEqual(code, 200)
            self.assertTrue(data)
            self.assertEqual(headers.get("Cache-Control"), "no-store")
            self.assertIn("default-src 'none'", headers.get(
                "Content-Security-Policy", "",
            ))

    def test_console_loopback_and_credential_free_status(self):
        self.assertEqual(self.server.server_address[0], HOST)
        code, _, _ = self.request("GET", "/api/status")
        self.assertEqual(code, 401)
        code, data, _ = self.request("GET", "/api/status", cookie=True)
        self.assertEqual(code, 200)
        self.assertNotIn(b"ACCESS_KEY", data)
        self.assertNotIn(b"ONION_ADDRESS", data)

    def test_access_is_on_demand_authenticated_and_ready_only(self):
        body = {"service": "chat"}
        self.assertEqual(self.request("POST", "/api/access", body)[0], 401)
        self.assertEqual(self.request("POST", "/api/access", body,
                                      cookie=True, origin=False)[0], 403)
        self.assertEqual(self.request("POST", "/api/access",
                                      {"service": "not-a-service"}, cookie=True)[0], 400)
        self.assertEqual(self.request("POST", "/api/access", body,
                                      cookie=True)[0], 409)
        self.server.runtime.statuses["chat"] = "running"
        code, data, headers = self.request("POST", "/api/access",
                                           body, cookie=True)
        self.assertEqual(code, 200)
        payload = json.loads(data)
        self.assertTrue(payload["onion_address"].endswith(".onion"))
        self.assertEqual(payload["authorization_key"], "FAKE_TEST_AUTH_VALUE")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        status = self.request("GET", "/api/status", cookie=True)[1]
        self.assertNotIn(b"FAKE_TEST_AUTH_VALUE", status)
        self.server.runtime.statuses["chat"] = "exited"
        self.assertEqual(self.request("POST", "/api/access", body,
                                      cookie=True)[0], 409)

    def test_access_rejects_actions_in_progress_or_missing_values(self):
        self.server.runtime.statuses["chat"] = "running"
        body = {"service": "chat"}
        self.server.action_lock.acquire()
        try:
            self.assertEqual(self.request("POST", "/api/access",
                                          body, cookie=True)[0], 409)
        finally:
            self.server.action_lock.release()
        original = self.server.runtime.access
        self.server.runtime.access = lambda service: {}
        try:
            self.assertEqual(self.request("POST", "/api/access",
                                          body, cookie=True)[0], 409)
        finally:
            self.server.runtime.access = original

    def test_login_and_host_origin_restrictions(self):
        code, _, _ = self.request("POST", "/api/login", {"token": "wrong"})
        self.assertEqual(code, 401)
        code, _, _ = self.request(
            "POST", "/api/login", {"token": "test-secret-operator-token"},
            origin=False,
        )
        self.assertEqual(code, 403)
        code, _, _ = self.request(
            "POST", "/api/login", {"token": "test-secret-operator-token"},
            host="evil.example",
        )
        self.assertEqual(code, 403)
        code, _, headers = self.request(
            "POST", "/api/login", {"token": "test-secret-operator-token"},
        )
        self.assertEqual(code, 200)
        self.assertIn("HttpOnly", headers.get("Set-Cookie", ""))
        self.assertIn("SameSite=Strict", headers.get("Set-Cookie", ""))

    def test_burn_requires_auth_correct_scope_and_word(self):
        body = {"action": "burn", "service": "chat", "confirmation": "BURN"}
        self.assertEqual(self.request("POST", "/api/action", body)[0], 401)
        self.assertEqual(
            self.request("POST", "/api/action", body, cookie=True,
                         origin=False)[0],
            403,
        )
        self.assertEqual(
            self.request("POST", "/api/action",
                         {**body, "confirmation": "wrong"}, cookie=True)[0],
            400,
        )
        self.assertEqual(
            self.request("POST", "/api/action",
                         {**body, "service": "not-a-service"}, cookie=True)[0],
            400,
        )
        self.assertEqual(self.server.runtime.burned, [])
        code, data, _ = self.request(
            "POST", "/api/action",
            {"action": "burn", "service": "chat", "confirmation": "bUrN"},
            cookie=True,
        )
        self.assertEqual(code, 200)
        self.assertEqual(
            [service.name for service in self.server.runtime.burned],
            ["chat"],
        )
        self.assertFalse(self.server.runtime.nuked)
        self.assertEqual(json.loads(data)["message"], "Burned Chat.")

    def test_launch_requires_auth_and_launches_selected_service(self):
        body = {"action": "launch", "service": "chat", "confirmation": ""}
        self.assertEqual(self.request("POST", "/api/action", body)[0], 401)
        self.assertEqual(
            self.request("POST", "/api/action",
                         {**body, "service": "not-a-service"}, cookie=True)[0],
            400,
        )
        code, data, _ = self.request(
            "POST", "/api/action", body, cookie=True
        )
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(data)["message"], "Chat is ready.")
        self.assertEqual(
            [service.name for service in self.server.runtime.launched],
            ["chat"],
        )
        self.assertFalse(self.server.runtime.nuked)

    def test_stop_preserves_identity_and_checks_running_state(self):
        body = {"action": "stop", "service": "chat", "confirmation": ""}
        self.assertEqual(self.request("POST", "/api/action", body, cookie=True)[0],
                         409)
        self.server.runtime.statuses["chat"] = "running"
        code, data, _ = self.request(
            "POST", "/api/action", body, cookie=True
        )
        self.assertEqual(code, 200)
        self.assertIn("identity preserved", json.loads(data)["message"])
        self.assertEqual(
            [service.name for service in self.server.runtime.stopped],
            ["chat"],
        )
        self.assertEqual(self.server.runtime.burned, [])

    def test_busy_operation_rejects_launch_burn_and_nuke(self):
        self.server.action_lock.acquire()
        try:
            for action, service, confirmation in (
                ("launch", "chat", ""),
                ("burn", "chat", "BURN"),
                ("nuke", None, "NUKE"),
            ):
                self.assertEqual(self.request(
                    "POST", "/api/action",
                    {"action": action, "service": service,
                     "confirmation": confirmation},
                    cookie=True,
                )[0], 409)
        finally:
            self.server.action_lock.release()
        self.assertEqual(self.server.runtime.launched, [])
        self.assertEqual(self.server.runtime.burned, [])
        self.assertFalse(self.server.runtime.nuked)

    def test_clear_board_is_operator_only_with_case_insensitive_word(self):
        body = {"action": "clear_board", "service": "board",
                "confirmation": "cLeAr"}
        self.assertEqual(self.request("POST", "/api/action", body)[0], 401)
        self.assertEqual(self.request("POST", "/api/action",
                          {**body, "confirmation": "wrong"}, cookie=True)[0], 400)
        self.assertEqual(self.request("POST", "/api/action",
                          {**body, "service": "chat"}, cookie=True)[0], 400)
        self.assertFalse(self.server.runtime.cleared_board)
        status, data, _ = self.request("POST", "/api/action", body, cookie=True)
        self.assertEqual(status, 200)
        self.assertTrue(self.server.runtime.cleared_board)
        self.assertIn("identity preserved", json.loads(data)["message"])
        self.assertFalse(self.server.runtime.nuked)

    def test_nuke_requires_word_and_auth(self):
        body = {"action": "nuke", "confirmation": "wrong"}
        self.assertEqual(
            self.request("POST", "/api/action", body, cookie=True)[0], 400,
        )
        self.assertFalse(self.server.runtime.nuked)
        body["confirmation"] = "NuKe"
        self.assertEqual(
            self.request("POST", "/api/action", body, cookie=True)[0], 200,
        )
        self.assertTrue(self.server.runtime.nuked)


if __name__ == "__main__":
    unittest.main()
