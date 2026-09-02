"""The same agent, running on LangGraph — Round 35.

Run:  python3 examples/graph_agent.py

The only difference from `support_agent.py` is *who holds the loop*: here
LangGraph does, and the rules stay identical. That is not just a claim — the
end of this file prints proof read straight off the compiled graph.
"""
import sys
sys.path.insert(0, "src"); sys.path.insert(0, "tests")

from fake_chat import FakeChat                     # stands in for a real model, no key needed
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import MemorySaver

from harness import tool
from harness.lg import build_agent, unguarded_paths

ORDERS = {"A-4471": {"item": "Mechanical keyboard", "price": 890_000, "status": "delivered"}}


@tool(effect="read")
def find_order(order_id: str) -> dict:
    """Look up an order by id."""
    return ORDERS.get(order_id, {"error": "not found"})


@tool(effect="write")
def save_note(order_id: str, note: str) -> str:
    """Save a note on the order record."""
    return f"note saved for {order_id}"


@tool(effect="danger")
def refund(order_id: str, amount: int) -> str:
    """Refund the customer. NOT reversible."""
    return f"refunded {amount} for order {order_id}"


def approve(call, ctx) -> bool:
    """Same signature as the classic backend: approve(ToolCall, RunContext) -> bool."""
    print(f"  [approver]  {call.name}({call.arguments}) -> approved")
    return True


SCRIPT = [
    FakeChat.call("find_order", {"order_id": "A-4471"}, "c1"),
    FakeChat.call("refund", {"order_id": "A-4471", "amount": 890_000}, "c2"),
    FakeChat.call("save_note", {"order_id": "A-4471", "note": "refund issued"}, "c3"),
    FakeChat.text("Refunded order A-4471 and saved a note."),
]

graph, runtime = build_agent(
    model=FakeChat(script=SCRIPT),
    tools=[find_order, save_note, refund],
    budget="$0.20, 15 steps",
    approve=approve,
    checkpointer=MemorySaver(),          # a run interrupted mid-way still resumes
)

cfg = {"configurable": {"thread_id": "customer-01"}}
result = graph.invoke({"messages": [HumanMessage("refund order A-4471")]}, cfg)

print("\nConversation")
print("-" * 62)
for m in result["messages"]:
    kind = type(m).__name__.replace("Message", "")
    calls = [c["name"] for c in getattr(m, "tool_calls", []) or []]
    print(f"  {kind:<9} {str(m.content)[:52] or '-> ' + ', '.join(calls)}")

print(f"\nSpent       : ${result['spent_usd']}")
print(f"Steps       : {result['step']}")
print(f"Stopped for : {result['stop_reason']}")

print("\nProof read from the compiled graph")
print("-" * 62)
edges = {(e.source, e.target) for e in graph.get_graph().edges}
print(f"  nodes            : {[n for n in graph.get_graph().nodes if not n.startswith('__')]}")
print(f"  into 'model' from: {sorted(s for s, t in edges if t == 'model')}")
print(f"  into 'tools' from: {sorted(s for s, t in edges if t == 'tools')}")
print(f"  unguarded paths  : {unguarded_paths(graph) or 'NONE'}")

print("\nRecovery after a simulated crash")
print("-" * 62)
saved = graph.get_state(cfg)
print(f"  checkpoint holds : {len(saved.values['messages'])} messages, "
      f"${saved.values['spent_usd']}, step {saved.values['step']}")
print("  -> a process that died mid-run can resume from exactly that point")
