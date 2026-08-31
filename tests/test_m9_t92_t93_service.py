"""T-9.2 (Service API) + T-9.3 (canonical event adapter) — docs/17-research-alignment.md
M9. `harness[server]` — chạy ASGI app trong tiến trình test qua Starlette `TestClient`
(httpx over ASGI transport, không mở socket thật)."""
import asyncio
import json
import sys
import tempfile
import time
import unittest

sys.path.insert(0, "src"); sys.path.insert(0, "tests")

from starlette.testclient import TestClient

from harness import Agent, tool
from harness.models.fake import FakeModel
from harness.observe.transcript import TranscriptWriter
from harness.observe.events import Event, EventKind
from harness.server import create_app


def _wait_until(client, run_id, *, not_status="running", timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/v1/runs/{run_id}").json()
        if body["status"] != not_status:
            return body
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} vẫn '{not_status}' sau {timeout}s")


class HappyPath(unittest.TestCase):
    def test_start_status_and_result(self):
        agent = Agent(name="T", job="j", model="fake", budget="$5",
                      provider=FakeModel([FakeModel.text("xin chao")]))
        app = create_app({"t": agent})
        with TestClient(app) as client:
            r = client.post("/v1/runs", json={"agent": "t", "message": "hi"})
            self.assertEqual(r.status_code, 202)
            run_id = r.json()["id"]
            body = _wait_until(client, run_id)
            self.assertEqual(body["status"], "completed")
            self.assertTrue(body["result"]["ok"])
            self.assertEqual(body["result"]["text"], "xin chao")

    def test_unknown_agent_is_404(self):
        app = create_app({})
        with TestClient(app) as client:
            r = client.post("/v1/runs", json={"agent": "nope", "message": "hi"})
            self.assertEqual(r.status_code, 404)

    def test_bad_body_is_400(self):
        app = create_app({})
        with TestClient(app) as client:
            r = client.post("/v1/runs", json={"agent": "t"})
            self.assertEqual(r.status_code, 400)

    def test_unknown_run_id_is_404(self):
        app = create_app({})
        with TestClient(app) as client:
            self.assertEqual(client.get("/v1/runs/nope").status_code, 404)
            self.assertEqual(client.post("/v1/runs/nope/cancel").status_code, 404)


class EventsSSE(unittest.TestCase):
    """T-9.3 — SSE dùng ĐÚNG `to_canonical_json`, mang đủ envelope v1."""

    def test_stream_carries_canonical_envelope(self):
        agent = Agent(name="T", job="j", model="fake", budget="$5",
                      provider=FakeModel([FakeModel.text("ok")]))
        app = create_app({"t": agent})
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"agent": "t", "message": "hi"}).json()["id"]
            _wait_until(client, run_id)
            with client.stream("GET", f"/v1/runs/{run_id}/events") as resp:
                lines = [ln for ln in resp.iter_lines() if ln.startswith("data: ")]
            parsed = [json.loads(ln[len("data: "):]) for ln in lines]
            events = [e for e in parsed if "kind" in e]        # bỏ "data: {}" của event: end
            self.assertTrue(events, "không có event nào trong SSE stream")
            kinds = {e["kind"] for e in events}
            self.assertIn("run.started", kinds)
            self.assertIn("run.finished", kinds)
            for e in events:
                self.assertIn("schema_version", e)
                self.assertIn("trace_id", e)
                self.assertIn("tenant_id", e)
                self.assertIn("session_id", e)


class CancelRun(unittest.TestCase):
    def test_cancel_a_slow_run(self):
        @tool(effect="write")
        async def slow(x: int) -> str:
            """Chậm, để cancel kịp can thiệp giữa chừng."""
            await asyncio.sleep(2.0)
            return "done"

        agent = Agent(name="T", job="j", model="fake", tools=[slow], budget="$5",
                      provider=FakeModel([FakeModel.tool_call("slow", {"x": 1}),
                                          FakeModel.text("ok")]))
        app = create_app({"t": agent})
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"agent": "t", "message": "go"}).json()["id"]
            time.sleep(0.1)
            r = client.post(f"/v1/runs/{run_id}/cancel")
            self.assertEqual(r.status_code, 200)
            body = _wait_until(client, run_id, timeout=5.0)
            self.assertEqual(body["status"], "cancelled")


class ApprovalsBridge(unittest.TestCase):
    def test_approve_over_http_unblocks_a_pending_ask(self):
        @tool(effect="danger")
        def wipe() -> str:
            """Nguy hiểm."""
            return "wiped"

        agent = Agent(name="T", job="j", model="fake", tools=[wipe], budget="$5",
                      provider=FakeModel([FakeModel.tool_call("wipe", {}),
                                          FakeModel.text("done")]))
        app = create_app({"t": agent})
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"agent": "t", "message": "go"}).json()["id"]

            deadline = time.monotonic() + 5.0
            pending = []
            while time.monotonic() < deadline:
                pending = client.get(f"/v1/runs/{run_id}").json().get("pending_approvals", [])
                if pending:
                    break
                time.sleep(0.02)
            self.assertTrue(pending, "không thấy approval nào chờ")
            self.assertEqual(pending[0]["tool"], "wipe")

            r = client.post(f"/v1/runs/{run_id}/approvals/{pending[0]['approval_id']}",
                            json={"approve": True})
            self.assertEqual(r.status_code, 200)

            body = _wait_until(client, run_id)
            self.assertEqual(body["status"], "completed")
            self.assertIn("wipe", body["result"]["tools_run"])

    def test_deny_over_http_blocks_the_tool(self):
        @tool(effect="danger")
        def wipe() -> str:
            """Nguy hiểm."""
            return "wiped"

        agent = Agent(name="T", job="j", model="fake", tools=[wipe], budget="$5",
                      provider=FakeModel([FakeModel.tool_call("wipe", {}),
                                          FakeModel.text("done")]))
        app = create_app({"t": agent})
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"agent": "t", "message": "go"}).json()["id"]

            deadline = time.monotonic() + 5.0
            pending = []
            while time.monotonic() < deadline:
                pending = client.get(f"/v1/runs/{run_id}").json().get("pending_approvals", [])
                if pending:
                    break
                time.sleep(0.02)
            self.assertTrue(pending)

            client.post(f"/v1/runs/{run_id}/approvals/{pending[0]['approval_id']}",
                       json={"approve": False})
            body = _wait_until(client, run_id)
            self.assertNotIn("wipe", body["result"]["tools_run"])

    def test_approvals_route_is_409_when_agent_has_its_own_approve(self):
        agent = Agent(name="T", job="j", model="fake", budget="$5",
                      approve=lambda call, ctx: True,
                      provider=FakeModel([FakeModel.text("ok")]))
        app = create_app({"t": agent})
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"agent": "t", "message": "go"}).json()["id"]
            _wait_until(client, run_id)
            r = client.post(f"/v1/runs/{run_id}/approvals/appr_1", json={"approve": True})
            self.assertEqual(r.status_code, 409)


class CanonicalTranscript(unittest.TestCase):
    """T-9.3 phần CLI/JSON — `TranscriptWriter` giờ ghi ĐÚNG envelope v1, không tự dựng
    dict riêng nữa (trước bản vá: schema_version/trace_id/tenant_id/session_id bị rơi)."""

    def test_transcript_line_carries_full_envelope(self):
        with tempfile.TemporaryDirectory() as d:
            path = f"{d}/t.jsonl"
            w = TranscriptWriter(path)
            w.emit(Event(0, 1234.5, "r1", EventKind.RUN_STARTED, 0, {"a": 1},
                        trace_id="tr1", tenant_id="ten1", session_id="ses1"))
            w.close()
            with open(path) as fh:
                row = json.loads(fh.readline())
            for k in ("schema_version", "trace_id", "tenant_id", "session_id",
                     "seq", "ts", "run_id", "kind", "step", "data"):
                self.assertIn(k, row, f"thiếu {k!r} trong dòng transcript")
            self.assertEqual(row["trace_id"], "tr1")
            self.assertEqual(row["tenant_id"], "ten1")
            self.assertEqual(row["session_id"], "ses1")


if __name__ == "__main__":
    unittest.main()
