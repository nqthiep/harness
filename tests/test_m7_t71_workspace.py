"""M7/T-7.1 (docs/17-research-alignment.md): workspace root confinement.

Test đúng bốn kịch bản T-7.1 tự đặt ra: "`../../etc/passwd`, symlink, đường dẫn tuyệt
đối, `..` mã hoá URL" — cộng đối chứng (đường dẫn hợp lệ phải đi qua được) và một lỗi
THẬT tìm thấy khi viết `confine()`: `Path(root) / path` của pathlib âm thầm bỏ `root`
khi `path` là tuyệt đối (`Path("/root") / "/etc/passwd" == Path("/etc/passwd")`) — sửa
bằng cách chặn tuyệt đối TRƯỚC khi join, không dựa vào containment check để bắt nó.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "src")

from harness.workspace import WorkspaceEscapeError, confine


class ConfineHopLe(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_duong_dan_tuong_doi_hop_le_di_qua(self):
        p = confine(self.root, "notes/todo.txt")
        self.assertEqual(p, Path(self.root).resolve() / "notes" / "todo.txt")

    def test_ten_file_don_gian_di_qua(self):
        p = confine(self.root, "a.txt")
        self.assertTrue(str(p).startswith(str(Path(self.root).resolve())))

    def test_ten_file_co_dau_phan_tram_that_di_qua_G16(self):
        """G-16, design/review-architect.md: `%` là một ký tự tên file BÌNH THƯỜNG
        (`"50% off.txt"`) — không phải mọi `%` đều là percent-encoding cần chặn."""
        p = confine(self.root, "50% off.txt")
        self.assertEqual(p, Path(self.root).resolve() / "50% off.txt")

    def test_dau_phan_tram_decode_ra_chinh_no_di_qua_G16(self):
        """`%25` decode ra `%` — không phải `.`/`/`/`\\`, không có gì nguy hiểm để chặn."""
        p = confine(self.root, "100%25.txt")
        self.assertEqual(p, Path(self.root).resolve() / "100%25.txt")


class ConfineTuChoiThoatRa(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_dot_dot_thoat_ra_ngoai_bi_tu_choi(self):
        with self.assertRaises(WorkspaceEscapeError):
            confine(self.root, "../../etc/passwd")

    def test_duong_dan_tuyet_doi_bi_tu_choi(self):
        with self.assertRaises(WorkspaceEscapeError):
            confine(self.root, "/etc/passwd")

    def test_duong_dan_tuyet_doi_khong_am_tham_bo_qua_root(self):
        """Đúng lỗi pathlib gốc tìm thấy khi viết hàm này: `Path(root) / "/etc/passwd"`
        tự nhiên trả về `/etc/passwd`, KHÔNG lỗi gì — containment check ở CUỐI hàm sẽ
        vẫn bắt được nếu chỉ dựa vào nó, nhưng test này khẳng định trực tiếp rằng có một
        chặn RIÊNG trước khi join, không dựa hoàn toàn vào containment check phía sau."""
        try:
            confine(self.root, "/etc/passwd")
            self.fail("phải raise WorkspaceEscapeError")
        except WorkspaceEscapeError as exc:
            self.assertIn("absolute", str(exc).lower())

    def test_windows_duong_dan_tuyet_doi_bi_tu_choi(self):
        with self.assertRaises(WorkspaceEscapeError):
            confine(self.root, r"C:\Windows\System32\config")

    def test_unc_duong_dan_bi_tu_choi(self):
        with self.assertRaises(WorkspaceEscapeError):
            confine(self.root, r"\\server\share\file")

    def test_url_encoded_dot_dot_bi_tu_choi(self):
        with self.assertRaises(WorkspaceEscapeError):
            confine(self.root, "%2e%2e/%2e%2e/etc/passwd")

    def test_url_encoded_dot_dot_chu_hoa_cung_bi_tu_choi(self):
        """G-16: `_SUSPICIOUS_PERCENT` phải không phân biệt hoa/thường — một decoder
        URL thật chấp nhận cả hai."""
        with self.assertRaises(WorkspaceEscapeError):
            confine(self.root, "%2E%2E/%2E%2E/etc/passwd")

    def test_url_encoded_slash_bi_tu_choi(self):
        with self.assertRaises(WorkspaceEscapeError):
            confine(self.root, "a%2fb")

    def test_url_encoded_backslash_bi_tu_choi(self):
        with self.assertRaises(WorkspaceEscapeError):
            confine(self.root, "a%5cb")

    def test_null_byte_bi_tu_choi(self):
        with self.assertRaises(WorkspaceEscapeError):
            confine(self.root, "a.txt\x00.jpg")

    def test_rong_bi_tu_choi(self):
        with self.assertRaises(WorkspaceEscapeError):
            confine(self.root, "")

    def test_symlink_thoat_ra_ngoai_bi_tu_choi(self):
        """Symlink NẰM TRONG workspace nhưng TRỎ RA NGOÀI — containment check phải theo
        symlink rồi mới kiểm, không kiểm chuỗi ký tự đường dẫn."""
        with tempfile.TemporaryDirectory() as outside:
            secret = Path(outside) / "secret.txt"
            secret.write_text("bí mật")
            link = Path(self.root) / "innocent_link.txt"
            os.symlink(secret, link)
            with self.assertRaises(WorkspaceEscapeError):
                confine(self.root, "innocent_link.txt")

    def test_symlink_trong_thu_muc_con_cung_bi_bat(self):
        """Symlink là một THƯ MỤC trỏ ra ngoài, rồi truy cập file bên trong nó."""
        with tempfile.TemporaryDirectory() as outside:
            (Path(outside) / "leaked.txt").write_text("lộ")
            link_dir = Path(self.root) / "linked_dir"
            os.symlink(outside, link_dir, target_is_directory=True)
            with self.assertRaises(WorkspaceEscapeError):
                confine(self.root, "linked_dir/leaked.txt")

    def test_khong_phai_string_bi_tu_choi(self):
        with self.assertRaises(WorkspaceEscapeError):
            confine(self.root, 123)  # type: ignore[arg-type]


class MutationConfineCoTacDung(unittest.TestCase):
    def test_bo_containment_check_thi_test_dotdot_do(self):
        """Mutation: một `confine` giả bỏ qua containment check hoàn toàn (chỉ join,
        không kiểm `relative_to`) — xác nhận test `..` thật sự phụ thuộc vào nhánh đó."""
        def fake_confine(root, path):
            from pathlib import Path as P
            return (P(root).resolve() / path).resolve()   # KHÔNG kiểm containment

        with tempfile.TemporaryDirectory() as root:
            # Với mutation này, thoát ra ngoài không raise gì cả:
            result = fake_confine(root, "../../etc/passwd")
            self.assertFalse(str(result).startswith(str(Path(root).resolve())),
                            "mutation này PHẢI cho thoát ra ngoài — khác hành vi thật, "
                            "chứng minh test thật phụ thuộc vào containment check")


if __name__ == "__main__":
    unittest.main()
