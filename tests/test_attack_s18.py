"""S-18: `EgressPolicy` chỉ so khớp CHUỖI hostname — P-4 (`Policy.check` thuần, không I/O)
cấm nó resolve DNS. Kiểm hai điều: (1) claim gốc của review về nhầm lẫn URL do
`userinfo@host` KHÔNG còn đúng trên `urlparse` của Python (đã lỗi thời, `EgressPolicy` an
toàn trước biến thể đó); (2) DNS rebinding vẫn là lỗ thật — không kiểm được bằng test đơn
vị (cần DNS resolver thật), nên viết lại thành một khẳng định về THIẾT KẾ: `EgressPolicy`
không bao giờ đọc gì ngoài chuỗi URL model đưa ra.
"""
import unittest

from harness.policy.base import ToolCall, Verdict
from harness.policy.builtin import EgressPolicy
from harness.tools import Effect, ToolSpec


def _spec(name="fetch_page") -> ToolSpec:
    async def fn(url: str) -> str: return "ok"
    return ToolSpec(name=name, description="d", input_schema={"type": "object"},
                    effect=Effect.EXTERNAL, fn=fn)


class UserinfoKhongLuaDuoc(unittest.TestCase):
    """Claim gốc S-18: `http://intranet.internal@evil.example/` có thể khiến policy thấy
    một host khác với host mà client HTTP thật sự kết nối. Không còn đúng: `urlparse` của
    Python (thứ `EgressPolicy` dùng) và client HTTP chuẩn RFC 3986 đều đồng ý là
    `evil.example`."""

    def test_host_sau_at_moi_la_host_that(self):
        p = EgressPolicy(["docs.python.org"])
        call = ToolCall("c1", "fetch_page", {"url": "http://intranet.internal@evil.example/"},
                        _spec())
        r = p.check(call, None)
        self.assertEqual(r.verdict, Verdict.DENY,
                         "phải DENY — host thật là evil.example, không trong allowlist")

    def test_fragment_at_khong_lam_lo_host_that(self):
        p = EgressPolicy(["docs.python.org"])
        call = ToolCall("c1", "fetch_page",
                        {"url": "http://evil.example#@docs.python.org/"}, _spec())
        r = p.check(call, None)
        self.assertEqual(r.verdict, Verdict.DENY,
                         "phải DENY — host thật là evil.example (phần sau # là fragment)")


class KhongResolveDNS(unittest.TestCase):
    """`EgressPolicy.check` không được đọc gì ngoài chuỗi trong `call.arguments` — P-4
    (thuần, không I/O). Đây là lý do DNS rebinding không chặn được, kiểm bằng cách xác
    nhận `check()` không đụng mạng: cùng một chuỗi host luôn cho cùng một verdict, không
    phụ thuộc gì khác ngoài chính chuỗi đó (nếu nó có I/O — vd. resolve DNS thật — verdict
    có thể đổi giữa hai lần gọi tuỳ tình trạng mạng)."""

    def test_verdict_chi_phu_thuoc_chuoi_host_khong_gi_khac(self):
        p = EgressPolicy(["docs.python.org"])
        call = ToolCall("c1", "fetch_page", {"url": "http://docs.python.org/"}, _spec())
        r1 = p.check(call, None)
        r2 = p.check(call, None)
        self.assertEqual(r1.verdict, r2.verdict)
        self.assertEqual(r1.verdict, Verdict.ALLOW)

    def test_host_khong_trong_allowlist_bi_chan_du_co_the_tro_ve_dau(self):
        """Một host không nằm trong allowlist bị chặn — nhưng nếu chuỗi host TỰ NÓ đã
        được coi là hợp lệ (nằm trong allowlist), EgressPolicy không có cách nào biết nó
        thật ra resolve về đâu lúc request thật chạy. Đây là giới hạn được ghi lại, không
        phải cái để "sửa" — sửa thật cần một tầng mạng, không phải một Policy thuần."""
        p = EgressPolicy(["attacker-controlled-dns.example"])
        call = ToolCall("c1", "fetch_page",
                        {"url": "http://attacker-controlled-dns.example/"}, _spec())
        r = p.check(call, None)
        self.assertEqual(r.verdict, Verdict.ALLOW,
                         "EgressPolicy cho qua bất kỳ host nào TRONG allowlist, không có "
                         "cách nào biết nó resolve về IP gì lúc request thật chạy — "
                         "đúng giới hạn S-18 mô tả, không phải lỗi cần vá bằng code")


if __name__ == "__main__":
    unittest.main()
