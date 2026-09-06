"""`harness.tools.code.CodeTools` — bộ tool code, và cái lỗ mà nó vá.

Lỗ đó được ghi trong chính tài liệu của dự án suốt từ M7: `confine()` (T-7.1) tồn tại,
`read_file`/`write_file` trong `tools/builtin` KHÔNG gọi nó, và
`CODING_AGENT_BLUEPRINT.md` nói thẳng "if you use them as-is, there is no root
confinement". Nói ra một lỗ hổng không phải vá nó: `read_file(path="../../.ssh/id_rsa")`
là đúng một khối `tool_use`, trên chính bộ tool mà đường dành cho người mới phát ra.

Nhóm test ở đây bám vào bốn thứ `code.py` tự hứa: nhốt trong workspace, điều hướng rẻ
(`outline`/`search_code` thay vì đọc cả file), sửa chính xác và từ chối khi mơ hồ, và
phân loại effect nói đúng sự thật về từng tool.
"""
import asyncio
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from shutil import which

from harness import Effect
from harness.tools.code import PASS_ENV, CodeTools
from harness.workspace import WorkspaceEscapeError

SRC = '''import os


class Thing:
    def go(self):
        return 1


def top():
    return Thing()
'''


class Base(unittest.TestCase):
    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.root = self._d.name
        os.makedirs(os.path.join(self.root, "pkg"))
        Path(self.root, "pkg", "a.py").write_text(SRC, encoding="utf-8")
        Path(self.root, "README.md").write_text("hello\n", encoding="utf-8")
        self.ct = CodeTools(self.root)
        self.T = {t.name: t for t in self.ct.tools()}

    def tearDown(self):
        self._d.cleanup()

    def call(self, name, **kw):
        return asyncio.run(self.T[name].fn(**kw))


class NhotTrongWorkspace(Base):
    def test_dot_dot_bi_tu_choi(self):
        with self.assertRaises(WorkspaceEscapeError):
            self.call("read_source", path="../../etc/passwd")

    def test_duong_dan_tuyet_doi_bi_tu_choi(self):
        with self.assertRaises(WorkspaceEscapeError):
            self.call("read_source", path="/etc/passwd")

    def test_ghi_ra_ngoai_goc_bi_tu_choi_truoc_khi_ghi(self):
        outside = Path(self._d.name).parent / "khong-duoc-ghi.txt"
        with self.assertRaises(WorkspaceEscapeError):
            self.call("write_source", path=f"../{outside.name}", text="x")
        self.assertFalse(outside.exists(), "đã ghi ra ngoài workspace")

    def test_duong_dan_hop_le_van_di_qua(self):
        self.assertIn("class Thing", self.call("read_source", path="pkg/a.py"))

    def test_builtin_read_file_nay_cung_bi_nhot_vao_cwd(self):
        """Cái lỗ mà tài liệu đã nêu tên từ M7: hai tool này không hề gọi `confine()`.
        (`.fn` là coroutine kể cả với tool đồng bộ — IDL-06 bọc lúc decorate.)"""
        from harness.tools.builtin.files import read_file
        with self.assertRaises(WorkspaceEscapeError):
            asyncio.run(read_file.fn(path="/etc/passwd"))

    def test_builtin_write_file_cung_vay(self):
        from harness.tools.builtin.files import write_file
        with self.assertRaises(WorkspaceEscapeError):
            asyncio.run(write_file.fn(path="../ra-ngoai.txt", text="x"))

    def test_run_tests_target_bi_nhot_giong_moi_tool_khac(self):
        """G-2, đã sửa: trước bản vá, `target` đi thẳng vào argv mà không qua
        `confine()` — cùng chuỗi `"../outside"` mà `read_source` từ chối đúng lại
        chạy được qua `run_tests`. Giờ cả hai đều từ chối như nhau."""
        with self.assertRaises(WorkspaceEscapeError):
            self.call("read_source", path="../outside")
        with self.assertRaises(WorkspaceEscapeError):
            self.call("run_tests", target="../outside")

    def test_run_tests_target_voi_node_id_pytest_van_di_qua(self):
        """`target="file.py::test_name"` (chọn một test cụ thể) là cú pháp hợp lệ của
        pytest — confine() chỉ validate PHẦN ĐƯỜNG DẪN trước `::`, không round-trip cả
        chuỗi qua Path() (sẽ làm sai cú pháp node-id)."""
        out = self.call("run_tests", target="pkg/a.py::test_khong_ton_tai")
        self.assertFalse(out.startswith("exit 0"), out)   # test không tồn tại -> lỗi
        self.assertNotIn("Traceback", out)                # không phải crash của tool

    def test_run_tests_target_bat_dau_bang_gach_ngang_bi_tu_choi(self):
        """G-2: `target` bắt đầu bằng `-` có thể bị hiểu như một cờ dòng lệnh
        (`--rootdir=...`, `-p ...`) thay vì một đường dẫn — từ chối trước khi tới argv."""
        out = self.call("run_tests", target="--rootdir=/")
        self.assertIn("bắt đầu bằng", out)

    def test_git_diff_path_bi_nhot(self):
        """G-2, đã sửa: `git_diff`'s `path` cũng đi thẳng vào argv trước bản vá, cùng lỗ
        như `run_tests`."""
        with self.assertRaises(WorkspaceEscapeError):
            self.call("git_diff", path="../outside")


class DieuHuongRe(Base):
    def test_outline_cho_ban_do_kem_so_dong_khong_phai_ca_file(self):
        out = self.call("outline", path="pkg/a.py")
        self.assertIn("class Thing", out)
        self.assertIn("def go", out)
        self.assertIn("def top", out)
        self.assertNotIn("import os", out, "outline mà kèm cả thân file thì vô nghĩa")
        self.assertLess(len(out), len(SRC))

    def test_outline_noi_that_khi_khong_phai_python(self):
        out = self.call("outline", path="README.md")
        self.assertIn("không phải file Python", out)
        self.assertIn("search_code", out)

    def test_outline_bao_dung_dong_khi_file_sai_cu_phap(self):
        Path(self.root, "hong.py").write_text("def (:\n", encoding="utf-8")
        self.assertIn("dòng 1", self.call("outline", path="hong.py"))

    def test_search_tra_ve_file_va_so_dong(self):
        out = self.call("search_code", pattern=r"def \w+\(")
        self.assertIn("pkg/a.py:5:", out)
        self.assertIn("pkg/a.py:9:", out)

    def test_search_voi_regex_hong_thi_bao_loi_doc_duoc(self):
        self.assertIn("không hợp lệ", self.call("search_code", pattern="([a-"))

    def test_read_source_kem_so_dong_va_gioi_han_duoc_khoang(self):
        out = self.call("read_source", path="pkg/a.py", start=4, end=6)
        self.assertTrue(out.startswith("    4| class Thing"), out)
        self.assertNotIn("def top", out)

    def test_list_files_bo_qua_git_va_pycache(self):
        os.makedirs(os.path.join(self.root, ".git", "objects"))
        Path(self.root, ".git", "config").write_text("x", encoding="utf-8")
        os.makedirs(os.path.join(self.root, "__pycache__"))
        Path(self.root, "__pycache__", "a.pyc").write_text("x", encoding="utf-8")
        out = self.call("list_files")
        self.assertNotIn(".git", out)
        self.assertNotIn("__pycache__", out)
        self.assertIn("pkg/a.py", out)

    def test_file_nhi_phan_khong_bi_do_vao_context(self):
        Path(self.root, "anh.bin").write_bytes(b"\x89PNG\x00\x00\x01binary")
        self.assertIn("nhị phân", self.call("read_source", path="anh.bin"))


class SuaChinhXac(Base):
    def test_sua_mot_cho_duy_nhat_thi_ghi_that(self):
        self.call("edit_source", path="pkg/a.py", old="return 1", new="return 2")
        self.assertIn("return 2", Path(self.root, "pkg", "a.py").read_text())

    def test_khop_nhieu_cho_thi_TU_CHOI_chu_khong_sua_cho_dau_tien(self):
        """Sửa đại chỗ đầu tiên là cách một agent phá file mà không ai thấy cho tới lúc
        chạy test."""
        before = Path(self.root, "pkg", "a.py").read_text()
        out = self.call("edit_source", path="pkg/a.py", old="return", new="X")
        self.assertIn("2 lần", out)
        self.assertEqual(Path(self.root, "pkg", "a.py").read_text(), before)

    def test_khong_khop_thi_noi_ro_phai_lam_gi(self):
        out = self.call("edit_source", path="pkg/a.py", old="không có đâu", new="X")
        self.assertIn("read_source", out)

    def test_write_source_tao_duoc_thu_muc_con(self):
        self.call("write_source", path="pkg/sub/moi.py", text="x = 1\n")
        self.assertEqual(Path(self.root, "pkg", "sub", "moi.py").read_text(), "x = 1\n")


class PhanLoaiEffectNoiDungSuThat(Base):
    def test_bang_effect(self):
        got = {t.name: t.effect for t in self.ct.tools()}
        for name in ("list_files", "read_source", "search_code", "outline",
                     "git_status", "git_diff"):
            self.assertIs(got[name], Effect.READ, name)
        for name in ("write_source", "edit_source", "git_commit"):
            self.assertIs(got[name], Effect.WRITE, name)
        # G-1, đã sửa: hai tool này THỰC THI code (chạy pytest / một binary ngoài), không
        # chỉ ghi file — `danger` là lớp duy nhất buộc duyệt trước khi chạy.
        for name in ("run_tests", "refresh_codebase_docs"):
            self.assertIs(got[name], Effect.DANGER, name)

    def test_khong_co_tool_external_nao_trong_module_nay(self):
        """Không tool nào trong module này tự tra cứu mạng — `search_code`/`outline` chỉ
        đọc filesystem cục bộ trong workspace."""
        self.assertNotIn(Effect.EXTERNAL, {t.effect for t in self.ct.tools()})

    def test_danger_chi_dung_o_hai_tool_thuc_thi_code(self):
        """G-1, đã sửa: bộ ba chết người (external + danger trong cùng agent) vẫn không
        tự động xảy ra khi import module này — module không có tool `external` nào — dù
        giờ nó CÓ hai tool `danger`. `git_push`/`deploy` vẫn là việc của người viết agent
        tự khai thêm."""
        danger = {t.name for t in self.ct.tools() if t.effect is Effect.DANGER}
        self.assertEqual(danger, {"run_tests", "refresh_codebase_docs"})

    def test_run_tests_la_danger_khong_phai_write(self):
        """G-1, đã sửa: `run_tests` IMPORT rồi chạy bất kỳ code Python nào trong file
        test — kể cả file model vừa tự ghi bằng `write_source`. `danger`'s floor là ASK
        ở cả hai mức safety; `write` thì auto-ALLOW ở mức standard."""
        from harness.tools import EFFECT_PROFILES
        self.assertIs(self.T["run_tests"].effect, Effect.DANGER)
        self.assertFalse(EFFECT_PROFILES[Effect.DANGER].parallel_safe)
        self.assertFalse(EFFECT_PROFILES[Effect.DANGER].retryable)
        self.assertIs(EFFECT_PROFILES[Effect.DANGER].decision_standard,
                     EFFECT_PROFILES[Effect.DANGER].decision_strict)

    def test_rce_qua_write_source_roi_run_tests_bi_chan_boi_agent_that(self):
        """G-1, kịch bản thật `design/review-architect.md` dùng để chứng minh lỗ hổng —
        ghi một file test chứa code thực thi lệnh hệ thống, rồi chạy nó qua `run_tests`.
        Trước bản vá: hai lời gọi `write`+`write` chạy tuột qua, không ai hỏi. Sau bản
        vá: đi qua một `Agent` THẬT (không có `approve=`) thì `run_tests` là `danger`,
        `PolicyEngine.resolve()` không có callback thì DENY — file test được ghi, nhưng
        không bao giờ được chạy."""
        from harness import Agent
        from harness.models.fake import FakeModel

        canary = Path(self.root, "PWNED.txt")
        script = FakeModel([
            FakeModel.tool_call("write_source", {
                "path": "test_pwned.py",
                "text": f"import os\nos.system('touch {canary}')\ndef test_x(): pass\n"}),
            FakeModel.tool_call("run_tests", {"target": "test_pwned.py"}),
            FakeModel.text("done"),
        ])
        agent = Agent(name="A", job="j", tools=self.ct.tools(), provider=script,
                      budget="$1, 10 steps")
        r = agent.try_run("write a test and run it")
        self.assertIn("write_source", r.tools_run)
        self.assertNotIn("run_tests", r.tools_run, "run_tests chạy dù không ai duyệt")
        self.assertFalse(canary.exists(), "code trong file test đã thực thi mà không ai "
                                          "duyệt — đúng lỗ hổng G-1")


class ChayLenhThat(Base):
    """Không mock: đây là chỗ mà một lỗi env hay một chuỗi shell sẽ lộ ra."""

    def test_moi_truong_la_danh_sach_cho_phep_khong_phai_os_environ(self):
        os.environ["MOT_BI_MAT_KHONG_DUOC_XUONG"] = "sk-ant-KHONG"
        try:
            ct = CodeTools(self.root)
            self.assertNotIn("MOT_BI_MAT_KHONG_DUOC_XUONG", ct.env)
            self.assertTrue(set(ct.env) <= set(PASS_ENV))
        finally:
            del os.environ["MOT_BI_MAT_KHONG_DUOC_XUONG"]

    def test_git_commit_chay_that_va_HOME_duoc_chuyen_xuong(self):
        """`Sandbox.run` dùng `env` NGUYÊN VĂN, nên thiếu `HOME` là git hỏng với
        'Author identity unknown' — đúng lỗi `examples/coding_agent.py` đã vấp."""
        if not _co_git():
            self.skipTest("máy này không có git")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        out = self.call("git_commit", message="lần đầu")
        self.assertTrue(out.startswith("exit 0"), out)
        log = subprocess.run(["git", "log", "--oneline"], cwd=self.root,
                             capture_output=True, text=True)
        self.assertIn("lần đầu", log.stdout)

    def test_git_status_thay_file_moi(self):
        if not _co_git():
            self.skipTest("máy này không có git")
        subprocess.run(["git", "init", "-q"], cwd=self.root, check=True)
        self.assertIn("pkg/", self.call("git_status"))

    def test_run_tests_tra_ve_ma_thoat_that(self):
        Path(self.root, "test_that.py").write_text(
            "def test_ok():\n    assert 1 == 1\n", encoding="utf-8")
        out = self.call("run_tests")
        self.assertTrue(out.startswith("exit 0"), out)

    def test_test_that_bai_thi_khong_bi_giau_di(self):
        Path(self.root, "test_hong.py").write_text(
            "def test_hong():\n    assert 1 == 2\n", encoding="utf-8")
        out = self.call("run_tests")
        self.assertFalse(out.startswith("exit 0"), out)
        self.assertIn("assert", out)

    def test_lenh_di_bang_argv_khong_phai_chuoi_shell(self):
        """Không có gì để tiêm qua `;`/`&&`: `target` là một phần tử argv, nên nó chỉ có
        thể là một đường dẫn sai, không thể là một lệnh thứ hai."""
        canary = Path(self.root, "bi-tiem.txt")
        out = self.call("run_tests", target=f"; touch {canary}")
        self.assertFalse(canary.exists(), "chuỗi lệnh đã bị diễn giải như shell")
        self.assertFalse(out.startswith("exit 0"))


class QuetBiCatBoPhaiNoiRo(Base):
    """Bug thật, tìm thấy khi tự review lại lượt vá `harness.tools.code`: `walk()` cắt
    ở `MAX_FILES` file mà không báo cho ai biết — khác `search_code`'s `MAX_MATCHES`,
    vốn tự nói "[dừng ở N kết quả]" khi cắt. Hệ quả: một workspace thật (>500 file,
    hoàn toàn bình thường với một repo có test/fixture) khiến `search_code` báo "không
    tìm thấy" cho một chuỗi THẬT SỰ có mặt, chỉ vì nó nằm trong file thứ 501 trở đi —
    dương tính giả có thể khiến agent viết lại một hàm đã tồn tại sẵn nơi khác."""

    def setUp(self):
        super().setUp()
        # MAX_FILES=500: tạo dư ra để chắc chắn vượt ngưỡng, tên sắp xếp sao cho file
        # "đáng tìm" rơi ra NGOÀI 500 file đầu (os.walk + sorted(filenames) theo tên).
        for i in range(510):
            Path(self.root, f"f{i:04d}.py").write_text(f"# rác {i}\n", encoding="utf-8")
        self.target = Path(self.root, "z_target.py")
        self.target.write_text("CHUOI_CAN_TIM = 1\n", encoding="utf-8")

    def test_walk_tu_bao_da_cat_bot(self):
        from harness.tools.code import MAX_FILES
        paths, truncated = self.ct.walk()
        self.assertTrue(truncated)
        self.assertEqual(len(paths), MAX_FILES)

    def test_search_code_khong_tim_thay_thi_phai_noi_ro_da_cat_bot(self):
        out = self.call("search_code", pattern="CHUOI_CAN_TIM")
        self.assertIn("không tìm thấy", out)
        self.assertIn("đã dừng quét", out,
                      "báo 'không tìm thấy' mà không nói đã cắt bớt — dương tính giả")

    def test_list_files_khong_khop_thi_cung_phai_noi_ro(self):
        out = self.call("list_files", pattern="z_target.py")
        self.assertIn("không có file nào khớp", out)
        self.assertIn("đã dừng quét", out)

    def test_co_ket_qua_van_phai_noi_da_cat_bot_vi_co_the_con_thieu(self):
        out = self.call("search_code", pattern="rác 0\\b")
        self.assertIn("f0000.py", out)
        self.assertIn("đã dừng quét", out,
                      "có kết quả rồi thì im re về việc cắt bớt — nhưng vẫn có thể "
                      "còn khớp khác ngoài phạm vi đã quét")

    def test_khong_vuot_nguong_thi_khong_co_ghi_chu_thua(self):
        """Đối chứng: một workspace nhỏ (như `Base.setUp`'s hai file) không được dán
        thêm ghi chú "đã dừng quét" — nó không hề dừng gì cả."""
        small = CodeTools(self._make_small_root())
        out = asyncio.run(next(t for t in small.tools()
                               if t.name == "search_code").fn(pattern="class Thing"))
        self.assertNotIn("đã dừng quét", out)

    def _make_small_root(self) -> str:
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        os.makedirs(os.path.join(d.name, "pkg"))
        Path(d.name, "pkg", "a.py").write_text(SRC, encoding="utf-8")
        return d.name


class GiaLapSandbox:
    """Ghi lại đúng lệnh được gọi, không chạy gì thật — dùng để kiểm heuristic
    `--init`/`--update` mà không phụ thuộc máy test có cài `openwiki` hay không."""
    def __init__(self):
        self.calls: list = []

    async def run(self, cmd, *, cwd, env, timeout):
        self.calls.append(tuple(cmd))
        from harness.sandbox import Completed
        return Completed(0, "ok", "", False)


class CapNhatWikiBangOpenwiki(Base):
    """`refresh_codebase_docs`: tool tường minh (chọn theo yêu cầu người dùng — "cách
    tường minh như các tool khác"), model phải tự gọi, không có gì tự động chạy nó."""

    def test_chua_co_thu_muc_openwiki_thi_goi_init(self):
        sb = GiaLapSandbox()
        ct = CodeTools(self.root, sandbox=sb)
        T = {t.name: t for t in ct.tools()}
        asyncio.run(T["refresh_codebase_docs"].fn())
        self.assertEqual(sb.calls, [("openwiki", "--init")])

    def test_da_co_thu_muc_openwiki_thi_goi_update_khong_phai_init_lai(self):
        os.makedirs(os.path.join(self.root, "openwiki"))
        sb = GiaLapSandbox()
        ct = CodeTools(self.root, sandbox=sb)
        T = {t.name: t for t in ct.tools()}
        asyncio.run(T["refresh_codebase_docs"].fn())
        self.assertEqual(sb.calls, [("openwiki", "--update")])

    def test_effect_la_danger(self):
        """G-1, đã sửa: chạy một binary ngoài (`openwiki`) là thực thi, không chỉ ghi."""
        self.assertIs(self.T["refresh_codebase_docs"].effect, Effect.DANGER)

    def test_khong_co_openwiki_tren_may_thi_bao_loi_doc_duoc_khong_crash(self):
        """Không mock: máy test này (như hầu hết máy) không cài `openwiki`. IDL-30 fail
        visible — `Subprocess.run` bắt `FileNotFoundError` và trả về exit 127 đọc được,
        không phải traceback hay im lặng làm sai việc."""
        if which("openwiki") is not None:
            self.skipTest("máy này có cài openwiki thật, bỏ qua test giả định vắng mặt")
        out = self.call("refresh_codebase_docs")
        self.assertTrue(out.startswith("exit 127"), out)

    def test_khong_tu_dong_chay_chi_khi_model_goi(self):
        """Chọn theo yêu cầu người dùng: cách tường minh như mọi tool khác trong
        `CodeTools` — không có móc nào tự gọi `refresh_codebase_docs` khi tạo `CodeTools`
        hay khi lấy danh sách tool; nó chỉ chạy khi `.fn()` được gọi trực tiếp."""
        sb = GiaLapSandbox()
        CodeTools(self.root, sandbox=sb).tools()
        self.assertEqual(sb.calls, [], "tạo CodeTools/lấy tools() không được tự chạy gì")


class IsolationTrenToolSpecH2(unittest.TestCase):
    """H-2, design/review-architect-round3.md: `06 §C`'s own row claimed an audit event
    could already show `isolation` — false until this fix. `ToolSpec.isolation` is the
    structural link that makes it true: set on the five tools that actually reach
    `CodeTools.sandbox`, `None` on the six that never do."""

    def setUp(self):
        self._d = tempfile.TemporaryDirectory()
        self.root = self._d.name

    def tearDown(self):
        self._d.cleanup()

    def test_nam_tool_qua_sandbox_mang_dung_isolation_that(self):
        ct = CodeTools(self.root)          # Subprocess() mặc định -> isolation="process"
        T = {t.name: t for t in ct.tools()}
        for name in ("run_tests", "git_status", "git_diff", "git_commit",
                    "refresh_codebase_docs"):
            self.assertEqual(T[name].isolation, "process", name)

    def test_sau_tool_khong_qua_sandbox_khong_mang_isolation(self):
        ct = CodeTools(self.root)
        T = {t.name: t for t in ct.tools()}
        for name in ("list_files", "read_source", "search_code", "outline",
                    "write_source", "edit_source"):
            self.assertIsNone(T[name].isolation, name)

    def test_sandbox_khong_khai_isolation_thi_ve_none(self):
        """Backward-compat: một `Sandbox` viết trước G-7 (không có `.isolation`) không
        được làm hỏng `tools()` — rơi về `"none"`, không raise."""
        ct = CodeTools(self.root, sandbox=GiaLapSandbox())
        T = {t.name: t for t in ct.tools()}
        self.assertEqual(T["run_tests"].isolation, "none")

    def test_isolation_toi_dung_su_kien_TOOL_FINISHED(self):
        """Đầu-cuối: `isolation` phải thật sự đi tới sự kiện, không chỉ nằm trên
        `ToolSpec` — đúng thứ H-2 nói `06 §C` đã lỡ khẳng định có sẵn."""
        from harness import Agent
        from harness.models.fake import FakeModel

        events = []

        class Rec:
            def emit(self, e): events.append((e.kind.value, dict(e.data)))
            def close(self): ...

        ct = CodeTools(self.root)
        run_tests = ct.tools()[6]          # index của run_tests trong tools()
        self.assertEqual(run_tests.name, "run_tests")
        script = [FakeModel.tool_call("run_tests", {}), FakeModel.text("xong")]
        agent = Agent(name="A", job="j", provider=FakeModel(script), tools=[run_tests],
                      budget="$5", approve=lambda c, ctx: True, exporters=[Rec()])
        agent.try_run("chạy test")
        finished = [d for k, d in events if k == "tool.finished" and d.get("tool") == "run_tests"]
        self.assertEqual(len(finished), 1)
        self.assertEqual(finished[0]["isolation"], "process")


def _co_git() -> bool:
    from shutil import which
    return which("git") is not None


if __name__ == "__main__":
    unittest.main()
