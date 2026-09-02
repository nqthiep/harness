"""An agent with long-term memory — LangGraph + OpenViking, Round 36.

Run:  python3 examples/viking_memory.py

All three platforms required in one file: LangChain (model), LangGraph (loop),
OpenViking (memory). No real server needed — the request goes through a fake
transport, but through **the real openviking-sdk code**.

The most notable thing here isn't that it runs, but that **`recall` is classified
`external`**. A context database that ingests web pages (`ov add-resource https://...`)
can return content an attacker wrote. If `recall` were `read`, a poisoned memory would
buy its way into using a `danger` tool — a hole the size of the whole memory system.

Note: `recall`/`remember` below are tools the library itself builds
(`VikingStore.tools()`, in `src/harness/memory/viking.py`) — their argument names
(`cau_hoi`, `ten`, `noi_dung`) are that library's own, out of scope for this example to
rename; everything else here is this file's own code.
"""
import asyncio
import sys
sys.path.insert(0, "src"); sys.path.insert(0, "tests")

import httpx
from fake_chat import FakeChat
from langchain_core.messages import HumanMessage
from openviking_sdk import AsyncHTTPClient

from harness import tool
from harness.errors import UnsafeToolSetError
from harness.lg import build_agent
from harness.memory.viking import VikingStore

# -- a fake OpenViking "server", returning the real envelope shape -----------
MEMORY = {"status": "ok", "result": {"results": [
    {"uri": "viking://memories/support/customer-01", "score": 0.94,
     "content": "Customer A-4471 prefers short answers, has complained about slow delivery."},
]}}


async def talk_to_fake_server() -> AsyncHTTPClient:
    c = AsyncHTTPClient(url="http://localhost:8080", api_key="demo")
    await c.initialize()
    c._http = httpx.AsyncClient(base_url="http://localhost:8080",
                                transport=httpx.MockTransport(
                                    lambda r: httpx.Response(200, json=MEMORY)))
    return c


store = VikingStore(client=asyncio.run(talk_to_fake_server()), namespace="support")
store._ready = True


@tool(effect="danger")
def refund(order_id: str, amount: int) -> str:
    """Refund the customer. NOT reversible.

    NO accepts_tainted=True here — S-16: that was the hole in the first draft, since a
    decorator argument with a default looks just like every other parameter under review.
    This grant now only ever comes from the operator, at the point build_agent() builds
    the agent — see below.
    """
    return f"refunded {amount} for {order_id}"


print("Tools the store hands the model")
print("-" * 66)
for t in store.tools():
    print(f"  {t.name:<10} effect={t.effect.value:<9} "
          f"{'-> TAINTS the run' if t.effect.value == 'external' else ''}")

# -- the important part: the harness refuses an unsafe combination -----------
@tool(effect="danger")
def delete_account(account_id: str) -> str:
    """Delete an account. NOT reversible."""
    return "deleted"


print("\nPairing recall with an irreversible tool, without declaring accepts_tainted")
print("-" * 66)
try:
    build_agent(model=FakeChat(script=[]), tools=store.tools() + [delete_account],
                budget="$1")
    print("  !! this built successfully -- THIS IS A BUG")
except UnsafeToolSetError:
    print("  -> refused at construction time, before a single step ran")

# -- a real run on LangGraph ---------------------------------------------------
graph, runtime = build_agent(
    model=FakeChat(script=[
        FakeChat.call("recall", {"cau_hoi": "how is customer A-4471"}, "c1"),
        FakeChat.call("refund", {"order_id": "A-4471", "amount": 890_000}, "c2"),
        FakeChat.text("Refund issued, kept it short as this customer prefers."),
    ]),
    tools=store.tools() + [refund],
    budget="$0.20, 10 steps",
    approve=lambda call, ctx: True,
    accepts_tainted=["refund"],          # granted by the OPERATOR -- not the tool's author
)

result = graph.invoke({"messages": [HumanMessage("handle order A-4471")]})

print("\nConversation")
print("-" * 66)
for m in result["messages"]:
    kind = type(m).__name__.replace("Message", "")
    calls = [c["name"] for c in getattr(m, "tool_calls", []) or []]
    print(f"  {kind:<9} {str(m.content)[:56] or '-> ' + ', '.join(calls)}")

print(f"\nrun tainted : {result['tainted']}  <- correct: memory was read from")
print(f"spent       : ${result['spent_usd']}")
print(f"stopped for : {result['stop_reason']}")
print("\nrefund could run because the OPERATOR granted accepts_tainted=[\"refund\"] when")
print("building the agent -- not because the tool's author declared it in @tool(). A")
print("line in the decorator looks like every other parameter under review; in")
print("build_agent() it does not.")
