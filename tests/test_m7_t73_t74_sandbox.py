"""M7/T-7.3 + T-7.4 (docs/17-research-alignment.md): `Sandbox` seam.

T-7.3: Protocol `Sandbox` với `run(cmd, *, cwd, env, timeout) -> Completed`; ship
`InProcess` (không cách ly, nói rõ) và `Subprocess` (env sạch, cwd = workspace, không
secret). "Done": một bản cài đặt bên thứ ba cắm được mà không sửa core (§I.1 của
`proof.py`'s khuôn, áp cho seam thứ sáu này).

T-7.4: `Sandbox.run` không bao giờ nhận `Secret`; env được lọc trắng. "Test": red-team —
secret không xuất hiện trong env của tiến trình con.
"""
import unittest

from harness.sandbox import Completed, InProcess, Subprocess
from harness.secrets import Secret


def _run(sandbox, cmd, *, cwd=".", env=None, timeout=5.0) -> Completed:
    import asyncio
    return asyncio.run(sandbox.run(cmd, cwd=cwd, env=env or {}, timeout=timeout))


class ChayLenhCoBan(unittest.TestCase):
    def test_inprocess_chay_lenh_thanh_cong(self):
        r = _run(InProcess(), ["echo", "hello"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("hello", r.stdout)
        self.assertFalse(r.timed_out)

    def test_subprocess_chay_lenh_thanh_cong(self):
        r = _run(Subprocess(), ["echo", "hello"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("hello", r.stdout)

    def test_lenh_khong_ton_tai_tra_127_khong_crash(self):
        r = _run(InProcess(), ["mot-lenh-khong-ton-tai-chac-chan-12345"])
        self.assertEqual(r.returncode, 127)

    def test_timeout_duoc_ton_trong(self):
        r = _run(Subprocess(), ["sleep", "5"], timeout=0.2)
        self.assertTrue(r.timed_out)
        self.assertEqual(r.returncode, -1)


class EnvSachKhongKeThuaOsEnviron(unittest.TestCase):
    """T-7.4/T-7.3: `env` được dùng ĐÚNG NHƯ đưa vào, không bao giờ merge với
    `os.environ` của tiến trình cha — đây là "env sạch" T-7.3 tự đặt ra."""

    def test_subprocess_khong_thay_bien_moi_truong_cua_tien_trinh_cha(self):
        import os
        os.environ["HARNESS_TEST_CANARY_KHONG_DUOC_LO"] = "canary-value-xyz"
        try:
            r = _run(Subprocess(), ["env"], env={"PATH": os.environ.get("PATH", "")})
            self.assertNotIn("canary-value-xyz", r.stdout,
                            "biến môi trường của tiến trình CHA không được lọt vào con — "
                            "env sạch nghĩa là chỉ có đúng những gì đưa vào `env=`")
        finally:
            del os.environ["HARNESS_TEST_CANARY_KHONG_DUOC_LO"]

    def test_inprocess_cung_khong_ke_thua_os_environ(self):
        import os
        os.environ["HARNESS_TEST_CANARY_2"] = "canary-2-xyz"
        try:
            r = _run(InProcess(), ["env"], env={"PATH": os.environ.get("PATH", "")})
            self.assertNotIn("canary-2-xyz", r.stdout)
        finally:
            del os.environ["HARNESS_TEST_CANARY_2"]

    def test_chi_dung_bien_duoc_dua_vao(self):
        r = _run(Subprocess(), ["env"], env={"MY_ONLY_VAR": "gia-tri-rieng"})
        self.assertIn("MY_ONLY_VAR=gia-tri-rieng", r.stdout)


class SecretKhongVaoSandbox(unittest.TestCase):
    """T-7.4: `Sandbox.run` không bao giờ nhận `Secret` trực tiếp."""

    def test_secret_object_truc_tiep_bi_tu_choi(self):
        s = Secret("sk-that-su-bi-mat", name="api_key")
        with self.assertRaises(TypeError):
            _run(InProcess(), ["env"], env={"API_KEY": s})

    def test_subprocess_cung_tu_choi_secret_object(self):
        s = Secret("sk-that-su-bi-mat-2", name="api_key")
        with self.assertRaises(TypeError):
            _run(Subprocess(), ["env"], env={"API_KEY": s})

    def test_redteam_secret_da_reveal_khong_dua_vao_env_thi_khong_lo(self):
        """Red-team đúng nghĩa T-7.4 đặt ra: một `Secret` được `reveal()` ở nơi khác
        trong chương trình (vd. để gọi API thật), nhưng KHÔNG được đưa vào `env=` của
        sandbox — plaintext của nó không được xuất hiện trong output của tiến trình
        con."""
        s = Secret("sk-plaintext-tuyet-mat-999", name="api_key")
        with s.reveal() as plaintext:
            self.assertEqual(plaintext, "sk-plaintext-tuyet-mat-999")
            # Cố tình KHÔNG đưa plaintext vào env= — mô phỏng tool tác giả cẩn thận.
            r = _run(Subprocess(), ["env"], env={"UNRELATED": "abc"})
        self.assertNotIn("sk-plaintext-tuyet-mat-999", r.stdout)

    def test_neu_tool_tu_dua_plaintext_vao_thi_do_la_lua_chon_tuong_minh(self):
        """Đối chứng: NẾU tác giả tool cố tình `reveal()` rồi tự đưa plaintext vào
        `env=`, `Sandbox.run` không cấm — đây là hành động tường minh của tác giả tool,
        không phải điều `Sandbox` có thể/nên ngăn (nó không biết `str` nào là bí mật)."""
        s = Secret("sk-duoc-dua-vao-co-y", name="api_key")
        with s.reveal() as plaintext:
            r = _run(Subprocess(), ["env"], env={"API_KEY": plaintext})
        self.assertIn("sk-duoc-dua-vao-co-y", r.stdout,
                      "đây LÀ hành vi mong đợi khi tác giả tool tự đưa vào — không phải "
                      "một lỗ hổng của Sandbox")


class BenThuBaCamDuocKhongSuaCore(unittest.TestCase):
    """T-7.3's "Done": một bản cài đặt Sandbox của BÊN THỨ BA cắm được, không sửa core —
    cùng khuôn `examples/proof.py §I.1` áp cho năm seam kia."""

    def test_sandbox_ben_thu_ba_hoat_dong_dung_protocol(self):
        class SandboxCuaToi:
            """Không kế thừa `Sandbox` — chỉ đúng protocol (`run(cmd, *, cwd, env,
            timeout) -> Completed`), giống hệt cách proof.py's ModelCuaToi/StoreCuaToi
            không kế thừa gì."""
            def __init__(self):
                self.calls: list = []

            async def run(self, cmd, *, cwd, env, timeout):
                self.calls.append((tuple(cmd), cwd, dict(env), timeout))
                return Completed(0, "gia-lap-thanh-cong", "", False)

        import asyncio
        sb = SandboxCuaToi()
        r = asyncio.run(sb.run(["ls"], cwd="/tmp", env={"X": "1"}, timeout=5.0))
        self.assertEqual(r.stdout, "gia-lap-thanh-cong")
        self.assertEqual(len(sb.calls), 1)
        self.assertEqual(sb.calls[0], (("ls",), "/tmp", {"X": "1"}, 5.0))


class MutationEnvCheckCoTacDung(unittest.TestCase):
    def test_bo_check_env_van_raise_nhung_thong_bao_te_hon(self):
        """Mutation: một `_check_env` giả không làm gì cả. `subprocess.Popen`'s tầng
        thấp VẪN raise `TypeError` (nó cũng không nhận `Secret` cho env — phòng thủ
        nhiều lớp tình cờ) — nhưng đây chính là điều `_check_env` tồn tại để tránh:
        không phải "có raise hay không", mà "raise RÕ RÀNG, sớm, đúng chỗ" so với "raise
        từ sâu trong stack trace của subprocess.py, khó hiểu, trễ". Test khẳng định
        đúng sự khác biệt đó, không khẳng định sai rằng thiếu `_check_env` thì im lặng."""
        import harness.sandbox as sandbox_mod
        original = sandbox_mod._check_env
        sandbox_mod._check_env = lambda env: None
        try:
            s = Secret("sk-mutation-test", name="k")
            with self.assertRaises(TypeError) as ctx:
                _run(InProcess(), ["env"], env={"API_KEY": s})
            # Với _check_env thật, thông báo nêu rõ "T-7.4" và "Sandbox.run never
            # accepts one directly" — với mutation này, thông báo là lỗi encode nội bộ
            # của subprocess, không nhắc gì tới Secret/T-7.4 cả.
            self.assertNotIn("T-7.4", str(ctx.exception),
                            "mutation lẽ ra phải cho một lỗi KHÁC (từ subprocess.py, "
                            "không nhắc T-7.4) — nếu vẫn thấy 'T-7.4' thì mutation "
                            "không thật sự tắt được _check_env")
        finally:
            sandbox_mod._check_env = original

    def test_check_env_that_bao_ro_va_som(self):
        """Đối chứng: với `_check_env` THẬT, thông báo lỗi phải rõ ràng và trỏ tới
        T-7.4/docs — không phải một `TypeError` mù mờ từ tầng subprocess."""
        s = Secret("sk-that", name="k")
        with self.assertRaises(TypeError) as ctx:
            _run(InProcess(), ["env"], env={"API_KEY": s})
        self.assertIn("T-7.4", str(ctx.exception))
        self.assertIn("Secret", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
