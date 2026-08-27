"""Trợ lý chăm sóc khách hàng — một agent đa chức năng.

Chạy:  python3 examples/support_agent.py

Ví dụ này dùng FakeModel (không cần API key, không tốn tiền). Để chạy thật, xoá
`provider=...` và chạy `harness setup` trước — Agent sẽ tự dùng AnthropicProvider.
"""
from __future__ import annotations

import dataclasses
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness import Agent, Secret, tool
from harness.memory import SqliteStore
from harness.tools.calc import calculate

# ─────────────────────────────────────────────────────────────────────────────
# 1. Dữ liệu giả lập  (thật ra là DB / API của bạn)
# ─────────────────────────────────────────────────────────────────────────────
ORDERS = {
    "A-4471": {"khach": "Lan",  "mon": "Bàn phím cơ", "gia": 1_290_000,
               "trang_thai": "đã giao", "ngay_giao": "2026-08-20"},
    "A-4472": {"khach": "Minh", "mon": "Chuột không dây", "gia": 450_000,
               "trang_thai": "đang giao", "ngay_giao": None},
}
DB = SqliteStore(Path(tempfile.mkdtemp()) / "notes.db", agent="support")
API_KEY = Secret("sk-live-PAYMENTS-abc123", name="payment_key")

# ─────────────────────────────────────────────────────────────────────────────
# 2. Công cụ — mỗi cái khai báo nó làm gì với thế giới
# ─────────────────────────────────────────────────────────────────────────────

@tool(effect="read")                       # chỉ nhìn → chạy song song, tự động cho phép
def tim_don_hang(ma_don: str) -> dict:
    """Tra cứu một đơn hàng theo mã."""
    return ORDERS.get(ma_don, {"loi": f"không có đơn {ma_don}"})


@tool(effect="read")
async def doc_ghi_chu(tu_khoa: str) -> str:
    """Đọc lại các ghi chú đã lưu về một khách hàng."""
    hits = await DB.search(tu_khoa, limit=3)
    return "\n".join(f"- {m.key}: {m.value}" for m in hits) or "chưa có ghi chú nào"


@tool(effect="write")                      # sửa được → chạy tuần tự, không tự retry
async def luu_ghi_chu(ma_don: str, noi_dung: str) -> str:
    """Lưu một ghi chú về đơn hàng để lần sau đọc lại."""
    await DB.put(f"don_{ma_don}", noi_dung)
    return f"đã lưu ghi chú cho {ma_don}"


@tool(effect="danger")                     # không undo được → LUÔN hỏi trước khi chạy
def hoan_tien(ma_don: str, so_tien: int) -> str:
    """Hoàn tiền cho khách. Không thể huỷ sau khi đã chạy."""
    with API_KEY.reveal() as key:          # bí mật chỉ mở trong khối này
        _ = key                            # gọi cổng thanh toán ở đây
    return f"đã hoàn {so_tien:,}đ cho đơn {ma_don}"


# ─────────────────────────────────────────────────────────────────────────────
# 3. Người phê duyệt — mọi tool `danger` đều đi qua đây
# ─────────────────────────────────────────────────────────────────────────────
def hoi_y_kien(call, ctx) -> bool:
    print(f"    ⚠  Xin phép chạy {call.name}({dict(call.arguments)})")
    ok = int(call.arguments.get("so_tien", 0)) <= 1_000_000     # tự động duyệt dưới 1tr
    print(f"    {'✓ đồng ý' if ok else '✗ từ chối — vượt hạn mức'}")
    return ok


# ─────────────────────────────────────────────────────────────────────────────
# 4. Subagent — việc đọc nhiều giao cho model rẻ, ngân sách riêng
# ─────────────────────────────────────────────────────────────────────────────
@tool(effect="read")
def doc_chinh_sach(muc: str) -> str:
    """Đọc một mục trong chính sách đổi trả."""
    return ("Đổi trả trong 7 ngày kể từ ngày giao. Hàng phải còn nguyên hộp. "
            "Hoàn tiền tối đa 100% giá trị đơn.")


chuyen_gia_chinh_sach = Agent(
    name="Chuyên gia chính sách",
    job="Đọc chính sách và trả lời ngắn gọn, chỉ dựa trên văn bản.",
    tools=[doc_chinh_sach],
    model="claude-haiku-4-5",              # model rẻ cho việc đọc
    budget="$0.01",
)


# ─────────────────────────────────────────────────────────────────────────────
# 5. Kiểu dữ liệu trả về — câu trả lời được kiểm tra, không phải chuỗi
# ─────────────────────────────────────────────────────────────────────────────
@dataclasses.dataclass
class KetLuan:
    ma_don: str
    duoc_hoan_tien: bool
    so_tien: int
    ly_do: str


# ─────────────────────────────────────────────────────────────────────────────
# 6. Agent chính
# ─────────────────────────────────────────────────────────────────────────────
def tao_agent(provider=None, transcript=None) -> Agent:
    return Agent(
        name="Trợ lý CSKH",
        job=(
            "Giúp khách hàng về đơn hàng. Luôn tra cứu đơn trước khi trả lời. "
            "Kiểm tra chính sách đổi trả trước khi hoàn tiền. "
            "Không bao giờ đoán trạng thái đơn hàng."
        ),
        tools=[
            tim_don_hang,
            doc_ghi_chu,
            luu_ghi_chu,
            calculate,                              # tool có sẵn
            chuyen_gia_chinh_sach.as_tool(),        # subagent thành một tool
            hoan_tien,
        ],
        returns=KetLuan,                            # câu trả lời có kiểu, được validate
        budget="$0.20, 15 steps, 2m",               # trần cứng, kiểm tra TRƯỚC mỗi lần gọi
        approve=hoi_y_kien,                         # mọi `danger` phải qua đây
        transcript=transcript,                      # ghi lại mọi quyết định
        provider=provider,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 7. Chạy
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    from harness.models.fake import FakeModel

    kich_ban = FakeModel([
        FakeModel.tool_call("tim_don_hang", {"ma_don": "A-4471"}, call_id="c1"),
        FakeModel.tool_call("ask_chuyen_gia_chinh_sach",
                            {"task": "Đơn giao 20/08 còn được đổi trả không?"}, call_id="c2"),
        FakeModel.tool_call("calculate", {"expression": "1290000 * 1.0"}, call_id="c3"),
        FakeModel.tool_call("hoan_tien", {"ma_don": "A-4471", "so_tien": 1290000}, call_id="c4"),
        FakeModel.tool_call("luu_ghi_chu",
                            {"ma_don": "A-4471", "noi_dung": "Đã hoàn tiền đầy đủ"}, call_id="c5"),
        FakeModel.text("Đã hoàn 1.290.000đ cho đơn A-4471 vì còn trong hạn 7 ngày."),
    ])

    ts = Path(tempfile.mkdtemp()) / "run.jsonl"
    agent = tao_agent(provider=kich_ban, transcript=ts)

    print("Công cụ và mức ảnh hưởng đã khai báo:")
    for t in agent.toolset:
        print(f"  {t.name:28} {t.effect.value}")

    print("\nChạy:")
    kq = agent.try_run("Đơn A-4471 giao hôm 20/08 bị lỗi phím, tôi muốn hoàn tiền.")

    print(f"\nTrả lời : {kq.text}")
    print(f"Kết thúc: {kq.stop_reason.value}  ·  {kq.steps} bước  ·  {kq.cost}")

    print("\nNhật ký (mọi quyết định đều được ghi):")
    from harness.observe.transcript import read
    for e in read(ts):
        if e["kind"] == "policy.decided":
            print(f"  {e['data']['tool']:28} → {e['data']['verdict']}")

    print("\nBí mật KHÔNG lọt vào nhật ký:",
          "sk-live-PAYMENTS" not in ts.read_text())


if __name__ == "__main__":
    main()
