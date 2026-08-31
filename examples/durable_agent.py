"""Agent(durable=True) — một Agent, một API, engine LangGraph giấu ở dưới.

Chạy:  python3 examples/durable_agent.py

Không cần API key: mặc định dùng model giả. Có key thì tự dùng model thật — CÙNG một
Agent, không đổi dòng code nào (dùng `harness.models.anthropic.AnthropicProvider`, y hệt
backend cổ điển — xem docs/02-architecture.md §3.1: `durable=True` gọi model qua ĐÚNG MỘT
seam, không phải một thư viện LangChain riêng).

So với `examples/graph_agent.py`/`langgraph_quickstart.py` (LangGraph lộ ra: `HumanMessage`,
`graph.invoke()`, `thread_id` trong config) — ở đây không có gì thuộc LangGraph lọt ra
ngoài: `run()`/`try_run()` nhận `str`, trả `Result`, giống hệt agent không durable.
"""
import os
import sys
import tempfile

sys.path.insert(0, "src")

from harness import Agent, tool

DON = {"A-4471": {"mon": "Bàn phím cơ", "trang_thai": "đã giao"}}


@tool(effect="read")
def tim_don(ma: str) -> dict:
    """Tra cứu một đơn hàng theo mã."""
    return DON.get(ma, {"loi": "không tìm thấy"})


def lay_provider(kich_ban):
    if os.environ.get("ANTHROPIC_API_KEY"):
        return None                      # None -> Agent tự dựng AnthropicProvider() thật
    from harness.models.fake import FakeModel
    return FakeModel(kich_ban)


# `checkpoint=` trỏ vào một thư mục tạm ở đây chỉ để ví dụ này không để lại file sau khi
# chạy xong — bỏ hẳn tham số này (mặc định `None`) là cách dùng thật: harness tự tạo
# `.harness/checkpoints/<ten-agent>.sqlite3`, không cần cấu hình gì thêm.
tmp_db = os.path.join(tempfile.mkdtemp(), "cskh.sqlite3")

print("═" * 68)
print("LƯỢT 1 — quy trình bình thường")
print("═" * 68)

from harness.models.fake import FakeModel  # noqa: E402

agent = Agent(
    name="CSKH", job="trả lời về đơn hàng", tools=[tim_don],
    provider=lay_provider([FakeModel.tool_call("tim_don", {"ma": "A-4471"}),
                           FakeModel.text("Đơn A-4471: Bàn phím cơ, đã giao.")]),
    durable=True, checkpoint=tmp_db,
    session_id="khach-42",               # cuộc hội thoại để kết nối lại sau này
    allowed_hosts=None,
)
r1 = agent.run("đơn A-4471 sao rồi")
print(f"  {r1.text}")
print(f"  đã chạy: {r1.tools_run}, tốn {r1.cost}")

print()
print("═" * 68)
print("« TIẾN TRÌNH DỪNG Ở ĐÂY » — coi như tiến trình Python đã khởi động lại")
print("═" * 68)
print("  Không có gì trong Python sống sót — Agent bên dưới là một OBJECT MỚI,")
print("  chỉ trỏ lại đúng file checkpoint và đúng session_id cũ.")
print()

agent_moi = Agent(
    name="CSKH", job="trả lời về đơn hàng", tools=[tim_don],
    provider=lay_provider([FakeModel.text("vâng, đã giao hôm qua rồi ạ.")]),
    durable=True, checkpoint=tmp_db,
    session_id="khach-42",               # CÙNG session_id -> nối lại đúng hội thoại
    allowed_hosts=None,
)
r2 = agent_moi.run("chắc chắn chưa vậy?")
print(f"  {r2.text}")
print(f"  cuộc hội thoại có {len(r2.messages)} message — gồm cả lượt 1")

print()
print("Những gì durable=True CHƯA hỗ trợ (docs/03-public-api.md §3.5, các N- đã ghi lại):")
print("  returns=, .chat(), .resume(transcript), on_delta=, timeout riêng từng tool.")
print("  Mỗi cái từ chối RÕ RÀNG lúc gọi — không âm thầm bỏ qua.")
