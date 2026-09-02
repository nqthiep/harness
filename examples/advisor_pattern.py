"""Advisor pattern — a cheap model handles routine work, a strong model advises when needed.

Run:  python3 examples/advisor_pattern.py

Two layers, stacked:

  1. The advisor is an ordinary SUBAGENT (`.as_tool()`) — the cheap model ("Worker")
     decides on its own WHEN to ask, through the existing `job=` mechanism
     (docs/07-cost.md S4). No new harness machinery needed: this is already
     "expressible today, with no new machinery" (docs/07-cost.md S6, about
     reflection/self-critique loops in general).

  2. `RequireBeforePolicy` (policy/builtin.py) makes "ask first" NOT just a prompt
     instruction (which a model can forget) but a structural HARD GATE: the
     `delete_data` tool (effect=danger) is DENIED at the policy layer if
     `consult_advisor` has not already run in this run — regardless of whether the
     `approve=` callback would have said yes.

     Important: the advisor NEVER grants permission itself. `RequireBeforePolicy` can
     only TIGHTEN the verdict (P-2, every Policy can only restrict) -- it turns "hasn't
     asked yet" into an earlier DENY, it never turns "has asked" into an ALLOW. The real
     ALLOW still has to come from `approve=` (a human or an operating system), exactly
     invariant D-1 (design/00-foundation.md S4.2): `Actor` deliberately has NO `Model`
     variant -- a model, however strong, is never the one who grants a `Decision`. This
     is exactly the mistake the source research calls out as "where agno gets it wrong."

This example uses FakeModel (no API key needed, costs nothing). To run for real, remove
`provider=...` and run `harness setup` first.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness import Agent, tool
from harness.policy.builtin import RequireBeforePolicy

# -----------------------------------------------------------------------------
# 1. The advisor -- a STRONG model, only ever gives an opinion, never acts itself
# -----------------------------------------------------------------------------
def make_advisor(provider=None) -> Agent:
    return Agent(
        name="Advisor",
        job=(
            "Carefully analyze a hard situation and give a clear, reasoned "
            "recommendation. You have NO tools to act with -- answer with analysis only."
        ),
        model="claude-opus-5",
        effort="high",               # think harder -- this is when it's worth it
        budget="$0.05",              # its own budget, further capped by the parent (S-13)
        provider=provider,
    )


def make_consult_tool(advisor: Agent):
    @tool(effect="read")
    def consult_advisor(situation: str) -> str:
        """Ask the advisor's opinion before a hard decision or an irreversible action."""
        return advisor.run(situation).text
    return consult_advisor


# -----------------------------------------------------------------------------
# 2. Routine work -- the Worker (cheap model) does it itself, no need to ask anyone
# -----------------------------------------------------------------------------
DATA = {"customer_A": {"orders": 3, "total_spent": 4_500_000}}


@tool(effect="read")
def look_up_customer(customer_id: str) -> dict:
    """Look up a customer's information."""
    return DATA.get(customer_id, {"error": "not found"})


@tool(effect="danger")
def delete_data(customer_id: str) -> str:
    """Permanently delete a customer's data -- CANNOT be undone."""
    DATA.pop(customer_id, None)
    return f"deleted {customer_id}"


def approve(call, ctx) -> bool:
    """A real operator would stand here -- this example auto-approves to keep things
    simple, BUT RequireBeforePolicy still blocks before this approval ever gets a
    chance to run, if the advisor wasn't consulted first."""
    print(f"    (approve= was asked about {call.name} -- wouldn't reach here without "
          f"a prior consult)")
    return True


def make_worker(provider=None, advisor_provider=None) -> Agent:
    consult_advisor = make_consult_tool(make_advisor(provider=advisor_provider))
    return Agent(
        name="Worker",
        job=(
            "Handle customer requests. Look-ups you do yourself. "
            "Before an IRREVERSIBLE action (deleting data), always call "
            "consult_advisor first to get an opinion."
        ),
        model="claude-haiku-4-5",        # cheap -- routine work doesn't need a strong model
        tools=[look_up_customer, consult_advisor, delete_data],
        budget="$0.05, 10 steps",
        approve=approve,
        # A hard gate, not dependent on the model REMEMBERING to ask first:
        policies=[RequireBeforePolicy(
            tool="delete_data", requires="consult_advisor",
            reason="delete_data cannot be undone -- consult_advisor must run first")],
        provider=provider,
    )


# -----------------------------------------------------------------------------
# 3. Run -- two scenarios: forgot to ask (blocked) and asked first (goes through)
# -----------------------------------------------------------------------------
def main() -> None:
    from harness.models.fake import FakeModel

    # The advisor is never actually called in Scenario 1 (the model tries to delete
    # right away), but building the Agent still needs SOME provider (nothing is spent
    # until it's actually called) -- use FakeModel for both scenarios so this example
    # runs without an API key.
    advisor_opinion = FakeModel([FakeModel.text(
        "Recommendation: there's email confirmation from the customer, correct "
        "procedure -- go ahead and delete.")])

    print("-- Scenario 1: the model TRIES to delete right away, without asking first --")
    forgot_to_ask = FakeModel([
        FakeModel.tool_call("delete_data", {"customer_id": "customer_A"}),
        FakeModel.text("Done."),
    ])
    w1 = make_worker(provider=forgot_to_ask, advisor_provider=advisor_opinion)
    r1 = w1.try_run("Please delete customer_A's data right away.")
    print(f"  result: {r1.stop_reason.value}  .  ran: {r1.tools_run}")
    print(f"  data still there: {'customer_A' in DATA}\n")

    print("-- Scenario 2: the model asks the advisor first, then deletes --")
    asked_first = FakeModel([
        FakeModel.tool_call("consult_advisor",
                            {"situation": "customer_A has asked for their data to be "
                                         "permanently deleted, confirmed by email. "
                                         "Should we delete it?"}),
        FakeModel.tool_call("delete_data", {"customer_id": "customer_A"}),
        FakeModel.text("Consulted the advisor and deleted the data."),
    ])
    w2 = make_worker(provider=asked_first, advisor_provider=advisor_opinion)
    r2 = w2.try_run("Please delete customer_A's data, they confirmed by email.")
    print(f"  result: {r2.stop_reason.value}  .  ran: {r2.tools_run}")
    print(f"  data still there: {'customer_A' in DATA}")

    print("\nThe advisor uses a strong model + high effort, only when actually needed --"
          " the Worker uses a cheap model for everything else. \"Must ask first\" is a "
          "Policy, not a prompt instruction a model can skip.")


if __name__ == "__main__":
    main()
