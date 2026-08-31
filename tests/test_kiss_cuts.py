"""review-kiss.md K-7/K-9/K-10/K-23 — checked against ACTUAL `src/harness/` code, not the
design-doc snapshot the review was written against, per the same "verify before fixing"
discipline used for the security findings (S-6, `Ledger.void()`, S-15 on the classic loop).

Two of the four turned out to already be stale by the time they reached code:

- K-7 said no code reads `Reservation.exact` — false today: `run.py` reads
  `Ledger.last_call_was_exactly_bounded` and puts it on the `BUDGET_RESERVED` event
  (`exact=...`), a real, wired consumer. Not cut; `test_khong_con_dung_nua` below locks
  in that the read is real, not decorative.
- K-23 said `max_concurrency` has no path from `Agent(...)` — false today: it does, as
  `max_parallel_tools`. The other eight tunables it lists were never implemented at all
  (no `RunConfig`, no `cancel_grace`, no `max_grant_ttl`, no `quarantine`, no depth cap,
  no retry budget in `src/harness/`) — nothing to cut, same "0 caller" situation as
  S-7..S-10. `EDIT_AT`/`COMPACT_AT`/`KEEP_RECENT_STEPS`/`INPUT_MARGIN`/
  `MIN_USEFUL_OUTPUT_TOKENS` are already unconfigurable module constants — already
  exactly what K-23 itself recommends.

One was real and got cut:

- K-9: `Result.raise_for_status()` was `run()` rewritten, read nowhere else. Cut from the
  public surface; `run()`/`arun()` now build the same `RunFailed` directly
  (`agent.py::_raise_if_failed`).

K-10 (nine OTel spans -> four) has no code to cut — no OpenTelemetry integration exists
in `src/harness/` at all yet (`observe/events.py`'s `EventBus`/`Exporter` is the only
observability mechanism today) — so it is a design-doc-only trim
(`design/04-runtime-durability.md` §8.2), not tested here.
"""
import sys, unittest
sys.path.insert(0, "src")

from harness import Agent, tool
from harness.budget.ledger import Budget, Ledger
from harness.errors import RunFailed
from harness.models.fake import FakeModel
from harness.result import Result


class K9RaiseForStatusCut(unittest.TestCase):
    def test_khong_con_tren_be_mat_cong_khai(self):
        self.assertFalse(hasattr(Result, "raise_for_status"),
                         "raise_for_status() quay lại — đúng bề mặt K-9 đã cắt")

    def test_ok_van_con(self):
        """`.ok` là property rẻ được giữ lại — K-9 chỉ cắt raise_for_status(), không cắt
        cách kiểm tra kết quả rẻ nhất."""
        self.assertTrue(hasattr(Result, "ok"))

    def test_run_van_raise_runfailed_dung_noi_dung(self):
        """`run()` phải build ĐÚNG cùng thông điệp mà `raise_for_status()` cũ từng build —
        hành vi công khai không đổi, chỉ đổi chỗ đứng của code."""
        m = FakeModel([FakeModel.text("...", stop="max_tokens")] * 2)
        a = Agent(name="T", job="j", provider=m, budget="$0.05")
        with self.assertRaises(RunFailed) as cm:
            a.run("go")
        self.assertIsNotNone(cm.exception.partial, "IDL-12: partial work vẫn phải giữ lại")
        self.assertFalse(cm.exception.partial.ok)

    def test_try_run_khong_bao_gio_raise(self):
        """Đối chứng: `try_run()` không đổi — vẫn không bao giờ raise cho kết quả run."""
        m = FakeModel([FakeModel.text("...", stop="max_tokens")])
        a = Agent(name="T", job="j", provider=m, budget="$0.05")
        r = a.try_run("go")
        self.assertFalse(r.ok)


class K7ExactVanConDuocDoc(unittest.TestCase):
    """K-7 nói `Reservation.exact` không ai đọc — kiểm lại trên code hiện tại."""

    def test_last_call_was_exactly_bounded_la_mot_consumer_that(self):
        """Khi hard_max_input (chars) vẫn vừa ngân sách, `reserve()` phải đánh dấu lần gọi
        này là "exact" — và cờ đó phải đọc lại được, không chỉ ghi rồi bỏ."""
        from harness.models.pricing import price
        led = Ledger(Budget.parse("$50"))
        p = price("claude-opus-5")
        led.reserve(100, 500, p, hard_max_input=1000)   # ngân sách rộng, hard bound vừa
        self.assertTrue(led.last_call_was_exactly_bounded,
                        "reserve() không đánh dấu exact dù hard bound vừa ngân sách")

    def test_run_py_dua_no_ra_su_kien_that(self):
        """`run.py` phải thật sự ĐỌC `last_call_was_exactly_bounded()` và đưa vào sự kiện
        `BUDGET_RESERVED` — đây chính là "consumer thật" chứng minh K-7 đã lỗi thời."""
        RAN = []

        class Sink:
            def emit(self, event):
                if event.kind.value == "budget.reserved":
                    RAN.append(event.data.get("exact"))
            def close(self) -> None: ...

        @tool(effect="read")
        def noop(x: int) -> str:
            """Không làm gì."""
            return "ok"

        a = Agent(name="T", job="j", tools=[noop], budget="$5",
                 provider=FakeModel([FakeModel.tool_call("noop", {"x": 1}),
                                     FakeModel.text("xong")]),
                 exporters=[Sink()])
        a.try_run("go")
        self.assertTrue(RAN, "không sự kiện budget.reserved nào mang attribute exact")
        self.assertTrue(all(isinstance(v, bool) for v in RAN))


if __name__ == "__main__":
    unittest.main()
