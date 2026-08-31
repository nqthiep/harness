"""S-21: `Ledger._blocked` phải sống qua checkpoint + restore. S-22: `reserve()` phải định
giá theo mức giá TỆ NHẤT (`cache_write_per_mtok`), không phải `input_per_mtok` một mình.

Cả hai đúng như review-security.md mô tả, kiểm trên `src/harness/budget/ledger.py` hôm nay:

S-21 — `snapshot()` có `spent`/`steps`/`calibration`/`overshoot` nhưng KHÔNG có `blocked`.
Backend LangGraph dựng lại `Ledger` từ `snapshot()` ở MỖI node
(`lg/runtime.py::_ledger`) — một ledger đã `_blocked=True` (spend vượt trần cứng) restore
về `_blocked=False`, "quên" mất nó đã bị chặn.

S-22 — `size_call()`/`reserve()` chỉ dùng `price.input_per_mtok` + `price.output_per_mtok`
để ước lượng, trong khi `settle()` (COST-3) dùng BỐN mức giá, và `cache_write_per_mtok`
luôn đắt hơn `input_per_mtok` đúng 25% (`models/pricing.py::_p`, cố định — không phải số
đo được, tính được trực tiếp). Mọi cuộc gọi THẬT SỰ ghi cache bị ước lượng thấp hơn thực
tế đúng 25%, có hệ thống, không phải ngẫu nhiên.
"""
import sys, unittest
sys.path.insert(0, "src")

from decimal import Decimal

from harness.budget.ledger import Budget, Ledger
from harness.models.pricing import price

PRICE = price("claude-opus-5")


class BlockedSongQuaCheckpoint(unittest.TestCase):
    def test_blocked_con_sau_snapshot_restore(self):
        led = Ledger(Budget.parse("$0.01"))
        # Dồn spend vượt trần cứng — `settle()` set `_blocked=True`.
        max_tokens = led.size_call(1, PRICE, 8000)
        r = led.reserve(1, max_tokens, PRICE)
        from harness.result import Usage
        led.settle(r, Usage(input_tokens=1, output_tokens=8000), PRICE)   # overshoot lớn
        self.assertTrue(led._blocked, "cần dựng được tình huống ledger đã bị chặn trước")

        snap = led.snapshot()
        restored = Ledger(Budget.parse("$0.01")).restore(snap)
        self.assertTrue(restored._blocked,
                        "ledger phục hồi từ checkpoint quên mất nó đã bị chặn — S-21")

    def test_snapshot_mang_khoa_blocked(self):
        led = Ledger(Budget.parse("$5"))
        self.assertIn("blocked", led.snapshot(),
                      "snapshot() không mang khoá 'blocked' — không có gì để restore lại")


class MutationS21(unittest.TestCase):
    def test_khong_co_ban_va_thi_blocked_mat_qua_restore(self):
        led = Ledger(Budget.parse("$0.01"))
        max_tokens = led.size_call(1, PRICE, 8000)
        r = led.reserve(1, max_tokens, PRICE)
        from harness.result import Usage
        led.settle(r, Usage(input_tokens=1, output_tokens=8000), PRICE)
        self.assertTrue(led._blocked)

        # Mô phỏng CHÍNH XÁC snapshot() trước bản vá: không có khoá "blocked".
        old_snap = {"spent": str(led._spent.decimal), "steps": led._steps,
                   "calibration": str(led._calibration),
                   "overshoot": str(led._overshoot.decimal)}
        restored = Ledger(Budget.parse("$0.01")).restore(old_snap)
        self.assertFalse(restored._blocked,
                         "mutation phải cho blocked=False sau restore — nếu test này fail "
                         "nghĩa là bản vá không còn load-bearing")


class ReserveDinhGiaTheoMucTe(unittest.TestCase):
    def test_reserve_dat_hon_settle_that_khi_ghi_cache(self):
        """`reserve()` phải định giá ÍT NHẤT bằng mức `settle()` sẽ tính nếu lần gọi đó
        THẬT SỰ ghi cache — nếu không, ước lượng luôn thấp hơn chi tiêu thật một số cố
        định, không phải một sai số ngẫu nhiên bị `INPUT_MARGIN`/calibration hấp thụ."""
        led = Ledger(Budget.parse("$50"))
        max_tokens = led.size_call(10_000, PRICE, 8000)
        r = led.reserve(10_000, max_tokens, PRICE)

        from harness.result import Usage
        # Toàn bộ input được billed là cache WRITE — trường hợp tệ nhất settle() tính.
        actual = led.settle(r, Usage(input_tokens=0, output_tokens=0,
                                     cache_creation_input_tokens=10_000), PRICE)
        self.assertLessEqual(actual.decimal, r.estimate.decimal,
                             f"chi thật ({actual}) vượt ước lượng ({r.estimate}) — reserve() "
                             f"không định giá đủ cho trường hợp ghi cache, đúng lỗi S-22")

    def test_reserve_dung_dung_cache_write_per_mtok(self):
        """Đơn vị: `est` của `reserve()` phải khớp công thức dùng `cache_write_per_mtok`,
        không phải `input_per_mtok`."""
        led = Ledger(Budget.parse("$50"))
        r = led.reserve(10_000, 1000, PRICE)
        adjusted = led._adjusted(10_000)      # INPUT_MARGIN * calibration đã áp
        expected = (adjusted / Decimal(1_000_000) * PRICE.cache_write_per_mtok
                   + Decimal(1000) / Decimal(1_000_000) * PRICE.output_per_mtok)
        self.assertAlmostEqual(float(r.estimate.decimal), float(expected), places=6)


class MutationS22(unittest.TestCase):
    def test_khong_co_ban_va_thi_uoc_luong_thap_hon_ban_va(self):
        """So đúng CÙNG một công thức (cộng margin, cộng output) — chỉ đổi mức giá input,
        đúng thứ bản vá đổi. Mô phỏng CHÍNH XÁC công thức trước bản vá: `input_per_mtok`,
        không phải `cache_write_per_mtok`."""
        led = Ledger(Budget.parse("$50"))
        adjusted = led._adjusted(10_000)
        output_cost = Decimal(1000) / Decimal(1_000_000) * PRICE.output_per_mtok
        old_est = adjusted / Decimal(1_000_000) * PRICE.input_per_mtok + output_cost
        new_est = adjusted / Decimal(1_000_000) * PRICE.cache_write_per_mtok + output_cost
        self.assertLess(old_est, new_est,
                        "mutation phải cho ước lượng cũ THẤP HƠN ước lượng đã vá — nếu "
                        "test này fail nghĩa là phép so sánh không còn phản ánh lỗi cũ")
        r = led.reserve(10_000, 1000, PRICE)
        self.assertAlmostEqual(float(r.estimate.decimal), float(new_est), places=6,
                               msg="reserve() thật không khớp công thức đã vá")


if __name__ == "__main__":
    unittest.main()
