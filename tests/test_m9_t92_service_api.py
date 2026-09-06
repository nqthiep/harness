"""M9/T-9.2 (docs/17-research-alignment.md): Service API — `POST /v1/runs`,
`GET /v1/runs/{id}`, `GET /v1/runs/{id}/events` (SSE), `POST .../cancel`,
`POST .../approvals/{call_id}`.

`starlette.testclient.TestClient` MUST be used as a context manager
(`with TestClient(app) as client:`) in every test here — without it, each request spins
up its OWN event loop (`anyio.from_thread.start_blocking_portal`, torn down at the end of
that one request), and a run still `await`ing an approval `Future` gets cancelled the
instant the request that started it returns. That is a `TestClient` artifact, not a
production one — a real ASGI server (uvicorn) runs one loop for the process's whole
life — but it means every test below opens the client once for its whole scenario.
"""
import time
import unittest

from harness import Agent, tool
from harness.models.fake import FakeModel
from harness.server import create_app

from starlette.testclient import TestClient


def _app(agent, **kw):
    """G-3: `authenticate=` giờ bắt buộc. Mọi test dưới đây kiểm tra HÀNH VI của Service
    API, không phải cơ chế xác thực — dùng một callback cho qua tất cả, xem
    `KiemTraXacThuc` cho các test kiểm tra chính `authenticate=`."""
    return create_app(agent, authenticate=lambda request: True, **kw)


def _poll(client, run_id, *, until=("done", "error", "cancelled"), timeout=2.0):
    deadline = time.monotonic() + timeout
    body = None
    while time.monotonic() < deadline:
        body = client.get(f"/v1/runs/{run_id}").json()
        if body["status"] in until:
            return body
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {until}, last body: {body}")


@tool(effect="read")
def look(x: int) -> str:
    """Nhìn."""
    return "ok"


@tool(effect="danger")
def wipe(x: int) -> str:
    """Xoá."""
    return "gone"


def _agent(script, tools=(look,)):
    return Agent(name="A", job="j", model="claude-opus-5",
                provider=FakeModel(script), tools=list(tools), budget="$5")


class ChayMotRunDonGian(unittest.TestCase):
    def test_post_tra_ve_202_va_id(self):
        app = _app(_agent([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("xong")]))
        with TestClient(app) as client:
            r = client.post("/v1/runs", json={"message": "hi"})
            self.assertEqual(r.status_code, 202)
            self.assertTrue(r.json()["id"].startswith("run_"))

    def test_get_phan_anh_dung_ket_qua(self):
        app = _app(_agent([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("xong")]))
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"message": "hi"}).json()["id"]
            body = _poll(client, run_id)
            self.assertEqual(body["status"], "done")
            self.assertTrue(body["result"]["ok"])
            self.assertEqual(body["result"]["text"], "xong")
            self.assertEqual(body["result"]["tools_run"], ["look"])

    def test_thieu_message_tra_ve_400(self):
        app = _app(_agent([FakeModel.text("x")]))
        with TestClient(app) as client:
            r = client.post("/v1/runs", json={})
            self.assertEqual(r.status_code, 400)

    def test_run_khong_ton_tai_tra_ve_404(self):
        app = _app(_agent([FakeModel.text("x")]))
        with TestClient(app) as client:
            r = client.get("/v1/runs/run_khong_ton_tai")
            self.assertEqual(r.status_code, 404)


class IdempotencyKey(unittest.TestCase):
    """T-6.1's real caller — S-4/S-23's `call_key` idea, đúng lớp mà `execute_once` được
    xây để phục vụ (ADR-043)."""

    def test_cung_key_tra_ve_cung_run_id(self):
        model = FakeModel([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("xong")])
        agent = Agent(name="A", job="j", model="claude-opus-5", provider=model,
                      tools=[look], budget="$5")
        app = _app(agent)
        with TestClient(app) as client:
            r1 = client.post("/v1/runs", json={"message": "hi"},
                             headers={"Idempotency-Key": "k1"})
            r2 = client.post("/v1/runs", json={"message": "hi"},
                             headers={"Idempotency-Key": "k1"})
            self.assertEqual(r1.json()["id"], r2.json()["id"])
            self.assertFalse(r1.json()["replayed"])
            self.assertTrue(r2.json()["replayed"])

    def test_cung_key_chi_chay_model_dung_mot_lan(self):
        """Xác nhận replay không PHẢI chỉ trả về đúng id mà còn thật sự không chạy lại —
        khoá bằng số lần `FakeModel.calls` được ghi, không chỉ bằng response JSON."""
        model = FakeModel([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("xong")])
        agent = Agent(name="A", job="j", model="claude-opus-5", provider=model,
                      tools=[look], budget="$5")
        app = _app(agent)
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"message": "hi"},
                                 headers={"Idempotency-Key": "k1"}).json()["id"]
            _poll(client, run_id)
            n_calls_after_first = len(model.calls)
            client.post("/v1/runs", json={"message": "hi"}, headers={"Idempotency-Key": "k1"})
            time.sleep(0.05)
            self.assertEqual(len(model.calls), n_calls_after_first,
                             "một lần replay không được gọi model thêm lần nào")

    def test_khac_key_la_hai_run_khac_nhau(self):
        app = _app(_agent([FakeModel.text("a"), FakeModel.text("b")], tools=()))
        with TestClient(app) as client:
            r1 = client.post("/v1/runs", json={"message": "hi"},
                             headers={"Idempotency-Key": "k1"})
            r2 = client.post("/v1/runs", json={"message": "hi"},
                             headers={"Idempotency-Key": "k2"})
            self.assertNotEqual(r1.json()["id"], r2.json()["id"])

    def test_khong_co_key_moi_lan_la_mot_run_moi(self):
        app = _app(_agent([FakeModel.text("a"), FakeModel.text("b")], tools=()))
        with TestClient(app) as client:
            r1 = client.post("/v1/runs", json={"message": "hi"})
            r2 = client.post("/v1/runs", json={"message": "hi"})
            self.assertNotEqual(r1.json()["id"], r2.json()["id"])

    def test_mutation_bo_qua_execute_once_luon_tao_run_moi(self):
        """Mutation: mô phỏng `start()` không gọi `execute_once` — mọi request đều spawn
        một run mới bất kể key. Đúng cái S-4/S-23 tồn tại để ngăn: client timeout rồi
        retry với CÙNG idempotency key sẽ chạy tool `write`/`danger` HAI LẦN."""
        model = FakeModel([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("a"),
                           FakeModel.tool_call("look", {"x": 1}), FakeModel.text("b")])
        agent = Agent(name="A", job="j", model="claude-opus-5", provider=model,
                      tools=[look], budget="$5")

        from harness.server import _RunRegistry

        class BuggyRegistry(_RunRegistry):
            async def start(self, message, *, idempotency_key):
                return self._spawn(message), False        # bỏ qua execute_once hoàn toàn

        import harness.server as server_mod
        old = server_mod._RunRegistry
        server_mod._RunRegistry = BuggyRegistry
        try:
            app = _app(agent)
        finally:
            server_mod._RunRegistry = old
        with TestClient(app) as client:
            r1 = client.post("/v1/runs", json={"message": "hi"},
                             headers={"Idempotency-Key": "k1"})
            r2 = client.post("/v1/runs", json={"message": "hi"},
                             headers={"Idempotency-Key": "k1"})
            self.assertNotEqual(r1.json()["id"], r2.json()["id"],
                                "mutation (bỏ execute_once) phải tạo HAI run cho CÙNG "
                                "một key — nếu vẫn ra cùng id, test này không còn phân "
                                "biệt được bản đúng và bản có lỗi")


class LuongDuyet(unittest.TestCase):
    def test_tool_danger_dung_o_waiting_approval(self):
        app = _app(_agent([FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("xong")],
                                tools=(wipe,)))
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"message": "hi"}).json()["id"]
            body = _poll(client, run_id, until=("waiting_approval",))
            self.assertEqual(body["status"], "waiting_approval")
            self.assertEqual(len(body["pending_approvals"]), 1)
            pending = body["pending_approvals"][0]
            self.assertEqual(pending["tool"], "wipe")
            self.assertEqual(pending["arguments"], {"x": 1})

    def test_duyet_true_chay_tiep_va_hoan_thanh(self):
        app = _app(_agent([FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("xong")],
                                tools=(wipe,)))
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"message": "hi"}).json()["id"]
            body = _poll(client, run_id, until=("waiting_approval",))
            call_id = body["pending_approvals"][0]["call_id"]
            r = client.post(f"/v1/runs/{run_id}/approvals/{call_id}",
                            json={"approve": True, "approved_by": "nqthiep"})
            self.assertEqual(r.status_code, 200)
            final = _poll(client, run_id)
            self.assertEqual(final["status"], "done")
            self.assertEqual(final["result"]["tools_run"], ["wipe"])

    def test_duyet_false_tu_choi_khong_chay_tool(self):
        app = _app(_agent([FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("thoi")],
                                tools=(wipe,)))
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"message": "hi"}).json()["id"]
            body = _poll(client, run_id, until=("waiting_approval",))
            call_id = body["pending_approvals"][0]["call_id"]
            client.post(f"/v1/runs/{run_id}/approvals/{call_id}", json={"approve": False})
            final = _poll(client, run_id)
            self.assertEqual(final["result"]["tools_run"], [],
                             "từ chối duyệt không được để tool chạy")

    def test_duyet_call_id_khong_ton_tai_tra_ve_404(self):
        app = _app(_agent([FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("xong")],
                                tools=(wipe,)))
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"message": "hi"}).json()["id"]
            _poll(client, run_id, until=("waiting_approval",))
            r = client.post(f"/v1/runs/{run_id}/approvals/no-such-call", json={"approve": True})
            self.assertEqual(r.status_code, 404)

    def test_mutation_khong_cho_qua_future_tu_duyet_luon(self):
        """Mutation: `approve=` trả `True` ngay lập tức, không tạo `Future` chờ HTTP —
        tool `danger` chạy mà KHÔNG CẦN `POST .../approvals/...` nào cả, đúng lỗ hổng
        toàn phiên này gọi là "model/run tự cầm công tắc phê duyệt của chính nó"."""
        async def auto_approve(call, ctx):
            return True

        model = FakeModel([FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("xong")])
        agent = Agent(name="A", job="j", model="claude-opus-5", provider=model,
                      tools=[wipe], budget="$5", approve=auto_approve)
        r = agent.try_run("hi")
        self.assertEqual(r.tools_run, ("wipe",),
                         "mutation (auto-approve) phải để tool chạy KHÔNG CẦN duyệt qua "
                         "HTTP — nếu nó không chạy, test này không còn phân biệt được "
                         "hành vi đúng (chờ POST approvals) và hành vi có lỗi")


class Huy(unittest.TestCase):
    def test_huy_khi_dang_cho_duyet(self):
        app = _app(_agent([FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("xong")],
                                tools=(wipe,)))
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"message": "hi"}).json()["id"]
            _poll(client, run_id, until=("waiting_approval",))
            r = client.post(f"/v1/runs/{run_id}/cancel")
            self.assertEqual(r.status_code, 200)
            final = _poll(client, run_id)
            self.assertEqual(final["status"], "cancelled")

    def test_huy_run_da_xong_tra_ve_409(self):
        app = _app(_agent([FakeModel.text("x")], tools=()))
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"message": "hi"}).json()["id"]
            _poll(client, run_id)
            r = client.post(f"/v1/runs/{run_id}/cancel")
            self.assertEqual(r.status_code, 409)

    def test_huy_run_khong_ton_tai_tra_ve_404(self):
        app = _app(_agent([FakeModel.text("x")]))
        with TestClient(app) as client:
            r = client.post("/v1/runs/khong-ton-tai/cancel")
            self.assertEqual(r.status_code, 404)


class SuKienSSE(unittest.TestCase):
    def test_backlog_day_du_thu_tu(self):
        app = _app(_agent([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("xong")]))
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"message": "hi"}).json()["id"]
            _poll(client, run_id)
            with client.stream("GET", f"/v1/runs/{run_id}/events") as resp:
                lines = [line[len("data: "):] for line in resp.iter_lines()
                        if line.startswith("data: ")]
            import json as _json
            kinds = [_json.loads(line)["kind"] for line in lines]
            self.assertEqual(kinds[0], "run.started")
            self.assertEqual(kinds[-1], "run.finished")
            self.assertIn("tool.requested", kinds)

    def test_events_route_run_khong_ton_tai_404(self):
        app = _app(_agent([FakeModel.text("x")]))
        with TestClient(app) as client:
            r = client.get("/v1/runs/khong-ton-tai/events")
            self.assertEqual(r.status_code, 404)


class ChongLoSecretQuaSSE(unittest.TestCase):
    """RT-13: `redact()` phải chạy TRÊN TASK CỦA CHÍNH RUN — `_event_json` gọi nó bên
    trong `_SseExporter.emit()`, đúng chỗ `EventBus.emit()` gọi đồng bộ, cùng task với
    `Secret.reveal()`.

    Vector thật: KHÔNG phải nội dung trả về của tool — `tool.finished` không hề mang
    payload (chỉ `duration_ms`/`is_error`/`truncated`; nội dung trả về chỉ vào MESSAGE
    gửi lại model, không vào Event nào). Vector là `error.raised`'s `message`:
    `dispatch.py::_tool_error` emit `message=msg` CHƯA qua `redact()` ở điểm emit (chỉ
    giá trị trả cho MODEL, qua `err()`, mới gọi `redact()`) — một tool `.reveal()` một
    `Secret` rồi RAISE với giá trị đó trong thông điệp lỗi là đúng khe hở `_event_json`
    phải tự đóng, độc lập với `dispatch.py`.
    """

    @tool(effect="read")
    def whoami() -> str:
        """Tiết lộ secret rồi raise — mô phỏng lỗi ký request thật (test_attack_s3.py)."""
        from harness.secrets import Secret
        s = Secret("sk-live-topsecret", name="api_key")
        with s.reveal() as v:
            raise ValueError(f"failed while using key {v}")

    def test_secret_khong_xuat_hien_nguyen_van_trong_event(self):
        app = _app(_agent([FakeModel.tool_call("whoami", {}), FakeModel.text("xong")],
                                tools=(ChongLoSecretQuaSSE.whoami,)))
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"message": "hi"}).json()["id"]
            _poll(client, run_id)
            with client.stream("GET", f"/v1/runs/{run_id}/events") as resp:
                lines = [line for line in resp.iter_lines() if line.startswith("data: ")]
            joined = "\n".join(lines)
            self.assertNotIn("sk-live-topsecret", joined)
            self.assertIn("error.raised", joined)
            self.assertIn("hidden", joined)

    def test_mutation_bo_redact_trong_event_json_lam_lo_secret(self):
        """Mutation: `_event_json` bỏ `redact()` — xác nhận nếu thiếu, secret RÒ RA
        nguyên văn (chứng minh `redact()` đang thật sự load-bearing, không phải trang
        trí thừa)."""
        import json as _json

        from harness.observe.events import Event, EventKind

        def buggy_event_json(event: Event) -> str:
            return _json.dumps(
                {"seq": event.seq, "ts": event.ts, "run_id": event.run_id,
                 "kind": event.kind.value, "step": event.step, "data": dict(event.data)},
                default=str)

        from harness.secrets import Secret, redaction_scope
        with redaction_scope():
            s = Secret("sk-live-topsecret", name="api_key")
            with s.reveal() as v:
                payload = {"content": f"key is {v}"}
            ev = Event(seq=1, ts=0.0, run_id="r1", kind=EventKind.TOOL_FINISHED,
                      step=1, data=payload)
            leaked = buggy_event_json(ev)
        self.assertIn("sk-live-topsecret", leaked,
                      "mutation (bỏ redact) phải để lộ secret nguyên văn — nếu nó cũng "
                      "không lộ, test này không còn phân biệt được bản đúng và bản có lỗi")


class KiemTraXacThuc(unittest.TestCase):
    """G-3, đã sửa: `authenticate=` giờ bắt buộc, không có mặc định. Trước bản vá,
    `create_app(agent)` dựng được một app hoàn toàn mở — ai gọi `POST .../approvals/...`
    cũng duyệt được tool `danger`."""

    def test_authenticate_la_tham_so_bat_buoc(self):
        import inspect
        from harness.server import create_app
        sig = inspect.signature(create_app)
        self.assertIn("authenticate", sig.parameters)
        self.assertIs(sig.parameters["authenticate"].default, inspect.Parameter.empty,
                     "authenticate= có mặc định — quay lại đúng lỗ hổng G-3")

    def test_authenticate_tra_ve_false_thi_401_moi_route(self):
        from harness.server import create_app
        app = create_app(_agent([FakeModel.tool_call("wipe", {"x": 1}),
                                 FakeModel.text("xong")], tools=(wipe,)),
                         authenticate=lambda request: False)
        with TestClient(app) as client:
            self.assertEqual(client.post("/v1/runs", json={"message": "hi"}).status_code, 401)
            self.assertEqual(client.get("/v1/runs/khong-quan-trong").status_code, 401)
            self.assertEqual(client.post("/v1/runs/khong-quan-trong/cancel").status_code,
                             401)
            self.assertEqual(
                client.post("/v1/runs/x/approvals/y", json={"approve": True}).status_code,
                401)

    def test_authenticate_tra_ve_true_thi_di_qua_binh_thuong(self):
        app = _app(_agent([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("xong")]))
        with TestClient(app) as client:
            r = client.post("/v1/runs", json={"message": "hi"})
            self.assertEqual(r.status_code, 202)

    def test_authenticate_bat_dong_bo_cung_duoc_ho_tro(self):
        """`authenticate` có thể là async — vd. tra cứu một session store thật."""
        from harness.server import create_app

        async def check(request) -> bool:
            return request.headers.get("x-api-key") == "dung-key"

        app = create_app(_agent([FakeModel.text("xong")], tools=()), authenticate=check)
        with TestClient(app) as client:
            self.assertEqual(client.post("/v1/runs", json={"message": "hi"}).status_code,
                             401)
            r = client.post("/v1/runs", json={"message": "hi"},
                            headers={"x-api-key": "dung-key"})
            self.assertEqual(r.status_code, 202)


class DuyetThieuBangChungBiTuChoiNgay(unittest.TestCase):
    """G-11, đã sửa: trước bản vá, một approval bị `PolicyEngine` DENY vì thiếu
    `AuthEvidence` vẫn nhận `{"resolved": true, "approve": true}` — người duyệt và
    `Result` cuối cùng không ai thấy được sự từ chối đó."""

    def test_thieu_evidence_khi_agent_doi_hoi_thi_400_ngay_khong_phai_200(self):
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("wipe", {"x": 1}),
                                         FakeModel.text("xong")]),
                      tools=[wipe], budget="$5", require_approval_evidence=True)
        app = _app(agent)
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"message": "hi"}).json()["id"]
            body = _poll(client, run_id, until=("waiting_approval",))
            call_id = body["pending_approvals"][0]["call_id"]
            r = client.post(f"/v1/runs/{run_id}/approvals/{call_id}",
                            json={"approve": True, "approved_by": "ai-do"})   # không evidence
            self.assertEqual(r.status_code, 400)
            self.assertIn("AuthEvidence", r.json()["error"])
            # 400 KHÔNG tiêu thụ approval đang chờ — future chưa bị resolve, nên run vẫn
            # đứng ở waiting_approval, còn cơ hội gửi lại kèm evidence đúng. Coi 400 như
            # đã "duyệt-thất-bại-âm-thầm" (resolve future, cho DENY âm thầm) mới là hành
            # vi cũ của G-11: người gọi mất luôn cơ hội sửa.
            still = client.get(f"/v1/runs/{run_id}").json()
            self.assertEqual(still["status"], "waiting_approval",
                             "400 không được tiêu thụ approval đang chờ")
            self.assertEqual(still["pending_approvals"][0]["call_id"], call_id)

    def test_co_evidence_hop_le_thi_van_chay_binh_thuong(self):
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("wipe", {"x": 1}),
                                         FakeModel.text("xong")]),
                      tools=[wipe], budget="$5", require_approval_evidence=True)
        app = _app(agent)
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"message": "hi"}).json()["id"]
            body = _poll(client, run_id, until=("waiting_approval",))
            call_id = body["pending_approvals"][0]["call_id"]
            r = client.post(f"/v1/runs/{run_id}/approvals/{call_id}",
                            json={"approve": True, "approved_by": "ai-do",
                                 "evidence": {"channel": "slack",
                                            "channel_message_id": "m1",
                                            "principal": "U123"}})
            self.assertEqual(r.status_code, 200)
            final = _poll(client, run_id)
            self.assertEqual(final["result"]["tools_run"], ["wipe"])

    def test_tu_choi_khong_can_evidence_du_agent_doi_hoi(self):
        """Từ chối (`approve: false`) không phải hành động cần xác thực danh tính —
        `require_approval_evidence` chỉ áp cho lượt CHO PHÉP."""
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=FakeModel([FakeModel.tool_call("wipe", {"x": 1}),
                                         FakeModel.text("thoi")]),
                      tools=[wipe], budget="$5", require_approval_evidence=True)
        app = _app(agent)
        with TestClient(app) as client:
            run_id = client.post("/v1/runs", json={"message": "hi"}).json()["id"]
            body = _poll(client, run_id, until=("waiting_approval",))
            call_id = body["pending_approvals"][0]["call_id"]
            r = client.post(f"/v1/runs/{run_id}/approvals/{call_id}", json={"approve": False})
            self.assertEqual(r.status_code, 200)


if __name__ == "__main__":
    unittest.main()
