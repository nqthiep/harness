"""Agent có trí nhớ dài hạn — LangGraph + OpenViking, vòng 36.

Chạy:  python3 examples/viking_memory.py

Cả ba nền tảng bắt buộc trong một file: LangChain (model), LangGraph (vòng lặp),
OpenViking (trí nhớ).  Không cần server thật — request đi qua một transport giả,
nhưng đi qua **đúng code của openviking-sdk**.

Điều đáng chú ý nhất không phải là nó chạy, mà là **`recall` được phân loại
`external`**.  Một context database có nuốt trang web vào (`ov add-resource https://…`),
nên thứ nó trả ra có thể do kẻ tấn công viết.  Nếu `recall` là `read`, một ký ức bị đầu
độc sẽ mua được quyền dùng tool `danger` — lỗ hổng to bằng cả hệ thống trí nhớ.
"""
import asyncio
import sys
sys.path.insert(0, "src"); sys.path.insert(0, "tests")

import httpx
from fake_chat import FakeChat
from langchain_core.messages import HumanMessage, ToolMessage
from openviking_sdk import AsyncHTTPClient

from harness import tool
from harness.errors import UnsafeToolSetError
from harness.lg import build_agent
from harness.memory.viking import VikingStore

# ── một "server" OpenViking giả, trả đúng envelope thật ──────────────────────
NHO = {"status": "ok", "result": {"results": [
    {"uri": "viking://memories/cskh/khach-01", "score": 0.94,
     "content": "Khách A-4471 thích trả lời ngắn gọn, đã từng khiếu nại giao chậm."},
]}}


async def noi_toi_server_gia() -> AsyncHTTPClient:
    c = AsyncHTTPClient(url="http://localhost:8080", api_key="demo")
    await c.initialize()
    c._http = httpx.AsyncClient(base_url="http://localhost:8080",
                                transport=httpx.MockTransport(
                                    lambda r: httpx.Response(200, json=NHO)))
    return c


store = VikingStore(client=asyncio.run(noi_toi_server_gia()), namespace="cskh")
store._ready = True


@tool(effect="danger", accepts_tainted=True)
def hoan_tien(ma: str, so_tien: int) -> str:
    """Hoàn tiền cho khách. KHÔNG hoàn tác được."""
    return f"đã hoàn {so_tien}đ cho {ma}"


print("Tool mà store cấp cho model")
print("─" * 66)
for t in store.tools():
    print(f"  {t.name:<10} effect={t.effect.value:<9} "
          f"{'→ LÀM BẨN run' if t.effect.value == 'external' else ''}")

# ── phần quan trọng nhất: harness từ chối tổ hợp không an toàn ───────────────
@tool(effect="danger")
def xoa_tai_khoan(ma: str) -> str:
    """Xoá tài khoản. KHÔNG hoàn tác."""
    return "đã xoá"


print("\nGhép recall với một tool không hoàn tác được, không khai accepts_tainted")
print("─" * 66)
try:
    build_agent(model=FakeChat(script=[]), tools=store.tools() + [xoa_tai_khoan],
                budget="$1")
    print("  !! đã dựng được — ĐÂY LÀ LỖI")
except UnsafeToolSetError:
    print("  → bị từ chối ngay lúc dựng, trước khi chạy một bước nào")

# ── chạy thật trên LangGraph ────────────────────────────────────────────────
graph, runtime = build_agent(
    model=FakeChat(script=[
        FakeChat.call("recall", {"cau_hoi": "khách A-4471 thế nào"}, "c1"),
        FakeChat.call("hoan_tien", {"ma": "A-4471", "so_tien": 890_000}, "c2"),
        FakeChat.text("Đã hoàn tiền, trả lời ngắn gọn như khách thích."),
    ]),
    tools=store.tools() + [hoan_tien],
    budget="$0.20, 10 steps",
    approve=lambda call, ctx: True,
)

ket_qua = graph.invoke({"messages": [HumanMessage("xử lý đơn A-4471")], "step": 0})

print("\nHội thoại")
print("─" * 66)
for m in ket_qua["messages"]:
    ten = type(m).__name__.replace("Message", "")
    goi = [c["name"] for c in getattr(m, "tool_calls", []) or []]
    print(f"  {ten:<9} {str(m.content)[:56] or '→ ' + ', '.join(goi)}")

print(f"\nrun bị làm bẩn : {ket_qua['tainted']}  ← đúng: đã đọc từ trí nhớ")
print(f"đã tiêu        : ${ket_qua['spent_usd']}")
print(f"dừng vì        : {ket_qua['stop_reason']}")
print("\nhoan_tien chạy được vì nó khai accepts_tainted=True — một quyết định")
print("của tác giả, viết ra trong code, chứ không phải mặc định im lặng.")
