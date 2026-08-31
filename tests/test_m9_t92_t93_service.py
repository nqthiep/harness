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

    def test_idempotency_key_replays_instead_of_starting_a_second_run(self):
        """S-4 re-verify — client-supplied idempotency_key: một POST lặp lại (client tự
        timeout rồi retry) phải trả lại ĐÚNG run cũ, không khởi một run thứ hai."""
        agent = Agent(name="T", job="j", model="fake", budget="$5",
                      provider=FakeModel([FakeModel.text("ok")]))
        app = create_app({"t": agent})
        with TestClient(app) as client:
            r1 = client.post("/v1/runs", json={"agent": "t", "message": "hi",
                                               "idempotency_key": "client-key-1"})
            self.assertEqual(r1.status_code, 202)
            self.assertFalse(r1.json()["replayed"])
            id1 = r1.json()["id"]

            r2 = client.post("/v1/runs", json={"agent": "t", "message": "hi khác hẳn",
                                               "idempotency_key": "client-key-1"})
            self.assertEqual(r2.status_code, 200)
            self.assertTrue(r2.json()["replayed"])
            self.assertEqual(r2.json()["id"], id1, "phải trả lại run CŨ, không tạo mới")

    def test_different_idempotency_keys_start_different_runs(self):
        agent = Agent(name="T", job="j", model="fake", budget="$5",
                      provider=FakeModel([FakeModel.text("ok"), FakeModel.text("ok2")]))
        app = create_app({"t": agent})
        with TestClient(app) as client:
            r1 = client.post("/v1/runs", json={"agent": "t", "message": "hi",
                                               "idempotency_key": "key-a"})
            r2 = client.post("/v1/runs", json={"agent": "t", "message": "hi",
                                               "idempotency_key": "key-b"})
            self.assertNotEqual(r1.json()["id"], r2.json()["id"])

    def test_no_idempotency_key_always_starts_a_new_run(self):
        agent = Agent(name="T", job="j", model="fake", budget="$5",
                      provider=FakeModel([FakeModel.text("ok"), FakeModel.text("ok2")]))
        app = create_app({"t": agent})
        with TestClient(app) as client:
            r1 = client.post("/v1/runs", json={"agent": "t", "message": "hi"})
            r2 = client.post("/v1/runs", json={"agent": "t", "message": "hi"})
            self.assertNotEqual(r1.json()["id"], r2.json()["id"])

    def test_non_string_idempotency_key_is_400(self):
        agent = Agent(name="T", job="j", model="fake", budget="$5",
                      provider=FakeModel([FakeModel.text("ok")]))
        app = create_app({"t": agent})
        with TestClient(app) as client:
            r = client.post("/v1/runs", json={"agent": "t", "message": "hi",
                                              "idempotency_key": 123})
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


class Backpressure(unittest.TestCase):
    """S-14 (design/07-risks-and-open-issues.md) — buffer SSE bị giới hạn, và một
    subscriber tụt lại phía sau được BÁO, không âm thầm mất event."""

    def test_slow_subscriber_gets_a_dropped_notice_not_a_silent_gap(self):
        from unittest.mock import patch

        @tool(effect="read")
        def peek(x: int) -> str:
            """Nhìn."""
            return "ok"

        script = []
        for i in range(6):
            script.append(FakeModel.tool_call("peek", {"x": i}, call_id=f"c{i}"))
        script.append(FakeModel.text("xong"))

        agent = Agent(name="T", job="j", model="fake", tools=[peek], budget="$5",
                      provider=FakeModel(script))
        app = create_app({"t": agent})
        # Buffer cực nhỏ — chắc chắn tràn với 6 lượt gọi tool (mỗi lượt vài event).
        with patch("harness.server.MAX_BUFFERED_EVENTS", 3):
            with TestClient(app) as client:
                run_id = client.post("/v1/runs",
                                     json={"agent": "t", "message": "go"}).json()["id"]
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    if client.get(f"/v1/runs/{run_id}").json()["status"] != "running":
                        break
                    time.sleep(0.02)
                text = client.get(f"/v1/runs/{run_id}/events").text
        self.assertIn("event: dropped", text,
                     "buffer tràn nhưng không có thông báo dropped nào cho subscriber")
        self.assertIn("dropped_events", text)

    def test_buffer_never_grows_past_the_bound(self):
        """`RunStore.start()` gọi `asyncio.ensure_future` — PHẢI chạy trong một event loop
        đang sống (như route handler thật đã làm), không phải gọi trực tiếp từ code đồng
        bộ: gọi ngoài loop khiến task không bao giờ thật sự chạy, `run.status` đứng yên
        mãi ở "running" — lỗi này tự nó gây treo, tìm ra khi viết test này lần đầu."""
        from unittest.mock import patch

        @tool(effect="read")
        def peek(x: int) -> str:
            """Nhìn."""
            return "ok"

        script = [FakeModel.tool_call("peek", {"x": i}, call_id=f"c{i}") for i in range(6)]
        script.append(FakeModel.text("xong"))
        agent = Agent(name="T", job="j", model="fake", tools=[peek], budget="$5",
                      provider=FakeModel(script))

        async def scenario():
            from harness.server import RunStore
            store = RunStore({"t": agent})
            run, _ = store.start("t", "go")
            deadline = asyncio.get_event_loop().time() + 5.0
            while run.status == "running" and asyncio.get_event_loop().time() < deadline:
                await asyncio.sleep(0.01)
            self.assertNotEqual(run.status, "running", "run không xong trong 5s")
            self.assertLessEqual(len(run.events), 3)
            self.assertGreater(run.dropped_events, 0)

        with patch("harness.server.MAX_BUFFERED_EVENTS", 3):
            asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
