"""`harness.tools.builtin.{calc,files,web}` — the only module cluster with no dedicated
test file, per `design/review-architect.md`'s grounding sweep: every other module touched
by the M6-M10 growth roadmap has one, this cluster didn't, and it's the first code a
beginner (`docs/15-first-agent.md`'s stated audience) actually touches.

Also covers G-4: `calculate()` classified `effect="read"` (floor ALLOW, auto-retried,
runs in parallel) let a model freeze the whole process with a twelve-character argument
(`"9**9**9**9"` — CPython computing an integer with ~370 million digits inside one
C-level `long_pow` call that never releases the GIL, so nothing — not `timeout_s`, not
the wall-clock budget, not T-6.2 cancellation — can interrupt it once it starts).
"""
import asyncio
import os
import sys
import tempfile
import unittest

sys.path.insert(0, "src")

from harness import Effect
from harness.tools.builtin.calc import calculate
from harness.tools.builtin.files import read_file, write_file
from harness.tools.builtin.web import fetch, search
from harness.testing import NetworkAccessInTest, no_network
from harness.workspace import WorkspaceEscapeError


def _call(t, **kw):
    return asyncio.run(t.fn(**kw))


class TinhToan(unittest.TestCase):
    def test_phep_tinh_thuong_dung(self):
        self.assertEqual(_call(calculate, expression="2+2"), 4)
        self.assertEqual(_call(calculate, expression="12 * (3 + 4)"), 84)
        self.assertEqual(_call(calculate, expression="2 ** 10"), 1024)
        self.assertEqual(_call(calculate, expression="-5"), -5)

    def test_khong_phai_so_hoc_thi_tu_choi(self):
        with self.assertRaises(ValueError):
            _call(calculate, expression="__import__('os').system('id')")
        with self.assertRaises(ValueError):
            _call(calculate, expression="[1,2,3]")

    def test_effect_la_read(self):
        self.assertIs(calculate.effect, Effect.READ)

    def test_so_mu_khong_lo_bi_tu_choi_ngay_khong_treo_may(self):
        """G-4: `9**9**9**9` từng treo cả tiến trình (~370 triệu chữ số, một lời gọi
        C-level `long_pow` không nhả GIL). Giờ phải bị từ chối gần như tức thì."""
        import time
        t0 = time.monotonic()
        with self.assertRaises(ValueError):
            _call(calculate, expression="9**9**9**9")
        self.assertLess(time.monotonic() - t0, 1.0,
                        "bị treo thay vì từ chối ngay — G-4 chưa được vá")

    def test_so_mu_am_khong_lo_cung_bi_tu_choi(self):
        with self.assertRaises(ValueError):
            _call(calculate, expression="2**-1000000")

    def test_so_mu_binh_thuong_van_chay_dung(self):
        self.assertEqual(_call(calculate, expression="2**63"), 2**63)
        self.assertEqual(_call(calculate, expression="10**10"), 10**10)

    def test_bit_length_nhan_so_mu_vuot_nguong_cung_bi_tu_choi(self):
        """Cô lập đúng nhánh bit_length(base)*exponent, KHÔNG đi qua nhánh "số mũ quá
        lớn": chọn số mũ = 64 (đúng ngưỡng riêng, không vượt), và một cơ số đủ lớn để
        101 * 64 hay hơn vẫn vượt 10_000 — cơ số ~2**160 (bit_length 161):
        161 * 64 = 10304 > 10_000."""
        big_base = 2**160
        with self.assertRaises(ValueError):
            _call(calculate, expression=f"{big_base}**64")
        # Đối chứng: cơ số nhỏ hơn nhiều, cùng số mũ 64, KHÔNG bị chặn bởi nhánh này.
        self.assertEqual(_call(calculate, expression="2**64"), 2**64)


class DocFile(unittest.TestCase):
    """`read_file`/`write_file` — nhốt trong CWD của tiến trình (không phải một root tuỳ
    ý như `CodeTools`, vì đây là tool module-level không có constructor để truyền root
    vào — đúng như docstring của chính module tự nói)."""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self._old_cwd = os.getcwd()
        os.chdir(self._d.name)

    def tearDown(self):
        os.chdir(self._old_cwd)
        self._d.cleanup()

    def test_ghi_roi_doc_lai(self):
        _call(write_file, path="a.txt", text="hello")
        self.assertEqual(_call(read_file, path="a.txt"), "hello")

    def test_duong_dan_ra_ngoai_cwd_bi_tu_choi(self):
        with self.assertRaises(WorkspaceEscapeError):
            _call(read_file, path="../../../etc/passwd")
        with self.assertRaises(WorkspaceEscapeError):
            _call(write_file, path="/etc/passwd", text="x")

    def test_effect(self):
        self.assertIs(read_file.effect, Effect.READ)
        self.assertIs(write_file.effect, Effect.WRITE)


class WebTools(unittest.TestCase):
    """Không gọi mạng thật — `no_network()` là Poka-Yoke có giá trị nhất trong bộ test
    (`harness/testing/__init__.py`'s own docstring)."""

    def test_effect_la_external(self):
        self.assertIs(search.effect, Effect.EXTERNAL)
        self.assertIs(fetch.effect, Effect.EXTERNAL)

    def test_khong_goi_mang_that_trong_test(self):
        # Dựng event loop TRƯỚC khi vào no_network(): tạo loop mới cũng mở một
        # socketpair nội bộ — dựng nó bên trong `with no_network()` thì chính việc DỰNG
        # LOOP bị chặn nhầm, không phải cuộc gọi mạng đang muốn kiểm tra.
        loop = asyncio.new_event_loop()
        try:
            with no_network():
                with self.assertRaises(NetworkAccessInTest):
                    loop.run_until_complete(fetch.fn(url="https://example.com"))
                with self.assertRaises(NetworkAccessInTest):
                    loop.run_until_complete(search.fn(query="anything"))
        finally:
            loop.close()


if __name__ == "__main__":
    unittest.main()
