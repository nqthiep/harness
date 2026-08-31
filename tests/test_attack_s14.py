"""S-14: `reserve()` phải chặn được lời gọi thứ HAI chồng lên lời gọi thứ nhất chưa
`settle()` — không chỉ chặn từng lời gọi riêng lẻ đọc `remaining_usd()` một mình.

design/review-security.md S-14, Nhánh A: `self._open` được ghi vào bởi `reserve()` và đọc
ra để pop bởi `settle()`, nhưng trước bản vá này KHÔNG BAO GIỜ được cộng vào bất kỳ phép
tính ngân sách nào. Ba `reserve()` gọi liên tiếp trước khi cái đầu tiên `settle()` — kịch
bản mà một `Retry` plugin tương lai sẽ tạo ra — mỗi lần đều đọc `remaining_usd()` chưa bị
trừ bởi hai cái kia, nên cả ba đều "vừa ngân sách" một cách độc lập, và tổng thực chi có
thể vượt trần gấp nhiều lần.

Không cần dựng agent hay graph — đây là lỗi ở đúng MỘT lớp (`Ledger`), kiểm trực tiếp.
"""
import sys, unittest
sys.path.insert(0, "src")

from harness.budget.ledger import Budget, Ledger
from harness.errors import BudgetExceeded
from harness.models.pricing import price
from harness.result import Usage

PRICE = price("claude-opus-5")


class ReservationChongNhau(unittest.TestCase):
    def test_reserve_thu_hai_bi_chan_boi_reserve_thu_nhat_chua_settle(self):
        """Ngân sách chỉ đủ cho MỘT lời gọi cỡ này. Gọi `reserve()` hai lần liên tiếp mà
        không `settle()` lần nào — lần hai phải bị từ chối, vì lần một vẫn đang "mở"."""
        led = Ledger(Budget.parse("$0.05"))
        max_tokens = led.size_call(1000, PRICE, 8000)

        r1 = led.reserve(1000, max_tokens, PRICE)          # mở, chưa settle
        self.assertIsNotNone(r1)

        with self.assertRaises(BudgetExceeded,
                               msg="reserve thứ hai lọt qua dù reserve thứ nhất "
                                   "vẫn đang giữ gần hết ngân sách"):
            led.reserve(1000, max_tokens, PRICE)

    def test_settle_giai_phong_cho_reserve_tiep_theo(self):
        """Đối chứng: sau khi `settle()`, reservation không còn "mở" nữa — lời gọi kế
        tiếp phải dùng lại được đúng phần ngân sách đó (giả sử chi phí thật ~ ước tính)."""
        led = Ledger(Budget.parse("$0.50"))
        max_tokens = led.size_call(1000, PRICE, 8000)

        r1 = led.reserve(1000, max_tokens, PRICE)
        led.settle(r1, Usage(input_tokens=1000, output_tokens=50), PRICE)

        r2 = led.reserve(1000, max_tokens, PRICE)          # phải KHÔNG raise
        self.assertIsNotNone(r2)

    def test_remaining_usd_tru_ca_reservation_dang_mo(self):
        """`remaining_usd()` phải phản ánh đúng: đã cam kết bao nhiêu, không chỉ đã tiêu
        bao nhiêu."""
        led = Ledger(Budget.parse("$1.00"))
        before = led.remaining_usd()
        r1 = led.reserve(1000, 500, PRICE)
        after = led.remaining_usd()
        self.assertLess(after.decimal, before.decimal,
                        "remaining_usd không giảm khi có reservation đang mở")
        self.assertAlmostEqual(
            (before.decimal - after.decimal), r1.estimate.decimal,
            places=8, msg="phần giảm phải đúng bằng estimate của reservation")

    def test_hai_reservation_nho_cong_don_dung(self):
        """Hai reservation nhỏ, tổng vượt ngân sách dù mỗi cái riêng lẻ vừa đủ — lần thứ
        hai phải bị chặn bởi TỔNG, không phải so với ngân sách gốc."""
        led = Ledger(Budget.parse("$0.05"))
        max_tokens = led.size_call(1000, PRICE, 8000)
        half_tokens = max(256, max_tokens // 3)   # đủ nhỏ để lần đầu qua, lần hai thì không

        led.reserve(1000, half_tokens, PRICE)
        with self.assertRaises(BudgetExceeded):
            led.reserve(1000, half_tokens, PRICE)
            led.reserve(1000, half_tokens, PRICE)


class MutationXacNhanLoadBearing(unittest.TestCase):
    """Khôi phục hành vi CŨ (remaining_usd chỉ trừ spent, không trừ open) và xác nhận
    scenario chính đúng là đỏ — chứng minh bản vá không phải trang trí."""

    def test_khong_co_ban_va_thi_reserve_thu_hai_lot_qua(self):
        led = Ledger(Budget.parse("$0.05"))
        max_tokens = led.size_call(1000, PRICE, 8000)
        led.reserve(1000, max_tokens, PRICE)

        # Mô phỏng CHÍNH XÁC hành vi trước bản vá: bỏ qua self._open hoàn toàn.
        old_committed = led._committed
        led._committed = lambda: led._spent           # MUTATION tại chỗ, không sửa file

        try:
            r2 = led.reserve(1000, max_tokens, PRICE)   # đúng như dự đoán: KHÔNG raise
            self.assertIsNotNone(r2, "mutation phải cho lọt — nếu test này fail nghĩa là "
                                     "bản vá không còn load-bearing")
        finally:
            led._committed = old_committed


if __name__ == "__main__":
    unittest.main()
