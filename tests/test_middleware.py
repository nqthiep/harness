"""`harness.middleware` — framework-managed base class, composed from three existing
seams (`ModelProvider`, a tool's plain callable, `Exporter`). Two things under test in
every case: (1) each hook gets ONE context object (`ModelCall`/`ToolInvocation`/`Event`),
never a grab bag of positional arguments; (2) a `Middleware` can add
restriction/observation, never bypass what the six core seams already decided — most
importantly, it must never see a tool call `Policy` already denied.
"""
import sys
import unittest

sys.path.insert(0, "src")

from harness import (Agent, Middleware, ModelCall, RunIdentity, ShortCircuit,
                     ToolInvocation, tool, with_middleware)
from harness.models.base import ModelResponse
from harness.models.fake import FakeModel
from harness.result import Usage


@tool(effect="read")
def look_up(order: str) -> dict:
    """Look up an order by id."""
    return {"status": "shipped", "order": order}


@tool(effect="danger")
def wipe(x: int) -> str:
    """Destroy something, irreversibly."""
    return "gone"


class Recorder(Middleware):
    def __init__(self):
        self.calls = []

    def before_model(self, call: ModelCall):
        assert call.response is None            # not decided yet at this point
        self.calls.append(("before_model", len(call.request.messages)))
        return call.request

    def after_model(self, call: ModelCall):
        assert call.response is not None
        self.calls.append(("after_model", call.response.stop_reason))
        return call.response

    def before_tool(self, call: ToolInvocation):
        assert call.result is None               # not run yet at this point
        self.calls.append(("before_tool", call.name, dict(call.kwargs)))
        return call.kwargs

    def after_tool(self, call: ToolInvocation):
        self.calls.append(("after_tool", call.name, call.result))
        return call.result

    def on_event(self, event):
        self.calls.append(("event", event.kind.value))


class ContextObjectShape(unittest.TestCase):
    """The thing that changed: one object per phase, not mismatched positional args."""

    def test_model_call_carries_request_and_response(self):
        seen = {}

        class Peek(Middleware):
            def before_model(self, call):
                seen["before"] = call
                return call.request
            def after_model(self, call):
                seen["after"] = call
                return call.response

        script = [FakeModel.text("hi")]
        a = Agent(name="p", job="x", provider=FakeModel(script))
        with_middleware(a, Peek()).try_run("hello")
        self.assertIsInstance(seen["before"], ModelCall)
        self.assertIsNone(seen["before"].response)
        self.assertIsInstance(seen["after"], ModelCall)
        self.assertIsNotNone(seen["after"].response)
        self.assertIs(seen["before"].request, seen["after"].request)

    def test_tool_invocation_carries_name_kwargs_and_result(self):
        seen = {}

        class Peek(Middleware):
            def before_tool(self, call):
                seen["before"] = call
                return call.kwargs
            def after_tool(self, call):
                seen["after"] = call
                return call.result

        script = [FakeModel.tool_call("look_up", {"order": "A1"}), FakeModel.text("ok")]
        a = Agent(name="p", job="x", provider=FakeModel(script), tools=[look_up],
                 allowed_hosts=None)
        with_middleware(a, Peek()).try_run("check A1")
        self.assertIsInstance(seen["before"], ToolInvocation)
        self.assertEqual(seen["before"].name, "look_up")
        self.assertEqual(dict(seen["before"].kwargs), {"order": "A1"})
        self.assertIsNone(seen["before"].result)
        self.assertIsInstance(seen["after"], ToolInvocation)
        self.assertEqual(seen["after"].result, {"status": "shipped", "order": "A1"})

    def test_model_call_and_tool_invocation_are_frozen(self):
        call = ModelCall(request=None)             # type: ignore[arg-type]
        with self.assertRaises(AttributeError):
            call.response = "x"                     # type: ignore[misc]
        inv = ToolInvocation(name="x", kwargs={})
        with self.assertRaises(AttributeError):
            inv.result = "y"                        # type: ignore[misc]

    def test_identity_is_empty_outside_a_real_run(self):
        """Constructing either type by hand (a test, or code outside `with_middleware()`
        entirely) gets an all-`None` `RunIdentity` — never a guessed or placeholder
        value."""
        call = ModelCall(request=None)              # type: ignore[arg-type]
        self.assertEqual(call.identity, RunIdentity())
        inv = ToolInvocation(name="x", kwargs={})
        self.assertEqual(inv.identity, RunIdentity())


class IdentityThreading(unittest.TestCase):
    """`.identity` on `ModelCall`/`ToolInvocation` — the thing that needed core files
    touched (`run.py`/`dispatch.py`/`lg/runtime.py`) to do correctly, verified against
    the real `langgraph` dependency rather than assumed: LangGraph's own executor
    (`pregel/_executor.py`) copies the `contextvars.Context` before dispatching a sync
    node, and `asyncio.to_thread` (`tools/__init__.py`'s sync-tool wrapper) does the
    same — a value set once per run survives all the way to a tool's own function body,
    on both engines.
    """

    def test_classic_backend_carries_run_id_session_id_tenant_id(self):
        seen = []

        class Peek(Middleware):
            def before_model(self, call):
                seen.append(call.identity)
                return call.request

        script = [FakeModel.text("hi")]
        a = Agent(name="p", job="x", provider=FakeModel(script), tenant_id="acme",
                 session_id="sess-A")
        r = with_middleware(a, Peek()).try_run("hello")
        self.assertEqual(seen[0].session_id, "sess-A")
        self.assertEqual(seen[0].tenant_id, "acme")
        self.assertEqual(seen[0].run_id, r.run_id)     # same id Result reports

    def test_durable_backend_carries_the_same_fields(self):
        import tempfile
        seen = []

        class Peek(Middleware):
            def before_model(self, call):
                seen.append(call.identity)
                return call.request

        db = tempfile.mktemp(suffix=".sqlite3")
        a = Agent(name="p", job="x", provider=FakeModel([FakeModel.text("hi")]),
                 durable=True, checkpoint=db, allowed_hosts=None, tenant_id="acme",
                 session_id="sess-B")
        r = with_middleware(a, Peek()).try_run("hello")
        self.assertEqual(seen[0].session_id, "sess-B")
        self.assertEqual(seen[0].tenant_id, "acme")
        self.assertEqual(seen[0].run_id, "sess-B")      # thread_id IS the run_id here
        self.assertEqual(seen[0].run_id, r.run_id)

    def test_tool_calls_carry_their_own_call_id_and_step(self):
        seen = []

        class Peek(Middleware):
            def before_tool(self, call):
                seen.append((call.identity.call_id, call.identity.step))
                return call.kwargs

        script = [FakeModel.tool_call("look_up", {"order": "A1"}), FakeModel.text("ok")]
        a = Agent(name="p", job="x", provider=FakeModel(script), tools=[look_up],
                 allowed_hosts=None)
        with_middleware(a, Peek()).try_run("check A1")
        self.assertEqual(seen, [("c1", 0)])

    def test_model_calls_have_no_call_id(self):
        seen = []

        class Peek(Middleware):
            def before_model(self, call):
                seen.append(call.identity.call_id)
                return call.request

        a = Agent(name="p", job="x", provider=FakeModel([FakeModel.text("hi")]))
        with_middleware(a, Peek()).try_run("hello")
        self.assertEqual(seen, [None])

    def test_concurrent_tool_calls_do_not_cross_talk(self):
        """Three `read` tools in one assistant turn run in PARALLEL
        (`dispatch.py::_bounded`, `asyncio.gather`) — each concurrent `asyncio.Task`
        gets its own copy of the `contextvars.Context`, so one call's `call_id` must
        never leak into a sibling's."""
        seen = {}

        class Peek(Middleware):
            def before_tool(self, call):
                seen[call.identity.call_id] = dict(call.kwargs)
                return call.kwargs

        resp = ModelResponse(
            tuple({"type": "tool_use", "id": f"c{i}", "name": "look_up",
                  "input": {"order": f"A{i}"}} for i in (1, 2, 3)),
            "tool_use", Usage(100, 15), "fake")
        a = Agent(name="p", job="x", provider=FakeModel([resp, FakeModel.text("done")]),
                 tools=[look_up], allowed_hosts=None)
        with_middleware(a, Peek()).try_run("go")
        self.assertEqual(seen, {"c1": {"order": "A1"}, "c2": {"order": "A2"},
                               "c3": {"order": "A3"}})


class MiddlewareBasics(unittest.TestCase):
    def test_default_hooks_are_pass_through(self):
        """A bare Middleware() — no overrides — changes nothing: proof the base class
        itself is inert, so subclassing and overriding one hook is genuinely opt-in."""
        script = [FakeModel.tool_call("look_up", {"order": "A1"}),
                  FakeModel.text("Order A1: shipped.")]
        a = Agent(name="p", job="x", provider=FakeModel(script), tools=[look_up],
                 allowed_hosts=None)
        m = with_middleware(a, Middleware())
        r = m.try_run("check A1")
        self.assertTrue(r.ok)
        self.assertEqual(r.text, "Order A1: shipped.")
        self.assertEqual(r.tools_run, ("look_up",))

    def test_no_middlewares_returns_the_same_agent(self):
        a = Agent(name="p", job="x", provider=FakeModel([]))
        self.assertIs(with_middleware(a), a)

    def test_before_and_after_model_fire_once_per_model_call(self):
        script = [FakeModel.tool_call("look_up", {"order": "A1"}),
                  FakeModel.text("done")]
        a = Agent(name="p", job="x", provider=FakeModel(script), tools=[look_up],
                 allowed_hosts=None)
        rec = Recorder()
        r = with_middleware(a, rec).try_run("check A1")
        self.assertTrue(r.ok)
        before = [c for c in rec.calls if c[0] == "before_model"]
        after = [c for c in rec.calls if c[0] == "after_model"]
        self.assertEqual(len(before), 2)      # one per model turn (tool call + answer)
        self.assertEqual(len(after), 2)

    def test_before_and_after_tool_see_the_real_call(self):
        script = [FakeModel.tool_call("look_up", {"order": "A1"}), FakeModel.text("ok")]
        a = Agent(name="p", job="x", provider=FakeModel(script), tools=[look_up],
                 allowed_hosts=None)
        rec = Recorder()
        with_middleware(a, rec).try_run("check A1")
        self.assertIn(("before_tool", "look_up", {"order": "A1"}), rec.calls)
        after_tool = [c for c in rec.calls if c[0] == "after_tool"]
        self.assertEqual(len(after_tool), 1)
        self.assertEqual(after_tool[0][1], "look_up")

    def test_on_event_sees_the_same_stream_an_exporter_would(self):
        script = [FakeModel.text("hi")]
        a = Agent(name="p", job="x", provider=FakeModel(script))
        rec = Recorder()
        with_middleware(a, rec).try_run("hello")
        kinds = [c[1] for c in rec.calls if c[0] == "event"]
        self.assertIn("run.started", kinds)
        self.assertIn("run.finished", kinds)

    def test_middlewares_run_in_the_given_order(self):
        order = []

        class First(Middleware):
            def before_tool(self, call):
                order.append("first"); return call.kwargs

        class Second(Middleware):
            def before_tool(self, call):
                order.append("second"); return call.kwargs

        script = [FakeModel.tool_call("look_up", {"order": "A1"}), FakeModel.text("ok")]
        a = Agent(name="p", job="x", provider=FakeModel(script), tools=[look_up],
                 allowed_hosts=None)
        with_middleware(a, First(), Second()).try_run("check A1")
        self.assertEqual(order, ["first", "second"])


class ShortCircuiting(unittest.TestCase):
    def test_before_tool_short_circuit_skips_the_real_function(self):
        ran = []

        @tool(effect="read")
        def expensive(x: int) -> str:
            """Do something that should never actually run here."""
            ran.append(x)
            return "real result"

        class Cache(Middleware):
            def before_tool(self, call):
                if call.name == "expensive":
                    raise ShortCircuit("cached result")
                return call.kwargs

        script = [FakeModel.tool_call("expensive", {"x": 1}), FakeModel.text("ok")]
        a = Agent(name="p", job="x", provider=FakeModel(script), tools=[expensive],
                 allowed_hosts=None)
        r = with_middleware(a, Cache()).try_run("go")
        self.assertTrue(r.ok)
        self.assertEqual(ran, [])                    # the real function never ran
        joined = " ".join(str(b.get("content", "")) for m in r.messages
                          if isinstance(m, dict) and isinstance(m.get("content"), list)
                          for b in m["content"] if isinstance(b, dict))
        self.assertIn("cached result", joined)

    def test_after_tool_can_replace_the_result(self):
        class Redactor(Middleware):
            def after_tool(self, call):
                return "[redacted]"

        script = [FakeModel.tool_call("look_up", {"order": "A1"}), FakeModel.text("ok")]
        a = Agent(name="p", job="x", provider=FakeModel(script), tools=[look_up],
                 allowed_hosts=None)
        r = with_middleware(a, Redactor()).try_run("check A1")
        joined = " ".join(str(b.get("content", "")) for m in r.messages
                          if isinstance(m, dict) and isinstance(m.get("content"), list)
                          for b in m["content"] if isinstance(b, dict))
        self.assertIn("[redacted]", joined)
        self.assertNotIn("shipped", joined)


class CannotBypassCoreEnforcement(unittest.TestCase):
    """The one property that has to hold or this whole module is a security regression:
    a Middleware only ever sees what the six core seams already allowed through."""

    def test_before_tool_never_sees_a_policy_denied_call(self):
        rec = Recorder()
        script = [FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("done")]
        a = Agent(name="p", job="x", provider=FakeModel(script), tools=[wipe],
                 allowed_hosts=None)                  # no approve= -> danger denied
        r = with_middleware(a, rec).try_run("wipe it")
        self.assertTrue(r.ok)
        self.assertEqual(r.tools_run, (), "the danger tool ran with no approve= callback")
        self.assertFalse(any(c[0] == "before_tool" for c in rec.calls),
                         "middleware saw a call Policy had already denied")

    def test_a_middleware_cannot_override_the_deny_by_returning_kwargs(self):
        """before_tool returning kwargs (even unmodified) is not a vote to allow — the
        hook is never invoked at all for a denied call, so there's nothing to "return
        ALLOW" from."""
        class NeverDenies(Middleware):
            def before_tool(self, call):
                return call.kwargs        # would-be "approve everything", if it ran

        script = [FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("done")]
        a = Agent(name="p", job="x", provider=FakeModel(script), tools=[wipe],
                 allowed_hosts=None)
        r = with_middleware(a, NeverDenies()).try_run("wipe it")
        self.assertEqual(r.tools_run, ())


class ComposesWithAgent(unittest.TestCase):
    def test_original_agent_is_unchanged(self):
        a = Agent(name="p", job="x", provider=FakeModel([FakeModel.text("a")]))
        b = with_middleware(a, Recorder())
        self.assertIsNot(a, b)
        self.assertIs(a.provider, a.provider)         # untouched
        self.assertEqual(a.exporters, ())

    def test_works_with_durable_true(self):
        import tempfile
        db = tempfile.mktemp(suffix=".sqlite3")
        rec = Recorder()
        a = Agent(name="p", job="x", provider=FakeModel([FakeModel.text("hi")]),
                 durable=True, checkpoint=db, allowed_hosts=None)
        r = with_middleware(a, rec).try_run("hello")
        self.assertTrue(r.ok)
        self.assertTrue(any(c[0] == "before_model" for c in rec.calls))


if __name__ == "__main__":
    unittest.main()
