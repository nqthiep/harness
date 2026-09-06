"""S-26: `canonical_args` được review mô tả ép mọi giá trị về `str` (`Scope.args:
Mapping[str, str]`), làm `transfer(amount=10)` (int) khớp nhầm `transfer(amount="10")`
(str) — một grant cấp cho lời gọi này lại áp được cho lời gọi khác có ý nghĩa khác.

Kiểm trên code hôm nay: `Scope.args: Mapping[str, Any] | None` (policy/decision.py), KHÔNG
phải `Mapping[str, str]`, và `Scope.matches()` so sánh bằng `dict(self.args) == dict(args)`
— so sánh Python dict trực tiếp, giữ nguyên kiểu, không qua bất kỳ bước chuẩn hoá nào ép
kiểu về chuỗi. `10 == "10"` là `False` trong Python — claim gốc của S-26 đã lỗi thời, không
còn đúng trên code hôm nay. Không cần sửa.
"""
import unittest

from harness.policy.decision import Scope


class KieuKhongBiMatQuaScope(unittest.TestCase):
    def test_int_khong_khop_chuoi_cung_gia_tri(self):
        scope = Scope(tool="transfer", args={"amount": 10})
        self.assertTrue(scope.matches("transfer", {"amount": 10}, call_id=None),
                        "cùng kiểu, cùng giá trị phải khớp")
        self.assertFalse(scope.matches("transfer", {"amount": "10"}, call_id=None),
                         "int 10 khớp nhầm chuỗi '10' — đúng lỗi S-26 mô tả, đã lỗi thời "
                         "trên code hôm nay nếu test này fail")

    def test_scope_args_khong_ep_ve_str(self):
        import dataclasses
        f = {f.name: f.type for f in dataclasses.fields(Scope)}
        self.assertIn("Any", str(f["args"]),
                      "Scope.args không còn là Mapping[str, Any] — kiểm lại S-26")


if __name__ == "__main__":
    unittest.main()
