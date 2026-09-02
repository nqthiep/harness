"""Advisor pattern — model rẻ xử lý việc thường ngày, model mạnh làm cố vấn khi cần.

Chạy:  python3 examples/advisor_pattern.py

Hai lớp, cộng dồn:

  1. Advisor là một SUBAGENT bình thường (`.as_tool()`) — model rẻ ("Worker") tự quyết
     định KHI NÀO cần hỏi, qua đúng cơ chế `job=` đã có sẵn (docs/07-cost.md §4). Không
     cần cơ chế mới trong harness: đây là điều đã "expressible today, with no new
     machinery" (docs/07-cost.md §6, về reflection/self-critique loop nói chung).

  2. `RequireBeforePolicy` (policy/builtin.py) làm cho việc "hỏi trước" KHÔNG PHẢI chỉ là
     lời nhắc trong prompt (model có thể quên) mà là một CHẶN CỨNG có cấu trúc: tool
     `xoa_du_lieu` (effect=danger) bị DENY ngay ở tầng policy nếu `tu_van_advisor` chưa
     từng chạy trong run này — bất kể `approve=` callback có đồng ý hay không.

     Quan trọng: advisor KHÔNG BAO GIỜ tự cấp quyền. `RequireBeforePolicy` chỉ có thể
     THẮT chặt thêm (P-2, mọi Policy chỉ được restrict) — nó biến một lần gọi "chưa hỏi"
     thành DENY sớm hơn, không biến việc "đã hỏi" thành ALLOW. Quyền ALLOW thật vẫn phải
     tới từ `approve=` (người/hệ thống vận hành), đúng bất biến D-1
     (design/00-foundation.md §4.2): `Actor` cố ý KHÔNG có biến thể `Model` — một model,
     dù mạnh tới đâu, không bao giờ là bên cấp một `Decision`. Đây chính là chỗ dự án gốc
     nói "agno sai".

Ví dụ này dùng FakeModel (không cần API key, không tốn tiền). Để chạy thật, xoá
`provider=...` và chạy `harness setup` trước.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness import Agent, tool
from harness.policy.builtin import RequireBeforePolicy

# ─────────────────────────────────────────────────────────────────────────────
# 1. Advisor — model MẠNH, chỉ đưa ý kiến, không bao giờ tự hành động
# ─────────────────────────────────────────────────────────────────────────────
def tao_advisor(provider=None) -> Agent:
    return Agent(
        name="Advisor",
        job=(
            "Phân tích kỹ một tình huống khó và đưa ra khuyến nghị rõ ràng, có lý do. "
            "Bạn KHÔNG có công cụ nào để tự hành động — chỉ trả lời bằng phân tích."
        ),
        model="claude-opus-5",
        effort="high",              # nghĩ sâu hơn — đây là lúc nó xứng đáng
        budget="$0.05",             # ngân sách riêng, bị cha giới hạn thêm (S-13)
        provider=provider,
    )


def tao_tu_van_tool(advisor: Agent):
    @tool(effect="read")
    def tu_van_advisor(tinh_huong: str) -> str:
        """Hỏi ý kiến advisor trước một quyết định khó hoặc một hành động không thể
        hoàn tác."""
        return advisor.run(tinh_huong).text
    return tu_van_advisor


# ─────────────────────────────────────────────────────────────────────────────
# 2. Việc thường ngày — Worker (model rẻ) tự làm, không cần hỏi ai
# ─────────────────────────────────────────────────────────────────────────────
DU_LIEU = {"khach_A": {"don": 3, "tong_chi": 4_500_000}}


@tool(effect="read")
def tra_cuu_khach(ma_khach: str) -> dict:
    """Tra cứu thông tin một khách hàng."""
    return DU_LIEU.get(ma_khach, {"loi": "không tìm thấy"})


@tool(effect="danger")
def xoa_du_lieu(ma_khach: str) -> str:
    """Xoá vĩnh viễn dữ liệu một khách hàng — KHÔNG thể hoàn tác."""
    DU_LIEU.pop(ma_khach, None)
    return f"đã xoá {ma_khach}"


def duyet(call, ctx) -> bool:
    """Người vận hành thật sẽ đứng ở đây — ví dụ này tự động đồng ý để minh hoạ, NHƯNG
    RequireBeforePolicy vẫn chặn trước khi lời duyệt này có cơ hội chạy, nếu chưa tư vấn."""
    print(f"    (approve= được hỏi cho {call.name} — sẽ không tới đây nếu chưa tư vấn)")
    return True


def tao_worker(provider=None, advisor_provider=None) -> Agent:
    tu_van_advisor = tao_tu_van_tool(tao_advisor(provider=advisor_provider))
    return Agent(
        name="Worker",
        job=(
            "Xử lý yêu cầu khách hàng. Việc tra cứu thì tự làm. "
            "Trước một hành động KHÔNG THỂ HOÀN TÁC (xoá dữ liệu), luôn gọi "
            "tu_van_advisor trước để xin ý kiến."
        ),
        model="claude-haiku-4-5",       # rẻ — việc thường ngày không cần model mạnh
        tools=[tra_cuu_khach, tu_van_advisor, xoa_du_lieu],
        budget="$0.05, 10 steps",
        approve=duyet,
        # Chặn cứng, không dựa vào việc model có NHỚ tự hỏi trước hay không:
        policies=[RequireBeforePolicy(
            tool="xoa_du_lieu", requires="tu_van_advisor",
            reason="xoa_du_lieu không thể hoàn tác — phải tu_van_advisor trước")],
        provider=provider,
    )


# ─────────────────────────────────────────────────────────────────────────────
# 3. Chạy — hai kịch bản: quên hỏi (bị chặn) và hỏi trước (qua được)
# ─────────────────────────────────────────────────────────────────────────────
def main() -> None:
    from harness.models.fake import FakeModel

    # Advisor không bao giờ thật sự được gọi ở Kịch bản 1 (model thử xoá luôn), nhưng
    # vẫn cần MỘT provider nào đó để dựng Agent (chưa gọi tới thì chưa tốn gì) — dùng
    # FakeModel cho cả hai kịch bản để ví dụ chạy được không cần API key.
    y_kien_advisor = FakeModel([FakeModel.text(
        "Khuyến nghị: có xác nhận bằng email từ khách, đúng quy trình — nên xoá.")])

    print("── Kịch bản 1: model THỬ xoá luôn, không hỏi trước ──")
    quen_hoi = FakeModel([
        FakeModel.tool_call("xoa_du_lieu", {"ma_khach": "khach_A"}),
        FakeModel.text("Đã xử lý xong."),
    ])
    w1 = tao_worker(provider=quen_hoi, advisor_provider=y_kien_advisor)
    r1 = w1.try_run("Xoá luôn dữ liệu khách_A giúp tôi.")
    print(f"  kết quả: {r1.stop_reason.value}  ·  đã chạy: {r1.tools_run}")
    print(f"  dữ liệu còn nguyên: {'khach_A' in DU_LIEU}\n")

    print("── Kịch bản 2: model hỏi advisor trước, rồi mới xoá ──")
    hoi_truoc = FakeModel([
        FakeModel.tool_call("tu_van_advisor",
                            {"tinh_huong": "Khách_A yêu cầu xoá dữ liệu vĩnh viễn, đã "
                                          "xác nhận qua email. Có nên xoá không?"}),
        FakeModel.tool_call("xoa_du_lieu", {"ma_khach": "khach_A"}),
        FakeModel.text("Đã tư vấn và xoá xong."),
    ])
    w2 = tao_worker(provider=hoi_truoc, advisor_provider=y_kien_advisor)
    r2 = w2.try_run("Xoá luôn dữ liệu khách_A giúp tôi, khách đã xác nhận qua email.")
    print(f"  kết quả: {r2.stop_reason.value}  ·  đã chạy: {r2.tools_run}")
    print(f"  dữ liệu còn nguyên: {'khach_A' in DU_LIEU}")

    print("\nAdvisor dùng model mạnh + effort cao, chỉ khi thật sự cần — Worker dùng "
          "model rẻ cho phần còn lại. Việc 'phải hỏi trước' là một Policy, không phải "
          "một lời nhắc trong prompt mà model có thể bỏ qua.")


if __name__ == "__main__":
    main()
