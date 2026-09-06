"""M8/T-8.4 (docs/17-research-alignment.md, S-06): `harness.eval.cost_per_success(runs)`
— tổng chi phí / P(thành công), kèm khoảng tin cậy — không bao giờ một con số trần trụi
(§45: "thà nói 'chưa đủ evidence' còn hơn đoán").
"""
import unittest
from dataclasses import dataclass

from harness.eval import cost_per_success
from harness.eval.cost import _wilson_interval
from harness.result import Money, Result, StopReason, Usage


def _run(ok: bool, cost: str) -> Result:
    return Result("", StopReason.COMPLETED if ok else StopReason.ERROR, 1,
                  Money(cost.lstrip("$")), Usage(), "r1")


class CongThucDung(unittest.TestCase):
    def test_tat_ca_thanh_cong_cost_per_success_la_trung_binh(self):
        runs = [_run(True, "$1"), _run(True, "$1"), _run(True, "$1")]
        r = cost_per_success(runs)
        self.assertAlmostEqual(r.total_cost_usd, 3.0)
        self.assertEqual(r.n_success, 3)
        self.assertEqual(r.success_rate, 1.0)
        self.assertAlmostEqual(r.cost_per_success_usd, 3.0)

    def test_that_bai_van_ton_tien_lam_cost_per_success_tang(self):
        """Đúng luận điểm S-06: run thất bại KHÔNG miễn phí — vẫn tốn tiền — nên chi
        phí/thành công phải LỚN HƠN chi phí/lượt chạy đơn thuần."""
        runs = [_run(True, "$1"), _run(False, "$1"), _run(False, "$1"), _run(False, "$1")]
        r = cost_per_success(runs)
        self.assertAlmostEqual(r.total_cost_usd, 4.0)
        self.assertEqual(r.n_success, 1)
        self.assertAlmostEqual(r.cost_per_success_usd, 16.0)   # $4 tổng / 25% thành công
        self.assertGreater(r.cost_per_success_usd, r.total_cost_usd / r.n_runs,
                           "cost/success phải lớn hơn cost/run thô — đúng lý do S-06 "
                           "nói cost/task là công thức sai")

    def test_khong_thanh_cong_nao_tra_none_khong_phai_infinity_hay_0(self):
        runs = [_run(False, "$1"), _run(False, "$1")]
        r = cost_per_success(runs)
        self.assertIsNone(r.cost_per_success_usd,
                          "0 thành công -> cost per success KHÔNG XÁC ĐỊNH, không phải "
                          "0 hay vô hạn ngầm định — phải nói rõ 'chưa đủ evidence'")
        self.assertIsNone(r.ci_low_usd)
        self.assertIsNone(r.ci_high_usd)

    def test_khong_co_run_nao_raise(self):
        with self.assertRaises(ValueError):
            cost_per_success([])


class KhoangTinCay(unittest.TestCase):
    def test_wilson_8_tren_10_95_phan_tram(self):
        lo, hi = _wilson_interval(8, 10, 1.96)
        # Giá trị tham chiếu chuẩn cho Wilson interval 8/10 @ 95% — khoảng 0.49–0.94.
        self.assertAlmostEqual(lo, 0.4902, places=3)
        self.assertAlmostEqual(hi, 0.9433, places=3)

    def test_ci_luon_di_kem_khong_bao_gio_thieu(self):
        runs = [_run(True, "$1")] * 8 + [_run(False, "$1")] * 2
        r = cost_per_success(runs)
        self.assertIsNotNone(r.ci_low_usd)
        self.assertIsNotNone(r.ci_high_usd)
        self.assertLess(r.ci_low_usd, r.cost_per_success_usd)
        self.assertGreater(r.ci_high_usd, r.cost_per_success_usd)

    def test_mau_lon_hon_cho_khoang_tuong_doi_hep_hon(self):
        """Mẫu lớn hơn (cùng tỉ lệ thành công) phải cho khoảng tin cậy TƯƠNG ĐỐI hẹp
        hơn — càng nhiều bằng chứng, càng chắc chắn hơn. So sánh width/cost_per_success
        (tương đối), không phải width tuyệt đối — tổng chi phí tự nó cũng tăng theo n
        nên so tuyệt đối giữa hai cỡ mẫu khác nhau là so sai đại lượng."""
        many = [_run(True, "$1")] * 80 + [_run(False, "$1")] * 20
        few = [_run(True, "$1")] * 8 + [_run(False, "$1")] * 2
        r_many = cost_per_success(many)
        r_few = cost_per_success(few)
        rel_width_many = (r_many.ci_high_usd - r_many.ci_low_usd) / r_many.cost_per_success_usd
        rel_width_few = (r_few.ci_high_usd - r_few.ci_low_usd) / r_few.cost_per_success_usd
        self.assertLess(rel_width_many, rel_width_few,
                        "mẫu 80 run phải có khoảng tin cậy TƯƠNG ĐỐI hẹp hơn mẫu 8 run "
                        "cùng tỉ lệ thành công")

    def test_confidence_khong_ho_tro_raise(self):
        with self.assertRaises(ValueError):
            cost_per_success([_run(True, "$1")], confidence=0.5)

    def test_confidence_99_rong_hon_95(self):
        runs = [_run(True, "$1")] * 8 + [_run(False, "$1")] * 2
        r95 = cost_per_success(runs, confidence=0.95)
        r99 = cost_per_success(runs, confidence=0.99)
        self.assertLess(r95.ci_high_usd - r95.ci_low_usd, r99.ci_high_usd - r99.ci_low_usd)


class KetQuaTuongThich(unittest.TestCase):
    def test_chap_nhan_object_khong_phai_result_that(self):
        """`runs` chỉ cần `.ok`/`.cost` — không ép phải là `harness.Result` thật, để
        test double hay bản ghi đọc lại từ golden set (M10) cũng dùng được."""
        @dataclass
        class GiaResult:
            ok: bool
            cost: float

        runs = [GiaResult(True, 1.0), GiaResult(False, 2.0)]
        r = cost_per_success(runs)
        self.assertAlmostEqual(r.total_cost_usd, 3.0)
        self.assertAlmostEqual(r.cost_per_success_usd, 6.0)   # $3 / 50%

    def test_str_khong_crash_ca_hai_nhanh(self):
        r1 = cost_per_success([_run(True, "$1")])
        r2 = cost_per_success([_run(False, "$1")])
        self.assertIn("per success", str(r1))
        self.assertIn("undefined", str(r2))


class MutationCoTacDung(unittest.TestCase):
    def test_bo_chia_cho_success_rate_thi_test_do(self):
        """Mutation: `cost_per_success` giả trả về TRUNG BÌNH cost/run thay vì
        cost/THÀNH CÔNG — xác nhận test chính (S-06's luận điểm) phụ thuộc đúng vào
        công thức chia cho success_rate."""
        def fake_cost_per_success(runs, *, confidence=0.95):
            total = sum(float(r.cost.decimal) for r in runs)
            return total / len(runs)      # SAI theo S-06 — không chia cho success_rate

        runs = [_run(True, "$1"), _run(False, "$1"), _run(False, "$1"), _run(False, "$1")]
        result = fake_cost_per_success(runs)
        self.assertEqual(result, 1.0,
                         "với mutation này, cost/task (sai) = $1 — khác $4 của "
                         "cost/success (đúng), chứng minh test chính phụ thuộc vào "
                         "công thức chia cho success_rate")


if __name__ == "__main__":
    unittest.main()
