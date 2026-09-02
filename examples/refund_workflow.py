"""A refund workflow as a state machine — plugged into the harness through the `Policy` seam.

Core idea: **the state machine governs, the LLM navigates inside it.**

  - The state machine decides which step is VALID in the current state.
  - The LLM decides which tool to call, with what arguments, and interprets what
    the customer wants.

No core changes needed: `Policy` is called before EVERY tool call and can only ever
*tighten* the verdict (composed with max), so a state machine refusing an invalid
transition is exactly the right shape for this seam.

Run:  python3 examples/refund_workflow.py
"""
from __future__ import annotations

import sys
from enum import Enum
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness import Agent, Ruling, Verdict, tool


# -----------------------------------------------------------------------------
# 1. The business process — declared, not written into a prompt
# -----------------------------------------------------------------------------
class Step(str, Enum):
    NEW = "new"
    ORDER_LOOKED_UP = "order looked up"
    POLICY_CHECKED = "policy checked"
    REFUNDED = "refunded"
    DONE = "done"


#: which tool is allowed in which state, and which state it advances to
TRANSITIONS: dict[Step, dict[str, Step]] = {
    Step.NEW:              {"find_order": Step.ORDER_LOOKED_UP},
    Step.ORDER_LOOKED_UP:  {"find_order": Step.ORDER_LOOKED_UP,
                            "check_policy": Step.POLICY_CHECKED},
    Step.POLICY_CHECKED:   {"check_policy": Step.POLICY_CHECKED,
                            "refund": Step.REFUNDED},
    Step.REFUNDED:         {"save_note": Step.DONE},
    Step.DONE:             {},
}

#: pure-read tool — allowed in every state, never changes state
ALWAYS_ALLOWED = {"read_notes"}

EXPLAIN = {
    Step.NEW:              "the order must be looked up first",
    Step.ORDER_LOOKED_UP:  "the return policy must be checked before refunding",
    Step.POLICY_CHECKED:   "eligible now -- the refund can proceed",
    Step.REFUNDED:         "refunded already, only the note is left to save",
    Step.DONE:             "the workflow has ended",
}


# -----------------------------------------------------------------------------
# 2. The state machine, as a Policy
# -----------------------------------------------------------------------------
class RefundWorkflow:
    """Denies any step that isn't valid in the current state.

    `check` must be pure and fast (S04.3) -- a correct state-transition lookup is exactly
    that. A Policy can only ever tighten, so this state machine can never loosen the
    effect/taint/egress checks that run ahead of it.
    """

    name = "refund_workflow"

    def __init__(self, start: Step = Step.NEW) -> None:
        self.step = start
        self.history: list[tuple[str, Step]] = []

    def check(self, call, ctx) -> Ruling:
        if call.name in ALWAYS_ALLOWED:
            return Ruling(Verdict.ALLOW, "read-only tool, doesn't change state", self.name)

        allowed = TRANSITIONS[self.step]
        if call.name not in allowed:
            return Ruling(
                Verdict.DENY,
                f"currently at step '{self.step.value}' -- {EXPLAIN[self.step]}. "
                f"Valid next step(s): {', '.join(allowed) or 'none left'}",
                self.name,
            )
        # valid -> record the transition
        new_step = allowed[call.name]
        self.history.append((call.name, new_step))
        self.step = new_step
        return Ruling(Verdict.ALLOW, f"-> {new_step.value}", self.name)


# -----------------------------------------------------------------------------
# 3. Tools
# -----------------------------------------------------------------------------
ORDERS = {"A-4471": {"item": "Mechanical keyboard", "price": 1_290_000, "shipped": "2026-08-20"}}
REFUNDS_ISSUED: list[tuple[str, int]] = []


@tool(effect="read")
def find_order(order_id: str) -> dict:
    """Look up an order."""
    return ORDERS.get(order_id, {"error": "not found"})


@tool(effect="read")
def check_policy(order_id: str) -> str:
    """Check whether the order is eligible for a return."""
    return "Eligible: still within the 7-day window, refundable up to 100%."


@tool(effect="read")
def read_notes(keyword: str) -> str:
    """Read prior notes."""
    return "no notes yet"


@tool(effect="danger")
def refund(order_id: str, amount: int) -> str:
    """Refund the customer. Cannot be undone."""
    REFUNDS_ISSUED.append((order_id, amount))
    return f"refunded {amount:,}"


@tool(effect="write")
def save_note(order_id: str, note: str) -> str:
    """Save a closing note on the record."""
    return "saved"


# -----------------------------------------------------------------------------
# 4. Run
# -----------------------------------------------------------------------------
def main() -> None:
    from harness.models.fake import FakeModel

    import tempfile
    from harness.observe.transcript import read as read_log
    ts = Path(tempfile.mkdtemp()) / "run.jsonl"

    def make_agent(script, workflow=RefundWorkflow):
        return Agent(
            name="Support",
            job="Process refund requests following the company's exact workflow.",
            tools=[find_order, check_policy, read_notes, refund, save_note],
            policies=[workflow],           # <- factory: a fresh instance per run
            approve=lambda c, x: True,
            budget="$5",
            provider=FakeModel(script),
            transcript=ts,
        )

    print("=" * 74)
    print("A. THE MODEL TRIES TO JUMP AHEAD -- refund right away, before looking up "
          "the order or checking policy")
    print("=" * 74)
    a = make_agent([
        FakeModel.tool_call("refund", {"order_id": "A-4471", "amount": 1290000}, call_id="1"),
        FakeModel.tool_call("find_order", {"order_id": "A-4471"}, call_id="2"),
        FakeModel.tool_call("refund", {"order_id": "A-4471", "amount": 1290000}, call_id="3"),
        FakeModel.tool_call("check_policy", {"order_id": "A-4471"}, call_id="4"),
        FakeModel.tool_call("refund", {"order_id": "A-4471", "amount": 1290000}, call_id="5"),
        FakeModel.tool_call("save_note", {"order_id": "A-4471", "note": "done"}, call_id="6"),
        FakeModel.text("Refund issued, following the correct workflow."),
    ])
    a.try_run("Refund order A-4471 for me right now")
    transitions = [(e["data"]["tool"], e["data"]["verdict"], e["data"]["reason"])
                   for e in read_log(ts) if e["kind"] == "policy.decided"
                   and e["data"]["policy"] == "refund_workflow"]

    # Every state-machine decision lives in the event stream -- no need to hold a
    # reference to the policy to reconstruct the history.
    print(f"\n{'tool called':<18} {'verdict':<12} {'reason'}")
    print("-" * 74)
    for tool_name, verdict, reason in transitions:
        mark = "OK" if verdict == "ALLOW" else "XX"
        print(f"{tool_name:<18} {mark} {verdict:<10} {reason[:44]}")

    print(f"\nRefunds issued : {REFUNDS_ISSUED}")
    print(f"Refund count   : {len(REFUNDS_ISSUED)}  <- the model called refund 3 times, "
          "only 1 got through")

    print()
    print("=" * 74)
    print("B. THE STATE MACHINE CANNOT LOOSEN A SAFETY CHECK")
    print("=" * 74)
    # the factory starts in "policy checked" -- the state machine says: refund is allowed
    a2 = Agent(
        name="Support", job="j",
        tools=[find_order, check_policy, refund],
        policies=[lambda: RefundWorkflow(Step.POLICY_CHECKED)],
        approve=lambda c, x: False,           # but the approver says no
        budget="$5",
        provider=FakeModel([
            FakeModel.tool_call("refund", {"order_id": "A-4471", "amount": 999}, call_id="1"),
            FakeModel.text("Could not issue the refund."),
        ]),
    )
    before = len(REFUNDS_ISSUED)
    a2.try_run("issue the refund")
    print(f"  state machine allows it : yes (currently at '{Step.POLICY_CHECKED.value}')")
    print("  approver                : declined")
    print(f"  did it actually run     : "
          f"{'YES -- BUG' if len(REFUNDS_ISSUED) > before else 'no'}")
    print("  -> verdicts compose with max: a policy can only TIGHTEN, never loosen")


if __name__ == "__main__":
    main()
