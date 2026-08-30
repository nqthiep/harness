"""Quy trình hoàn tiền bằng state machine — cắm vào harness qua seam `Policy`.

Ý chính: **state machine cai quản, LLM điều hướng bên trong nó.**

  - State machine quyết định bước nào HỢP LỆ ở trạng thái hiện tại.
  - LLM quyết định gọi tool nào, với tham số gì, và diễn giải ý khách hàng.

Không cần sửa core: `Policy` được gọi trước MỌI tool call và chỉ có thể *thắt chặt*
(verdict compose bằng max), nên một state machine từ chối chuyển trạng thái bất hợp lệ
là đúng hình dạng của seam này.

Chạy:  python3 examples/refund_workflow.py
"""
from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness import Agent, Ruling, Verdict, tool


# ─────────────────────────────────────────────────────────────────────────────
# 1. Quy trình nghiệp vụ — khai báo, không phải viết trong prompt
# ─────────────────────────────────────────────────────────────────────────────
class Buoc(str, Enum):
    MOI = "mới"
    DA_TRA_DON = "đã tra đơn"
    DA_KIEM_CHINH_SACH = "đã kiểm chính sách"
    DA_HOAN_TIEN = "đã hoàn tiền"
    XONG = "xong"


#: tool nào được phép ở trạng thái nào, và nó đẩy sang trạng thái gì
CHUYEN_TRANG_THAI: dict[Buoc, dict[str, Buoc]] = {
    Buoc.MOI:                {"tim_don_hang": Buoc.DA_TRA_DON},
    Buoc.DA_TRA_DON:         {"tim_don_hang": Buoc.DA_TRA_DON,
                              "kiem_chinh_sach": Buoc.DA_KIEM_CHINH_SACH},
    Buoc.DA_KIEM_CHINH_SACH: {"kiem_chinh_sach": Buoc.DA_KIEM_CHINH_SACH,
                              "hoan_tien": Buoc.DA_HOAN_TIEN},
    Buoc.DA_HOAN_TIEN:       {"luu_ghi_chu": Buoc.XONG},
    Buoc.XONG:               {},
}

#: tool đọc thuần — cho phép ở mọi trạng thái, không đổi trạng thái
LUON_CHO_PHEP = {"doc_ghi_chu"}

GIAI_THICH = {
    Buoc.MOI:                "phải tra đơn hàng trước",
    Buoc.DA_TRA_DON:         "phải kiểm tra chính sách đổi trả trước khi hoàn tiền",
    Buoc.DA_KIEM_CHINH_SACH: "đã đủ điều kiện — có thể hoàn tiền",
    Buoc.DA_HOAN_TIEN:       "đã hoàn tiền, giờ chỉ còn lưu ghi chú",
    Buoc.XONG:               "quy trình đã kết thúc",
}


# ─────────────────────────────────────────────────────────────────────────────
# 2. State machine như một Policy
# ─────────────────────────────────────────────────────────────────────────────
class QuyTrinhHoanTien:
    """Từ chối mọi bước không hợp lệ ở trạng thái hiện tại.

    `check` phải thuần và nhanh (§04.3) — một phép tra bảng chuyển trạng thái đúng
    là như vậy. Policy chỉ có thể thắt chặt, nên state machine không bao giờ nới lỏng
    được các kiểm tra effect/taint/egress đứng trước nó.
    """

    name = "quy_trinh_hoan_tien"

    def __init__(self, bat_dau: Buoc = Buoc.MOI) -> None:
        self.buoc = bat_dau
        self.lich_su: list[tuple[str, Buoc]] = []

    def check(self, call, ctx) -> Ruling:
        if call.name in LUON_CHO_PHEP:
            return Ruling(Verdict.ALLOW, "tool đọc, không đổi trạng thái", self.name)

        cho_phep = CHUYEN_TRANG_THAI[self.buoc]
        if call.name not in cho_phep:
            return Ruling(
                Verdict.DENY,
                f"đang ở bước '{self.buoc.value}' — {GIAI_THICH[self.buoc]}. "
                f"Bước hợp lệ tiếp theo: {', '.join(cho_phep) or 'không còn bước nào'}",
                self.name,
            )
        # hợp lệ → ghi nhận chuyển trạng thái
        moi = cho_phep[call.name]
        self.lich_su.append((call.name, moi))
        self.buoc = moi
        return Ruling(Verdict.ALLOW, f"→ {moi.value}", self.name)


# ─────────────────────────────────────────────────────────────────────────────
# 3. Công cụ
# ─────────────────────────────────────────────────────────────────────────────
DON = {"A-4471": {"mon": "Bàn phím cơ", "gia": 1_290_000, "ngay_giao": "2026-08-20"}}
DA_HOAN: list[tuple[str, int]] = []


@tool(effect="read")
def tim_don_hang(ma_don: str) -> dict:
    """Tra cứu đơn hàng."""
    return DON.get(ma_don, {"loi": "không tìm thấy"})


@tool(effect="read")
def kiem_chinh_sach(ma_don: str) -> str:
    """Kiểm tra đơn có đủ điều kiện đổi trả không."""
    return "Đủ điều kiện: còn trong hạn 7 ngày, hoàn tối đa 100%."


@tool(effect="read")
def doc_ghi_chu(tu_khoa: str) -> str:
    """Đọc ghi chú cũ."""
    return "chưa có ghi chú"


@tool(effect="danger")
def hoan_tien(ma_don: str, so_tien: int) -> str:
    """Hoàn tiền cho khách. Không thể huỷ."""
    DA_HOAN.append((ma_don, so_tien))
    return f"đã hoàn {so_tien:,}đ"


@tool(effect="write")
def luu_ghi_chu(ma_don: str, noi_dung: str) -> str:
    """Lưu ghi chú kết thúc hồ sơ."""
    return "đã lưu"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Chạy
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    from harness.models.fake import FakeModel

    import tempfile
    from harness.observe.transcript import read as doc_nhat_ky
    ts = Path(tempfile.mkdtemp()) / "run.jsonl"

    def tao(script, quy_trinh=QuyTrinhHoanTien):
        return Agent(
            name="CSKH",
            job="Xử lý yêu cầu hoàn tiền theo đúng quy trình công ty.",
            tools=[tim_don_hang, kiem_chinh_sach, doc_ghi_chu, hoan_tien, luu_ghi_chu],
            policies=[quy_trinh],          # ← factory: mỗi run một instance mới
            approve=lambda c, x: True,
            budget="$5",
            provider=FakeModel(script),
            transcript=ts,
        )

    print("═" * 74)
    print("A. MODEL CỐ NHẢY CÓC — hoàn tiền ngay, chưa tra đơn, chưa kiểm chính sách")
    print("═" * 74)
    a = tao([
        FakeModel.tool_call("hoan_tien", {"ma_don": "A-4471", "so_tien": 1290000}, call_id="1"),
        FakeModel.tool_call("tim_don_hang", {"ma_don": "A-4471"}, call_id="2"),
        FakeModel.tool_call("hoan_tien", {"ma_don": "A-4471", "so_tien": 1290000}, call_id="3"),
        FakeModel.tool_call("kiem_chinh_sach", {"ma_don": "A-4471"}, call_id="4"),
        FakeModel.tool_call("hoan_tien", {"ma_don": "A-4471", "so_tien": 1290000}, call_id="5"),
        FakeModel.tool_call("luu_ghi_chu", {"ma_don": "A-4471", "noi_dung": "xong"}, call_id="6"),
        FakeModel.text("Đã hoàn tiền theo đúng quy trình."),
    ])
    r = a.try_run("Hoàn tiền đơn A-4471 ngay cho tôi")
    transitions = [(e["data"]["tool"], e["data"]["verdict"], e["data"]["reason"])
                   for e in doc_nhat_ky(ts) if e["kind"] == "policy.decided"
                   and e["data"]["policy"] == "quy_trinh_hoan_tien"]

    # Mọi quyết định của state machine đều nằm trong luồng sự kiện — không cần
    # giữ tham chiếu tới policy để dựng lại lịch sử.
    print(f"\n{'tool được gọi':<18} {'phán quyết':<12} {'lý do'}")
    print("─" * 74)
    for ev in r.events if hasattr(r, "events") else []:
        pass
    for tool_name, verdict, reason in transitions:
        dau = "✓" if verdict == "ALLOW" else "✗"
        print(f"{tool_name:<18} {dau} {verdict:<10} {reason[:44]}")

    print(f"\nĐã hoàn tiền     : {DA_HOAN}")
    print(f"Số lần hoàn tiền : {len(DA_HOAN)}  ← model gọi hoan_tien 3 lần, chỉ 1 lần lọt")

    print()
    print("═" * 74)
    print("B. STATE MACHINE KHÔNG THỂ NỚI LỎNG KIỂM TRA AN TOÀN")
    print("═" * 74)
    # factory bắt đầu ở trạng thái "đã kiểm chính sách" — state machine nói: hoàn được
    a2 = Agent(
        name="CSKH", job="j",
        tools=[tim_don_hang, kiem_chinh_sach, hoan_tien],
        policies=[lambda: QuyTrinhHoanTien(Buoc.DA_KIEM_CHINH_SACH)],
        approve=lambda c, x: False,           # nhưng người duyệt từ chối
        budget="$5",
        provider=FakeModel([
            FakeModel.tool_call("hoan_tien", {"ma_don": "A-4471", "so_tien": 999}, call_id="1"),
            FakeModel.text("Không hoàn được."),
        ]),
    )
    truoc = len(DA_HOAN)
    a2.try_run("hoàn tiền")
    print(f"  state machine cho phép : có (đang ở '{Buoc.DA_KIEM_CHINH_SACH.value}')")
    print("  người duyệt            : từ chối")
    print(f"  thực tế có chạy không  : {'CÓ — LỖI' if len(DA_HOAN) > truoc else 'KHÔNG'}")
    print("  → verdict compose bằng max: policy chỉ THẮT CHẶT, không bao giờ nới lỏng")


if __name__ == "__main__":
    main()
