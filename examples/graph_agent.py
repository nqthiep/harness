"""Cùng một agent, chạy trên LangGraph — vòng 35.

Chạy:  python3 examples/graph_agent.py

Điểm khác biệt duy nhất so với `support_agent.py` là *ai giữ vòng lặp*:
ở đây LangGraph giữ, còn luật thì vẫn y nguyên.  Điều đó không phải lời hứa
suông — cuối file in ra bằng chứng đọc thẳng từ đồ thị đã biên dịch.
"""
import sys
sys.path.insert(0, "src"); sys.path.insert(0, "tests")

from fake_chat import FakeChat                     # thay cho model thật, không cần key
from langchain_core.messages import HumanMessage, ToolMessage
from langgraph.checkpoint.memory import MemorySaver

from harness import tool
from harness.lg import build_agent, unguarded_paths

DON = {"A-4471": {"mon": "Bàn phím cơ", "gia": 890_000, "trang_thai": "đã giao"}}


@tool(effect="read")
def tim_don(ma: str) -> dict:
    """Tra cứu một đơn hàng theo mã."""
    return DON.get(ma, {"loi": "không tìm thấy"})


@tool(effect="write")
def luu_ghi_chu(ma: str, noi_dung: str) -> str:
    """Lưu ghi chú vào hồ sơ đơn hàng."""
    return f"đã lưu ghi chú cho {ma}"


@tool(effect="danger")
def hoan_tien(ma: str, so_tien: int) -> str:
    """Hoàn tiền cho khách. KHÔNG hoàn tác được."""
    return f"đã hoàn {so_tien}đ cho đơn {ma}"


def duyet(call, ctx) -> bool:
    """Cùng một chữ ký với backend vòng lặp: approve(ToolCall, RunContext) -> bool."""
    print(f"  [người duyệt]  {call.name}({call.arguments}) → đồng ý")
    return True


KICH_BAN = [
    FakeChat.call("tim_don", {"ma": "A-4471"}, "c1"),
    FakeChat.call("hoan_tien", {"ma": "A-4471", "so_tien": 890_000}, "c2"),
    FakeChat.call("luu_ghi_chu", {"ma": "A-4471", "noi_dung": "đã hoàn tiền"}, "c3"),
    FakeChat.text("Đã hoàn tiền đơn A-4471 và ghi chú lại."),
]

graph, runtime = build_agent(
    model=FakeChat(script=KICH_BAN),
    tools=[tim_don, luu_ghi_chu, hoan_tien],
    budget="$0.20, 15 steps",
    approve=duyet,
    checkpointer=MemorySaver(),          # chạy dở giữa chừng vẫn khôi phục được
)

cfg = {"configurable": {"thread_id": "khach-01"}}
ket_qua = graph.invoke({"messages": [HumanMessage("hoàn tiền đơn A-4471")]}, cfg)

print("\nHội thoại")
print("─" * 62)
for m in ket_qua["messages"]:
    ten = type(m).__name__.replace("Message", "")
    goi = [c["name"] for c in getattr(m, "tool_calls", []) or []]
    print(f"  {ten:<9} {str(m.content)[:52] or '→ ' + ', '.join(goi)}")

print(f"\nĐã tiêu     : ${ket_qua['spent_usd']}")
print(f"Số bước     : {ket_qua['step']}")
print(f"Dừng vì     : {ket_qua['stop_reason']}")

print("\nBằng chứng đọc từ đồ thị đã biên dịch")
print("─" * 62)
canh = {(e.source, e.target) for e in graph.get_graph().edges}
print(f"  node             : {[n for n in graph.get_graph().nodes if not n.startswith('__')]}")
print(f"  vào 'model' từ   : {sorted(s for s, t in canh if t == 'model')}")
print(f"  vào 'tools' từ   : {sorted(s for s, t in canh if t == 'tools')}")
print(f"  cổng bị đi vòng  : {unguarded_paths(graph) or 'KHÔNG'}")

print("\nKhôi phục sau khi tắt máy")
print("─" * 62)
luu = graph.get_state(cfg)
print(f"  checkpoint giữ   : {len(luu.values['messages'])} tin nhắn, "
      f"${luu.values['spent_usd']}, bước {luu.values['step']}")
print("  → tiến trình chết giữa chừng vẫn chạy tiếp được từ đúng chỗ đó")
