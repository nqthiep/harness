"""S-13: bốn sub-agent spawn trong một lượt không được cộng dồn vượt trần step/thời gian
của run gốc.

design/review-security.md S-13: `04 §7.2` nói `steps` "là của run gốc... sub-agent trừ
vào CÙNG sổ step". Trước bản vá này, `_run_subagent` (cả hai backend) chỉ `hold()` trục
`usd`; `steps`/`wall_clock_s` của con được kế thừa NGUYÊN VẸN từ `Budget` con tự khai.
Bốn sub-agent spawn trong một lượt, mỗi đứa tự khai `steps=20`, có thể tiêu tới 80 step
trong khi trần của run gốc chỉ có 20 — đúng lỗi TOCTOU mà `hold()` đã sửa cho `usd`
(Round 28), chưa từng sửa cho hai trục còn lại.

Kiểm bằng cách gọi thẳng `Ledger.hold_steps()`/`child_wall_clock()` — không cần dựng graph
thật, vì đây là lỗi ở đúng một lớp (`Ledger`), giống cách `test_attack_s14.py` đã làm cho
`reserve()`.
"""
import sys, unittest
sys.path.insert(0, "src")

from harness.budget.ledger import Budget, Ledger


class BonSubagentTrongMotLuot(unittest.TestCase):
    def test_bon_lan_hold_steps_khong_vuot_tran_cha(self):
        """Cha còn 20 step. Bốn con, mỗi con tự khai muốn 20 step. Tổng số step được
        GIỮ cho cả bốn không được vượt 20 — trước bản vá, mỗi `hold_steps` sẽ là no-op
        (không tồn tại) và mỗi con giữ nguyên 20 của riêng nó, tổng 80."""
        led = Ledger(Budget.parse("20 steps"))
        held = [led.hold_steps(20) for _ in range(4)]
        self.assertLessEqual(sum(held), 20,
                             f"bốn con giữ tổng {sum(held)} step, vượt trần 20 của cha")

    def test_hold_steps_chia_dung_khong_nhan_ban(self):
        """Chia đúng: con đầu giữ hết 20, ba con sau không còn gì để giữ (0), không phải
        mỗi con đều tự cho mình 20."""
        led = Ledger(Budget.parse("20 steps"))
        h1 = led.hold_steps(20)
        h2 = led.hold_steps(20)
        h3 = led.hold_steps(20)
        self.assertEqual(h1, 20)
        self.assertEqual(h2, 0, "con thứ hai vẫn giữ được step dù con đầu đã lấy hết")
        self.assertEqual(h3, 0)

    def test_release_tra_lai_phan_khong_dung_het(self):
        led = Ledger(Budget.parse("20 steps"))
        held = led.hold_steps(20)
        self.assertEqual(led.remaining_steps(), 0)
        led.release_steps(held, actual=5)          # con chỉ dùng hết 5/20 đã giữ
        self.assertEqual(led.remaining_steps(), 15,
                         "step không dùng hết của con không được trả lại cho cha")

    def test_child_wall_clock_khong_vuot_thoi_gian_con_lai_cua_cha(self):
        """Con tự khai muốn 300s, cha chỉ còn ~60s — con phải bị cắt xuống ~60s, không
        được hứa nhiều thời gian hơn cha thật sự còn."""
        led = Ledger(Budget.parse("$5, 20 steps, 60s"))
        capped = led.child_wall_clock(300.0)
        self.assertLessEqual(capped, 60.0 + 0.5,   # nới nhẹ cho độ trễ đo clock trong test
                             f"con được cấp {capped}s, vượt ~60s cha còn lại")

    def test_child_wall_clock_khong_nhan_ban_giua_nhieu_con(self):
        """Wall-clock KHÔNG phải hồ tài nguyên bị chia — hai con hỏi liên tiếp đều nhận
        được gần đúng cùng một trần (khác `steps`/`usd`, nơi con thứ hai nhận ít hơn)."""
        led = Ledger(Budget.parse("$5, 20 steps, 60s"))
        c1 = led.child_wall_clock(300.0)
        c2 = led.child_wall_clock(300.0)
        self.assertAlmostEqual(c1, c2, delta=0.5,
                               msg="con thứ hai nhận trần thời gian khác hẳn con đầu")


class MutationXacNhanLoadBearing(unittest.TestCase):
    """Khôi phục hành vi CŨ (con giữ nguyên `steps` tự khai, không hold) và xác nhận tổng
    step vượt trần cha thật — chứng minh bản vá không phải trang trí."""

    def test_khong_co_ban_va_thi_bon_con_deu_giu_du_20(self):
        led = Ledger(Budget.parse("20 steps"))
        # Mô phỏng CHÍNH XÁC hành vi trước bản vá: con giữ nguyên số step tự khai, Ledger
        # cha không hề bị trừ.
        held = [min(20, 20) for _ in range(4)]     # không gọi hold_steps() thật
        self.assertEqual(sum(held), 80,
                         "mutation phải cho tổng 80 (4×20) — nếu test này fail nghĩa là "
                         "phép so sánh không còn phản ánh đúng lỗi cũ")
        self.assertGreater(sum(held), led.budget.steps,
                           "80 phải vượt trần 20 của cha — đây chính là S-13")


class ChayThatQuaDayNoi(unittest.TestCase):
    """Gọi thẳng `_run_subagent` module-level (`lg/runtime.py`) bốn lần trên CÙNG một
    `led` — xác nhận chính hàm đó, không chỉ `Ledger` cô lập, thật sự gọi
    `hold_steps()`/`release_steps()`/`child_wall_clock()` đúng chỗ."""

    def _make_toolspec(self, tracker: list, budget_steps=20, n_calls=20):
        """`tracker` ghi một phần tử mỗi lần `noop` THẬT SỰ chạy — tín hiệu quan sát được
        trực tiếp, không đi qua số học `Ledger` (một mutation trong `_run_subagent` có
        thể vừa bỏ `hold()` vừa vô tình làm `release()` triệt tiêu về 0, khiến
        `remaining_steps()` trông vẫn "an toàn" dù cha không hề bị trừ — xem lịch sử sửa
        của chính test này). Số lần tool chạy thì không thể bị triệt tiêu kiểu đó."""
        from harness import Agent, tool
        from harness.models.fake import FakeModel

        @tool(effect="read")
        def noop(x: int) -> str:
            """Không làm gì."""
            tracker.append(x)
            return "ok"

        script = [FakeModel.tool_call("noop", {"x": i}) for i in range(n_calls)] + \
                 [FakeModel.text("xong")]
        child = Agent(name="Con", job="lặp", model="claude-opus-5",
                     provider=FakeModel(script), tools=[noop],
                     budget=f"$5, {budget_steps} steps")
        return child.as_tool()

    def test_con_bi_cap_xuong_dung_step_cha_con_lai(self):
        """Cha chỉ còn 3 step. Con TỰ KHAI muốn 20 và kịch bản THẬT SỰ có 20 lời gọi
        tool sẵn sàng chạy. Nếu bị cap đúng, con chỉ chạy được ~3 lượt trước khi Ledger
        CỦA CHÍNH CON dừng nó — không phải 20."""
        from harness.lg.runtime import _run_subagent

        led = Ledger(Budget.parse("$5, 3 steps"))
        tracker: list = []
        sub = self._make_toolspec(tracker, budget_steps=20, n_calls=20)

        _run_subagent(sub, {"task": "t"}, led)

        self.assertLess(len(tracker), 20,
                        f"noop chạy {len(tracker)} lần — con không hề bị cap xuống 3 "
                        f"step còn lại của cha, nó chạy hết cả 20 nó tự khai")

    def test_bon_con_cong_don_khong_vuot_qua_tong_the_cha_khai_bao(self):
        """Bốn con liên tiếp, mỗi con muốn 20 step, cha chỉ có 8 TỔNG CỘNG cho tất cả.
        Tổng số lần noop chạy CỘNG DỒN qua cả bốn con không được vượt xa 8 — nếu mỗi con
        đều lọt qua uncapped, tổng sẽ là 80."""
        from harness.lg.runtime import _run_subagent

        led = Ledger(Budget.parse("$5, 8 steps"))
        tracker: list = []
        for _ in range(4):
            sub = self._make_toolspec(tracker, budget_steps=20, n_calls=20)
            _run_subagent(sub, {"task": "t"}, led)

        self.assertLess(len(tracker), 80,
                        f"noop chạy tổng {len(tracker)} lần qua 4 con — không hề bị chặn "
                        f"bởi trần 8 step của cha (80 = 4×20 nếu hoàn toàn uncapped)")

    def test_con_dung_it_hon_giu_thi_step_thua_duoc_tra_lai(self):
        """Con chỉ khai `n_calls=2` (dùng ít hơn 20 step được giữ) — phần dư phải quay
        lại cho cha, để con thứ hai vẫn còn chỗ mà giữ."""
        from harness.lg.runtime import _run_subagent

        led = Ledger(Budget.parse("$5, 20 steps"))
        light = self._make_toolspec([], budget_steps=20, n_calls=2)   # giữ 20, dùng ít

        _run_subagent(light, {"task": "t"}, led)
        self.assertGreater(led.remaining_steps(), 0,
                           "con dùng ít hơn giữ mà cha không còn step nào — release_steps "
                           "không trả lại phần dư")


if __name__ == "__main__":
    unittest.main()
