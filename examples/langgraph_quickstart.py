"""Tạo agent với harness + LangGraph — từ nhỏ nhất đến đầy đủ.

Chạy:  python3 examples/langgraph_quickstart.py

Không cần API key: mặc định dùng model giả.  Có key thì nó tự dùng model thật:

    export ANTHROPIC_API_KEY=sk-ant-...
    pip install 'harness[graph]' langchain-anthropic

Năm bậc, mỗi bậc thêm ĐÚNG MỘT khái niệm.  Bậc 1 chạy được rồi; bốn bậc sau
là thứ bạn thêm khi cần, không phải thứ phải học trước.
"""
import os
import sys
sys.path.insert(0, "src"); sys.path.insert(0, "tests")

from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from harness import tool
from harness.lg import build_agent, unguarded_paths


def lay_model(kich_ban):
    """Có key thì model thật, không thì model giả — code agent y hệt nhau."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model="claude-opus-5")
    from fake_chat import FakeChat
    return FakeChat(script=kich_ban)


def tieu_de(n, chu):
    print(f"\n{'═' * 68}\nBẬC {n} — {chu}\n{'═' * 68}")


# ══════════════════════════════════════════════════════════════════════════
tieu_de(1, "Agent nhỏ nhất chạy được")
# Chỉ cần model và ngân sách.  `budget=` không phải tuỳ chọn nâng cao —
# nó ở đây từ dòng đầu tiên, vì một agent không có trần chi tiêu là một
# agent có thể tiêu hết thẻ của bạn.
from fake_chat import FakeChat  # noqa: E402

graph, _ = build_agent(model=lay_model([FakeChat.text("Chào bạn!")]), budget="$0.10")
ket_qua = graph.invoke({"messages": [HumanMessage("chào")]})
print(f"  {ket_qua['messages'][-1].content}")
print(f"  đã tiêu ${ket_qua['spent_usd']} — trần là $0.10")


# ══════════════════════════════════════════════════════════════════════════
tieu_de(2, "Thêm tool — mỗi tool phải khai `effect`")
# `effect` là thứ DUY NHẤT bạn phải quyết định về một tool.  Từ nó harness
# suy ra 5 hành vi: chạy song song được không, retry được không, có làm bẩn
# run không, mặc định cho phép hay hỏi, ghi log mức nào.
DON = {"A-4471": {"mon": "Bàn phím cơ", "gia": 890_000, "trang_thai": "đã giao"}}


@tool(effect="read")           # chỉ nhìn, không đổi gì
def tim_don(ma: str) -> dict:
    """Tra cứu một đơn hàng theo mã."""
    return DON.get(ma, {"loi": "không tìm thấy"})


@tool(effect="write")          # đổi thứ gì đó, nhưng hoàn tác được
def luu_ghi_chu(ma: str, noi_dung: str) -> str:
    """Lưu ghi chú vào hồ sơ đơn."""
    return f"đã lưu ghi chú cho {ma}"


graph, _ = build_agent(
    model=lay_model([FakeChat.call("tim_don", {"ma": "A-4471"}, "c1"),
                     FakeChat.text("Đơn A-4471: Bàn phím cơ, đã giao.")]),
    tools=[tim_don, luu_ghi_chu],
    budget="$0.10",
)
ket_qua = graph.invoke({"messages": [HumanMessage("đơn A-4471 sao rồi")]})
print(f"  {ket_qua['messages'][-1].content}")


# ══════════════════════════════════════════════════════════════════════════
tieu_de(3, "Tool nguy hiểm — mặc định là TỪ CHỐI")
@tool(effect="danger")         # không hoàn tác được
def hoan_tien(ma: str, so_tien: int) -> str:
    """Hoàn tiền cho khách. KHÔNG hoàn tác được."""
    print(f"     >>> đã hoàn {so_tien:,}đ")
    return f"đã hoàn {so_tien}đ"


KICH_BAN_HOAN = [FakeChat.call("hoan_tien", {"ma": "A-4471", "so_tien": 890_000}, "c1"),
                 FakeChat.text("Xong.")]

# 3a. không có người duyệt → bị chặn
graph, _ = build_agent(model=lay_model(KICH_BAN_HOAN), tools=[hoan_tien], budget="$0.10")
kq = graph.invoke({"messages": [HumanMessage("hoàn tiền đơn A-4471")]})
print(f"  không có approve= : {kq['messages'][-2].content}")

# 3b. có người duyệt → hỏi rồi mới làm
def duyet(call, ctx) -> bool:
    print(f"  [hỏi người]       : {call.name}({call.arguments}) → đồng ý")
    return True


graph, _ = build_agent(model=lay_model(KICH_BAN_HOAN), tools=[hoan_tien],
                       budget="$0.10", approve=duyet)
graph.invoke({"messages": [HumanMessage("hoàn tiền đơn A-4471")]})


# ══════════════════════════════════════════════════════════════════════════
tieu_de(4, "Bền vững — tắt máy giữa chừng vẫn chạy tiếp")
# Đây là thứ LangGraph cho mà vòng lặp tự viết không cho: checkpointer.
graph, _ = build_agent(
    model=lay_model([FakeChat.call("tim_don", {"ma": "A-4471"}, "c1"),
                     FakeChat.text("Đã giao rồi bạn nhé.")]),
    tools=[tim_don],
    budget="$0.10",
    checkpointer=MemorySaver(),          # thật thì dùng SqliteSaver/PostgresSaver
)
cau_hinh = {"configurable": {"thread_id": "khach-01"}}
graph.invoke({"messages": [HumanMessage("đơn A-4471 sao rồi")]}, cau_hinh)

luu = graph.get_state(cau_hinh).values
print(f"  checkpoint giữ    : {len(luu['messages'])} tin nhắn, ${luu['spent_usd']}, "
      f"bước {luu['step']}")

# cùng thread_id → agent nhớ lượt trước, ngân sách vẫn cộng dồn
graph.invoke({"messages": [HumanMessage("thế còn ghi chú thì sao")]}, cau_hinh)
luu = graph.get_state(cau_hinh).values
print(f"  sau lượt thứ hai  : {len(luu['messages'])} tin nhắn, ${luu['spent_usd']}")


# ══════════════════════════════════════════════════════════════════════════
tieu_de(5, "Vì sao tin được — đọc thẳng từ đồ thị")
# Các cổng an toàn không phải là quy ước, mà là HÌNH DẠNG của đồ thị.
# Không có cạnh nào vào `model` mà không qua `budget`, vào `tools` mà không
# qua `policy`.  Đây là chứng minh bằng khả năng tới được, đúng cho cả những
# đường đi mà không test nào đi qua.
canh = {(e.source, e.target) for e in graph.get_graph().edges}
print(f"  node              : {[n for n in graph.get_graph().nodes if not n.startswith('__')]}")
print(f"  vào 'model' từ    : {sorted(s for s, t in canh if t == 'model')}")
print(f"  vào 'tools' từ    : {sorted(s for s, t in canh if t == 'tools')}")
print(f"  cổng bị đi vòng   : {unguarded_paths(graph) or 'KHÔNG'}")

print(f"""
{'─' * 68}
Tóm lại, một agent đầy đủ là chừng này:

    from harness import tool
    from harness.lg import build_agent

    @tool(effect="read")
    def tim_don(ma: str) -> dict:
        \"\"\"Tra cứu đơn hàng.\"\"\"
        return DON.get(ma)

    graph, _ = build_agent(model=ChatAnthropic(model="claude-opus-5"),
                           tools=[tim_don], budget="$0.20, 15 steps",
                           approve=duyet, checkpointer=MemorySaver())

    graph.invoke({{"messages": [HumanMessage("đơn A-4471 sao rồi")]}})

Model đang dùng: {'THẬT (ANTHROPIC_API_KEY)' if os.environ.get('ANTHROPIC_API_KEY') else 'GIẢ — đặt ANTHROPIC_API_KEY để chạy thật'}""")
