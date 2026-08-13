from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.main import create_app
from backend.quota import DemoQuotaConfig, DemoQuotaService
from backend.service import AskDataApplicationService
from backend.signed_auth import SignedAuthConfig, SignedAuthService


async def _request(app, method: str, path: str, *, body=None, cookie: str = ""):
    raw_body = json.dumps(body or {}).encode("utf-8") if body is not None else b""
    headers = [(b"content-type", b"application/json")]
    if cookie:
        headers.append((b"cookie", cookie.encode("latin-1")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method,
        "scheme": "https",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": headers,
        "client": ("203.0.113.9", 50123),
        "server": ("testserver", 443),
    }
    request_sent = False
    messages = []

    async def receive():
        nonlocal request_sent
        if not request_sent:
            request_sent = True
            return {"type": "http.request", "body": raw_body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message):
        messages.append(message)

    await app(scope, receive, send)
    start = next(message for message in messages if message["type"] == "http.response.start")
    response_body = b"".join(
        message.get("body", b"")
        for message in messages
        if message["type"] == "http.response.body"
    )
    response_headers = {}
    for key, value in start.get("headers", []):
        response_headers.setdefault(key.decode("latin-1").lower(), []).append(
            value.decode("latin-1")
        )
    return start["status"], response_headers, response_body


class PublicDemoEndToEndTestCase(unittest.TestCase):
    def test_guest_can_run_two_queries_and_export_but_third_is_blocked(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as temporary_dir, patch.dict(
                "os.environ", {}, clear=True
            ):
                runtime = Path(temporary_dir)
                service = AskDataApplicationService(runtime_dir=runtime)
                auth = SignedAuthService(
                    SignedAuthConfig(
                        email="guest@askdata.demo",
                        password="",
                        display_name="问数访客",
                        secret="e" * 32,
                    )
                )
                app = create_app(service=service, auth_service=auth)
                app.state.quota = DemoQuotaService(
                    DemoQuotaConfig(
                        db_path=runtime / "quota.db",
                        query_limit=2,
                        fingerprint_salt="e2e-test-salt",
                    )
                )
                try:
                    status, _, payload = await _request(app, "GET", "/health")
                    self.assertEqual(status, 200)
                    self.assertEqual(json.loads(payload)["status"], "ok")

                    status, headers, payload = await _request(
                        app, "POST", "/api/auth/guest", body={}
                    )
                    self.assertEqual(status, 200)
                    self.assertFalse(json.loads(payload)["user"]["is_admin"])
                    cookie = headers["set-cookie"][0].split(";", 1)[0]

                    status, _, _ = await _request(
                        app, "GET", "/api/auth/me", cookie=cookie
                    )
                    self.assertEqual(status, 200)

                    completed_runs = []
                    requests = (
                        {
                            "query": "查询总交易笔数大于50000的利率，并展示实际执行的SQL",
                            "session_id": "e2e-session-2",
                            "generate_chart": False,
                        },
                        {
                            "query": "请生成最近30天销售额折线图",
                            "session_id": "e2e-session",
                            "generate_chart": True,
                        },
                    )
                    for request_body in requests:
                        status, _, payload = await _request(
                            app, "POST", "/api/chat", body=request_body, cookie=cookie
                        )
                        self.assertEqual(status, 202, payload.decode("utf-8"))
                        run_id = json.loads(payload)["run_id"]
                        snapshot = None
                        for _ in range(200):
                            status, _, payload = await _request(
                                app, "GET", f"/api/runs/{run_id}", cookie=cookie
                            )
                            self.assertEqual(status, 200)
                            snapshot = json.loads(payload)
                            if snapshot["status"] in {"completed", "failed", "cancelled"}:
                                break
                            await asyncio.sleep(0.01)
                        self.assertEqual(snapshot["status"], "completed", snapshot)
                        self.assertTrue(snapshot["result"]["sql"]["text"], snapshot)
                        self.assertTrue(snapshot["result"]["table"]["rows"], snapshot)
                        completed_runs.append((run_id, snapshot["result"]))

                    self.assertTrue(completed_runs[1][1]["charts"])
                    run_id = completed_runs[1][0]
                    status, headers, csv_body = await _request(
                        app, "GET", f"/api/runs/{run_id}/export.csv", cookie=cookie
                    )
                    self.assertEqual(status, 200)
                    self.assertIn("text/csv", headers["content-type"][0])
                    self.assertGreater(len(csv_body), 20)

                    status, headers, xlsx_body = await _request(
                        app, "GET", f"/api/runs/{run_id}/export.xlsx", cookie=cookie
                    )
                    self.assertEqual(status, 200)
                    self.assertIn("spreadsheetml", headers["content-type"][0])
                    self.assertTrue(xlsx_body.startswith(b"PK"))

                    status, _, payload = await _request(
                        app,
                        "POST",
                        "/api/chat",
                        body={
                            "query": "第三次请求应被限制",
                            "session_id": "e2e-session",
                        },
                        cookie=cookie,
                    )
                    self.assertEqual(status, 429, payload.decode("utf-8"))
                finally:
                    service.close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
