"""Customer support assistant — a multi-capability agent.

Run:  python3 examples/support_agent.py

This example uses FakeModel (no API key needed, costs nothing). To run for real,
remove `provider=...` and run `harness setup` first — the Agent will use
AnthropicProvider on its own.
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

# -----------------------------------------------------------------------------
# 1. Mock data (in reality, your DB / API)
# -----------------------------------------------------------------------------
ORDERS = {
    "A-4471": {"customer": "Lan",  "item": "Mechanical keyboard", "price": 1_290_000,
               "status": "delivered", "shipped": "2026-08-20"},
    "A-4472": {"customer": "Minh", "item": "Wireless mouse", "price": 450_000,
               "status": "in transit", "shipped": None},
}
DB = SqliteStore(Path(tempfile.mkdtemp()) / "notes.db", agent="support")
API_KEY = Secret("sk-live-PAYMENTS-abc123", name="payment_key")

# -----------------------------------------------------------------------------
# 2. Tools — each declares what it does to the world
# -----------------------------------------------------------------------------

@tool(effect="read")                       # only looks -> runs in parallel, auto-allowed
def find_order(order_id: str) -> dict:
    """Look up an order by id."""
    return ORDERS.get(order_id, {"error": f"no such order {order_id}"})


@tool(effect="read")
async def read_notes(keyword: str) -> str:
    """Read back saved notes about a customer."""
    hits = await DB.search(keyword, limit=3)
    return "\n".join(f"- {m.key}: {m.value}" for m in hits) or "no notes yet"


@tool(effect="write")                      # changes something -> runs sequentially, no auto-retry
async def save_note(order_id: str, note: str) -> str:
    """Save a note about an order for later reading."""
    await DB.put(f"order_{order_id}", note)
    return f"note saved for {order_id}"


@tool(effect="danger")                     # can't undo -> ALWAYS asks before running
def refund(order_id: str, amount: int) -> str:
    """Refund the customer. Cannot be undone once it runs."""
    with API_KEY.reveal() as key:          # the secret is only open inside this block
        _ = key                            # call the payment gateway here
    return f"refunded {amount:,} for order {order_id}"


# -----------------------------------------------------------------------------
# 3. The approver — every `danger` tool goes through here
# -----------------------------------------------------------------------------
def ask_approval(call, ctx) -> bool:
    print(f"    !  requesting permission to run {call.name}({dict(call.arguments)})")
    ok = int(call.arguments.get("amount", 0)) <= 1_000_000     # auto-approve under 1M
    print(f"    {'-> approved' if ok else '-> declined -- over the limit'}")
    return ok


# -----------------------------------------------------------------------------
# 4. Subagent — bulk reading handed to a cheap model, its own budget
# -----------------------------------------------------------------------------
@tool(effect="read")
def read_policy(section: str) -> str:
    """Read a section of the return policy."""
    return ("Returns accepted within 7 days of delivery. Item must be unopened. "
            "Refund up to 100% of the order value.")


policy_expert = Agent(
    name="Policy Expert",
    job="Read the policy and answer briefly, based only on the text.",
    tools=[read_policy],
    model="claude-haiku-4-5",              # a cheap model for reading
    budget="$0.01",
    approve=ask_approval,                  # G-15: the parent below has one, so must this
)


# -----------------------------------------------------------------------------
# 5. The return type — a checked answer, not a bare string
# -----------------------------------------------------------------------------
@dataclasses.dataclass
class Conclusion:
    order_id: str
    refund_approved: bool
    amount: int
    reason: str


# -----------------------------------------------------------------------------
# 6. The main agent
# -----------------------------------------------------------------------------
def make_agent(provider=None, transcript=None) -> Agent:
    return Agent(
        name="Support Assistant",
        job=(
            "Help customers with their orders. Always look up the order before "
            "answering. Check the return policy before issuing a refund. "
            "Never guess an order's status."
        ),
        tools=[
            find_order,
            read_notes,
            save_note,
            calculate,                              # a built-in tool
            policy_expert.as_tool(),                # a subagent turned into a tool
            refund,
        ],
        returns=Conclusion,                          # a typed, validated answer
        budget="$0.20, 15 steps, 2m",                # a hard ceiling, checked BEFORE every call
        approve=ask_approval,                        # every `danger` call goes through this
        transcript=transcript,                       # records every decision
        provider=provider,
    )


# -----------------------------------------------------------------------------
# 7. Run
# -----------------------------------------------------------------------------
def main() -> None:
    from harness.models.fake import FakeModel

    script = FakeModel([
        FakeModel.tool_call("find_order", {"order_id": "A-4471"}, call_id="c1"),
        FakeModel.tool_call("ask_policy_expert",
                            {"task": "Is an order delivered on 08/20 still returnable?"},
                            call_id="c2"),
        FakeModel.tool_call("calculate", {"expression": "1290000 * 1.0"}, call_id="c3"),
        FakeModel.tool_call("refund", {"order_id": "A-4471", "amount": 1290000}, call_id="c4"),
        FakeModel.tool_call("save_note",
                            {"order_id": "A-4471", "note": "Refunded in full"}, call_id="c5"),
        # With returns=Conclusion, the model returns JSON of the right shape -- the
        # harness validates it and rebuilds the dataclass.
        FakeModel.text('{"order_id": "A-4471", "refund_approved": true, '
                       '"amount": 1290000, "reason": "Still within the 7-day return window"}'),
    ])

    ts = Path(tempfile.mkdtemp()) / "run.jsonl"
    agent = make_agent(provider=script, transcript=ts)

    print("Declared tools and their effect classes:")
    for t in agent.toolset:
        print(f"  {t.name:28} {t.effect.value}")

    print("\nRunning:")
    r = agent.try_run("Order A-4471, delivered 08/20, has a broken key -- I want a refund.")

    print(f"\nTyped answer      : {r.value}")
    print(f"  .refund_approved : {r.value.refund_approved if r.value else '-'}")
    print(f"  .amount          : {r.value.amount:,}" if r.value else "")
    print(f"Stopped: {r.stop_reason.value}  .  {r.steps} steps  .  {r.cost}")

    print("\nLog (every decision is recorded):")
    from harness.observe.transcript import read
    for e in read(ts):
        if e["kind"] == "policy.decided":
            print(f"  {e['data']['tool']:28} -> {e['data']['verdict']}")

    print("\nSecret does NOT leak into the log:",
          "sk-live-PAYMENTS" not in ts.read_text())


if __name__ == "__main__":
    main()
