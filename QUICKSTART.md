# Quickstart

Every code block below is real — copied from this repo's own tests, or run against
`harness.testing.FakeModel` while writing this file so nothing here is aspirational.
Every comparison to another framework is sourced from `docs/17-research-alignment.md`,
which is itself built on an independent study of 12 agent frameworks and 9 harnesses
(`research/`) — not from general reputation. Where a claim is a self-assessment rather
than an independent one, this file says so.

---

## 1. Install and run your first agent

```
pip install harness
harness setup          # asks for a key, checks it works before saving it
harness new joker       # writes joker.py, and a .gitignore that protects the key
python joker.py
```

`joker.py`, exactly as scaffolded:

```python
from harness import Agent

joker = Agent(
    name="Joker",
    job="Tell funny jokes for kids. Keep them short and silly.",
    budget="$0.05",     # a ceiling, enforced before each call — not an alert after
)

print(joker.run("Tell me a joke about a cat"))
```

Three concepts (`name`, `job`, `budget`), one method (`run`). That is the whole surface
for a first agent — everything else (the loop, retries, prompt caching, permissions,
telemetry) is already on with safe defaults.

---

## 2. What's actually different here — and how to check it yourself

Most "why us" sections ask you to trust marketing copy. These don't need trust — every
row is something you can paste and run in the next five minutes, most with **no API key
at all**, because the mechanism fires before any model call would happen.

### It refuses a dangerous tool combination before spending a cent

```python
from harness import Agent, tool

@tool(effect="external")
def search_web(query: str) -> str:
    """Search the web."""
    return "..."

@tool(effect="danger")
def send_email(to: str, body: str) -> str:
    """Send an email."""
    return "sent"

Agent(name="Bad", job="Research and email a summary.", tools=[search_web, send_email])
```

```
This helper can read things from the internet AND do something it can't undo.

  search_web  can bring in words from a website
  send_email  can't be undone

  A website could trick your helper into using send_email on your stuff.

  Pick one:
    1. Take one of them out, or make two separate helpers.  ← easiest
    2. If send_email really is safe, an OPERATOR says so — not the tool's own code:
         Agent(..., accepts_tainted=["send_email"])
```

This is the "lethal trifecta" (read + untrusted content + irreversible action) — the
research the project cites names it as the pattern most frameworks get wrong: Cline
[confuses approval with isolation](docs/17-research-alignment.md) (an approval prompt is
not a safety boundary — a human can't audit a wall of fetched text), and several others
leave permission modeling to the application author entirely. Here it's a **constructor
error**, not a runtime surprise discovered after a website has already talked your agent
into emailing something. If the combination really is safe, you say so explicitly
(`accepts_tainted=[...]`) — the harness never guesses "probably fine."

### It won't let a tool go unclassified

```python
from harness import tool

@tool()
def send_email(to: str) -> str:
    """Send an email."""
    return "sent"
```

```
Your tool needs to say what it does in the world.

    @tool(effect="danger")     ← probably this one, from the name
    def send_email(...):

  read      only looks at things
  write     changes something you could undo
  external  brings in stuff from the internet
  danger    does something you can't undo
```

One required word (`effect=`) drives five downstream behaviors for free: whether it can
run in parallel with other calls, whether a failure gets retried, what it's allowed to
touch when the conversation has read something untrusted, whether it needs a human's
"yes," and how loudly it's logged. You classify once; you never hand-write five separate
guards. Frameworks that don't ship this (the study's write-up names Pi specifically —
"must build your own permission model, sandbox, MCP integration, subagent system") push
that whole design problem onto every application built on them.

### The budget is checked *before* the call, and it's exact

```python
from harness import Agent
from harness.testing import FakeModel

agent = Agent(name="Helper", job="Answer briefly.", budget="$0.05",
              provider=FakeModel([FakeModel.text("Paris.")]))
result = agent.try_run("What is the capital of France?")
print(result.text, result.cost, result.ok)
```

```
Paris. $0.0000 True
```

The ledger reserves the worst case — counted input tokens × input price + the
budget-derived `max_tokens` × output price — **before** the request goes out, not after
the invoice arrives. A run that would exceed it never starts; one that's already running
stops with the partial answer intact (`.run()` raises `RunFailed`, and `err.partial` still
has whatever was produced — nothing is silently thrown away). Measured under 3,000
adversarial runs with injected token-count drift: the authorization ceiling holds exactly,
and actual spend exceeds it by at most 1.008× in the worst case
([§07.1](docs/07-cost.md#1-the-budget-is-a-ceiling-not-an-alert)) — a number with a test
behind it, not an adjective.

### You test it without an API key or a network connection

```python
from harness import Agent, tool
from harness.testing import FakeModel, Trajectory

@tool(effect="write")
def save_note(text: str) -> str:
    """Save a note."""
    return "saved"

agent = Agent(name="Notes", job="Save notes for the user.", tools=[save_note],
              budget="$0.05",
              provider=FakeModel([FakeModel.tool_call("save_note", {"text": "buy milk"}),
                                  FakeModel.text("done")]))

result = agent.try_run("Remember to buy milk")
report = Trajectory(must_call=frozenset({"save_note"})).check(result)
print(report.ok, result.tools_run)
```

```
True ('save_note',)
```

`FakeModel` scripts exact responses with zero real cost; a project-wide `no_network()`
autouse fixture makes an accidental live call fail the test instead of billing you.
`Trajectory` is a declarative contract — `must_call`, `must_not_call`,
`requires_approval`, `max_model_calls`, `max_tokens`, `max_cost_usd`, `output_schema`,
`no_duplicate_side_effects` — so "did my agent behave correctly" is an assertion, not a
transcript you eyeball. Pair it with `harness.eval.run_golden_set` for a pass rate that
always ships with a 95% confidence interval, never a bare number
([§09](docs/09-testing.md)).

### Third-party tools (MCP) go through the exact same gate as your own

```python
from harness.mcp import McpServerPolicy, bind_mcp_server

policy = McpServerPolicy(identity="some-mcp-server", trusted=False)  # default: DANGER
binding = await bind_mcp_server("path/to/server", policy=policy)
agent = Agent(name="Helper", job="...", tools=[*binding.tools, *my_own_tools])
```

An MCP server's own `readOnlyHint`/`destructiveHint` annotations are, per the protocol's
own spec, unverified self-reports — "clients should never make tool use decisions based
on ToolAnnotations received from untrusted servers." A server you haven't explicitly marked `trusted`
gets every tool classified `danger` by default, gated behind human approval, regardless of
what it claims about itself. A grant for `search` on one server never silently authorizes
`search` on another server with the same name (`Scope.server`) — the exact confused-deputy
class of bug the research found live in more than one existing implementation. Rug-pulled
tools (a server that changes a tool's shape between two `tools/list` calls) fail closed,
not silently.

### The core stays small; everything else is opt-in

```
python -c "import harness"   # 3 hard dependencies, no LangChain, no database driver
```

`langgraph`, `openviking-sdk`, `opentelemetry`, and `starlette` (for the optional HTTP
service) are all extras — `pip install 'harness[graph]'`, `'harness[server]'`, etc. — never
imported by `import harness` itself. The research cites this as the failure mode to avoid
at the other extreme (OpenHands: "operationally heavy" by default). You get a library
first; a service, a durable graph backend, and telemetry are things you opt into when you
actually need them, not things you carry from line one.

### No role/crew tax on multi-agent work

```python
reader = Agent(name="Reader", job="Summarize one page.",
               tools=[fetch], model="claude-haiku-4-5", budget="$0.01")
research = Agent(name="Research", job="Answer using a cheap sub-agent for reading.",
                 tools=[search_web, reader.as_tool()])
```

A sub-agent is just a tool with its own budget, carved out of the parent's remaining
budget — not a new "crew"/"role" abstraction with its own token-explosion failure mode
(the study names this specifically against CrewAI). Delegate to a cheaper model for
reading-heavy work with one line, `.as_tool()`, and the parent's ceiling still holds.

---

## 3. Where the honesty stops being a slogan

This project scores itself against the same weighted rubric the 12/9-framework study
uses — **[self-assessed], not graded by the study's own reviewers**, so treat the number
as a shape (which dimensions are strong) rather than a leaderboard position:
`docs/17-research-alignment.md §1`. It is not a claim that this beats any specific named
framework overall, and two dimensions in that table — real-world developer-experience
testing with people who aren't engineers, and measured production performance numbers —
are explicitly marked as unmeasured until someone actually runs that study or that
deployment. A library that hides which parts of its own claims are unverified would be
the more common thing to ship; this one lists them instead
(`docs/07-risks-and-open-issues.md`).

---

## 4. Going further

| I want to... | Read |
|---|---|
| The gentlest possible walkthrough (written for a 10-year-old, verbatim) | [`docs/15-first-agent.md`](docs/15-first-agent.md) |
| Every parameter, the full export list, the error-message standard | [`docs/03-public-api.md`](docs/03-public-api.md) |
| The full safety model — taint tracking, the policy engine, secrets | [`docs/06-safety.md`](docs/06-safety.md) |
| Multi-turn conversation (`agent.chat()`), approval, delegation | [`docs/03-public-api.md §2`](docs/03-public-api.md#level-2--conversation-approval-memory-delegation-half-a-day) |
| Run an HTTP service in front of an agent (`harness[server]`) | [`src/harness/server.py`](src/harness/server.py) |
| Connect an MCP server as a tool source | [`src/harness/mcp.py`](src/harness/mcp.py) |
| See every claim in this file traced to a test | [`docs/14-validation-plan.md`](docs/14-validation-plan.md) |
