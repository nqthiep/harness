"""N-5 (design/07-risks-and-open-issues.md): retry cấp PROVIDER — `docs/10 §3`'s lời
hứa "Yes, honoring Retry-After" / "Yes, exponential backoff" cuối cùng có code.

Bốn phần tương ứng bốn quyết định thiết kế thật trong ADR-070:

1. `retry_wait()` — luật thuần, không cần dựng Agent nào để kiểm.
2. `ProviderRateLimited.retry_after` + `models/anthropic.py::_map` đọc header thật.
3. `Ledger.release_reservation` — lỗ rò rỉ mà việc THÊM retry vào một Ledger DÙNG LẠI
   qua nhiều lần thử sẽ lộ ra nếu không sửa (trước đây vô hại vì run kết thúc ngay sau
   một lỗi provider, nên reservation treo không bao giờ được đọc lại).
4. Vòng lặp classic thật sự retry, bị chặn bởi wall-clock chứ không phải một số đếm.

**Phạm vi, nói thẳng**: chỉ vòng lặp classic. Backend LangGraph nhận một model LangChain
tuỳ ý qua `build_agent(model=...)` và gọi `.invoke()` thẳng — không đi qua
`AnthropicProvider._map()` nên không có gì để nhận diện `ProviderRateLimited` v.v. Xem
`provider_retry.py`'s docstring.
"""
import sys
import unittest
from decimal import Decimal

sys.path.insert(0, "src")
sys.path.insert(0, "tests")

from harness import Agent, tool
from harness.budget.ledger import Ledger, Budget
from harness.errors import (ProviderAuthError, ProviderBadRequest, ProviderError,
                            ProviderRateLimited, ProviderTimeout, ProviderUnavailable)
from harness.models import pricing
from harness.observe.events import EventKind
from harness.provider_retry import retry_wait
from harness.result import StopReason
from harness.testing.chaos import TimeoutProvider


class LuatThuanRetryWait(unittest.TestCase):
    def test_rate_limited_co_retry_after_thi_dung_dung_gia_tri_do(self):
        exc = ProviderRateLimited("429", retry_after=12.5)
        self.assertEqual(retry_wait(exc, 0), 12.5)

    def test_retry_after_bang_khong_van_co_san_mot_khoang_cho_toi_thieu(self):
        """`Retry-After: 0` (hay lệch đồng hồ ra số âm) không được sụp thành vòng lặp
        dồn dập tấn công đúng server vừa bảo chậm lại."""
        exc = ProviderRateLimited("429", retry_after=0.0)
        from harness.dispatch import RETRY_BACKOFF_S
        self.assertGreaterEqual(retry_wait(exc, 0), RETRY_BACKOFF_S)

    def test_rate_limited_khong_co_retry_after_thi_lui_theo_ham_mu(self):
        exc = ProviderRateLimited("429", retry_after=None)
        from harness.dispatch import RETRY_BACKOFF_MAX_S, RETRY_BACKOFF_S
        self.assertEqual(retry_wait(exc, 0), RETRY_BACKOFF_S)
        self.assertLessEqual(retry_wait(exc, 10), RETRY_BACKOFF_MAX_S)

    def test_unavailable_va_timeout_lui_theo_ham_mu_tang_dan(self):
        from harness.dispatch import RETRY_BACKOFF_MAX_S
        w0 = retry_wait(ProviderUnavailable("500"), 0)
        w1 = retry_wait(ProviderUnavailable("500"), 1)
        self.assertLess(w0, w1)
        self.assertLessEqual(retry_wait(ProviderTimeout("timeout"), 20), RETRY_BACKOFF_MAX_S)

    def test_timeout_error_thuan_cung_duoc_coi_la_tam_thoi(self):
        """`harness.testing.chaos.TimeoutProvider` raise `TimeoutError` gốc, không phải
        `harness.errors.ProviderTimeout` — cùng một seam mà một provider thật cũng có
        thể raise trực tiếp."""
        self.assertIsNotNone(retry_wait(TimeoutError("chaos"), 0))

    def test_loi_khong_tam_thoi_thi_khong_bao_gio_retry(self):
        for exc in (ProviderAuthError("401"), ProviderBadRequest("400"),
                   ProviderError("lỗi lạ"), ValueError("không liên quan")):
            self.assertIsNone(retry_wait(exc, 0), f"{type(exc).__name__} không nên retry")


class DocRetryAfterTuSDKThat(unittest.TestCase):
    def test_map_429_doc_retry_after_tu_header(self):
        from harness.models.anthropic import AnthropicProvider

        class FakeResponse:
            headers = {"retry-after": "7"}

        class FakeExc(Exception):
            status_code = 429
            response = FakeResponse()

        p = AnthropicProvider.__new__(AnthropicProvider)
        mapped = p._map(FakeExc("429 rồi"))
        self.assertIsInstance(mapped, ProviderRateLimited)
        self.assertEqual(mapped.retry_after, 7.0)

    def test_khong_co_header_thi_retry_after_la_None_khong_phai_doan(self):
        from harness.models.anthropic import AnthropicProvider

        class FakeResponse:
            headers = {}

        class FakeExc(Exception):
            status_code = 429
            response = FakeResponse()

        p = AnthropicProvider.__new__(AnthropicProvider)
        mapped = p._map(FakeExc("429"))
        self.assertIsInstance(mapped, ProviderRateLimited)
        self.assertIsNone(mapped.retry_after)

    def test_header_khong_phai_so_thi_khong_crash_tra_ve_None(self):
        """HTTP cho phép Retry-After là một ngày tháng thay vì số giây — adapter này
        không parse ngày, và không được vì thế mà nổ."""
        from harness.models.anthropic import AnthropicProvider

        class FakeResponse:
            headers = {"retry-after": "Wed, 21 Oct 2099 07:28:00 GMT"}

        class FakeExc(Exception):
            status_code = 429
            response = FakeResponse()

        p = AnthropicProvider.__new__(AnthropicProvider)
        mapped = p._map(FakeExc("429"))
        self.assertIsNone(mapped.retry_after)


class ReservationKhongBiRoRi(unittest.TestCase):
    """Lỗ rò rỉ mà việc thêm retry sẽ lộ ra nếu không sửa: trước N-5, một reservation
    của một lời gọi hỏng nằm mãi trong `Ledger._open`, không sao vì run kết thúc ngay
    sau đó. Retry dùng LẠI cùng một `Ledger` qua nhiều lần thử, nên lỗ đó phải vá."""

    def setUp(self):
        self.ledger = Ledger(Budget(usd=Decimal("1.00"), steps=100, wall_clock_s=60))
        self.price = pricing.price("claude-opus-5")

    def test_release_khong_lam_mat_tien_da_tieu(self):
        r = self.ledger.reserve(100, 100, self.price)
        before = self.ledger.remaining_usd()
        self.ledger.release_reservation(r)
        after = self.ledger.remaining_usd()
        self.assertEqual(self.ledger.spent.decimal, Decimal("0"))
        self.assertGreater(after.decimal, before.decimal, "release không trả lại chỗ đã giữ")

    def test_khong_release_thi_uoc_tinh_treo_mai_trong_committed(self):
        """Đối chứng: xác nhận đây THẬT SỰ là một lỗ, không phải test tưởng tượng."""
        self.ledger.reserve(100, 100, self.price)
        before = self.ledger.remaining_usd()
        # Không release — mô phỏng hành vi TRƯỚC N-5.
        after = self.ledger.remaining_usd()
        self.assertEqual(before, after, "reservation không release vẫn chiếm chỗ mãi")

    def test_nhieu_lan_release_lien_tiep_khong_lam_lech_ngan_sach(self):
        """Đúng hình dạng một chuỗi retry thật: reserve → hỏng → release → reserve lại."""
        start = self.ledger.remaining_usd()
        for _ in range(5):
            r = self.ledger.reserve(100, 100, self.price)
            self.ledger.release_reservation(r)
        self.assertEqual(self.ledger.remaining_usd(), start,
                         "năm lần reserve/release liên tiếp làm lệch ngân sách còn lại")


class VongLapClassicRetryThat(unittest.TestCase):
    def _noop(self):
        @tool(effect="read")
        def look() -> str:
            """Nhìn."""
            return "ok"
        return look

    def test_hong_thoang_qua_thi_tu_hoi_phuc(self):
        """Đúng điều `docs/10 §3` hứa: retry, không chỉ bắt lỗi rồi dừng."""
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=TimeoutProvider(fail_calls=(0,)), budget="$5, 20 steps, 5s")
        r = agent.try_run("thử")
        self.assertTrue(r.ok, r.detail)
        self.assertIs(r.stop_reason, StopReason.COMPLETED)

    def test_hong_lien_tuc_thi_dung_lai_khi_het_wall_clock_khong_phai_mot_so_dem(self):
        """'Retry budget is bounded by the run's wall clock, never by an independent
        retry count' — docs/10 §3, nguyên văn."""
        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=TimeoutProvider(fail_calls=range(10_000)),
                      budget="$5, 100 steps, 0.3s")
        r = agent.try_run("thử")
        self.assertFalse(r.ok)
        self.assertIn(r.stop_reason, (StopReason.ERROR, StopReason.TIMEOUT))

    def test_su_kien_error_raised_danh_dau_retryable_dung_qua_tung_lan_thu(self):
        seen = []

        class Collector:
            def emit(self, e):
                if e.kind is EventKind.ERROR_RAISED:
                    seen.append(dict(e.data))
            def close(self): ...

        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=TimeoutProvider(fail_calls=(0, 1)), budget="$5, 20 steps, 5s",
                      exporters=[Collector()])
        r = agent.try_run("thử")
        self.assertTrue(r.ok, r.detail)
        provider_errors = [e for e in seen if e.get("where") == "provider"]
        self.assertEqual(len(provider_errors), 2)
        self.assertTrue(all(e["retryable"] for e in provider_errors))
        self.assertEqual([e["attempt"] for e in provider_errors], [0, 1])

    def test_moi_lan_thu_lai_la_mot_reservation_moi_khong_phai_dung_chung(self):
        """I-1: không lời gọi model nào thiếu một reservation NGAY TRƯỚC nó — một lần
        thử lại là một lời gọi model mới, nên cũng cần reservation của riêng nó."""
        reserved_steps = []

        class Collector:
            def emit(self, e):
                if e.kind is EventKind.BUDGET_RESERVED:
                    reserved_steps.append(e.data.get("estimate_usd"))
            def close(self): ...

        agent = Agent(name="A", job="j", model="claude-opus-5",
                      provider=TimeoutProvider(fail_calls=(0,)), budget="$5, 20 steps, 5s",
                      exporters=[Collector()])
        agent.try_run("thử")
        self.assertEqual(len(reserved_steps), 2, "phải có đúng một reservation mỗi lần thử")

    def test_loi_khong_tam_thoi_thi_khong_retry_dung_lai_ngay(self):
        """`ProviderAuthError`/`ProviderBadRequest` không bao giờ retry — chờ không sửa
        được một API key sai."""
        class BadKeyProvider:
            def __init__(self):
                self.calls = 0

            async def complete(self, request, *, on_delta=None):
                self.calls += 1
                from harness.errors import ProviderAuthError
                raise ProviderAuthError("401: sai key")

            def price(self, model): return pricing.price(model)
            async def count_input_tokens(self, request): return 10
            def max_output(self, model): return 1000

        provider = BadKeyProvider()
        agent = Agent(name="A", job="j", model="claude-opus-5", provider=provider,
                      budget="$5, 20 steps, 5s")
        r = agent.try_run("thử")
        self.assertFalse(r.ok)
        self.assertEqual(provider.calls, 1, "một lỗi không tạm thời mà vẫn bị retry")


if __name__ == "__main__":
    unittest.main()
