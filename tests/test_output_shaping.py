"""`output_shaping.py` — fixing a real, measured bug in the "read the actual failure"
workflow. `HeadOnlyTruncationLosesTheFailure` reproduces the bug in `dispatch.py::
truncate()` ITSELF (core, not the fix) first — proof the problem is real before proving
the fix works, the same "reproduce, then fix" discipline `docs/09-testing.md` asks for
red-team findings.
"""
import sys
import unittest

sys.path.insert(0, "src")
sys.path.insert(0, "examples")


def _big_pytest_style_output(n_passing: int = 500) -> str:
    """Shaped like real `pytest -v` output: many PASSED lines, then a FAILURES section
    with the actual error at the bottom — the exact shape that broke in practice."""
    lines = [f"test_big.py::test_pass[{i}] PASSED" + " " * 20 + f"[{i}%]"
            for i in range(n_passing)]
    lines += [
        "",
        "=================================== FAILURES ===================================",
        "________________________________ test_the_real_bug _____________________________",
        "",
        "    def test_the_real_bug():",
        '        assert 1 == 5, "this is the actual bug the agent needs to see"',
        "E       AssertionError: this is the actual bug the agent needs to see",
        "",
        "test_big.py:9: AssertionError",
        "=========================== short test summary info ============================",
        "FAILED test_big.py::test_the_real_bug - AssertionError: ...",
        f"1 failed, {n_passing} passed in 0.45s",
    ]
    return "\n".join(lines)


class HeadOnlyTruncationLosesTheFailure(unittest.TestCase):
    """Reproduces the bug in CORE `dispatch.py::truncate()`, unpatched — establishes
    the problem is real (measured at 41,085 chars against this repo's own pytest in the
    session that found this) before testing the fix below."""

    def test_the_bug_is_real_in_dispatch_truncate(self):
        from harness.dispatch import truncate

        text = _big_pytest_style_output()
        self.assertGreater(len(text), 4_000 * 4, "fixture must exceed the default cap")
        kept, was_truncated = truncate(text, 4_000)
        self.assertTrue(was_truncated)
        self.assertNotIn("FAILURES", kept)
        self.assertNotIn("this is the actual bug", kept)


class SmartTruncateThat(unittest.TestCase):
    def test_short_text_is_returned_unchanged(self):
        from output_shaping import smart_truncate
        text = "short output, well under the limit"
        self.assertEqual(smart_truncate(text, 4_000), text)

    def test_keeps_the_failure_a_head_only_cut_would_lose(self):
        from output_shaping import smart_truncate
        text = _big_pytest_style_output()
        out = smart_truncate(text, 4_000)
        self.assertIn("FAILURES", out)
        self.assertIn("this is the actual bug", out)

    def test_also_keeps_something_from_the_head(self):
        from output_shaping import smart_truncate
        text = _big_pytest_style_output()
        out = smart_truncate(text, 4_000)
        self.assertIn("test_pass[0] PASSED", out)

    def test_stays_within_budget(self):
        from output_shaping import smart_truncate
        text = _big_pytest_style_output(n_passing=5000)
        out = smart_truncate(text, 4_000)
        # Generous slack for the head/tail markers themselves, not a tight bound.
        self.assertLess(len(out), 4_000 * 4 + 400)

    def test_never_splits_a_multibyte_character(self):
        from output_shaping import smart_truncate
        text = ("x" * 20_000) + "🎉" * 100 + ("y" * 20_000)
        out = smart_truncate(text, 100)          # tiny budget, forces a cut near emoji
        out.encode("utf-8")                      # raises if a surrogate/half-char leaked


class WithSmartTruncationThat(unittest.TestCase):
    def test_only_wraps_named_tools(self):
        import asyncio

        from harness import tool
        from output_shaping import with_smart_truncation

        @tool(effect="read")
        async def big_one() -> str:
            """big"""
            return _big_pytest_style_output()

        @tool(effect="read")
        async def small_one() -> str:
            """small"""
            return _big_pytest_style_output()

        wrapped = with_smart_truncation([big_one, small_one], tools=("big_one",))
        specs = {s.name: s for s in wrapped}

        out_big = asyncio.run(specs["big_one"].fn())
        self.assertIn("this is the actual bug", out_big)
        self.assertEqual(specs["big_one"].max_result_tokens, 12_000)

        # untouched: same fn, same default max_result_tokens
        self.assertIs(specs["small_one"].fn, small_one.fn)
        self.assertEqual(specs["small_one"].max_result_tokens, small_one.max_result_tokens)

    def test_wrapping_preserves_effect_and_other_fields(self):
        from harness import Effect, tool
        from output_shaping import with_smart_truncation

        @tool(effect="write", timeout_s=99.0)
        async def run_something() -> str:
            """run"""
            return "ok"

        wrapped = with_smart_truncation([run_something], tools=("run_something",))[0]
        self.assertEqual(wrapped.effect, Effect.WRITE)
        self.assertEqual(wrapped.timeout_s, 99.0)

    def test_composes_with_dispatch_truncate_without_double_cutting(self):
        """The whole point: after with_smart_truncation, dispatch.py::truncate() (the
        core pass that still runs afterward) sees output already under the raised
        ceiling and passes it through UNCHANGED — the failure is not cut a second time
        by the generic head-only pass."""
        import asyncio

        from harness.dispatch import truncate
        from harness import tool
        from output_shaping import with_smart_truncation

        @tool(effect="read")
        async def big_one() -> str:
            """big"""
            return _big_pytest_style_output()

        spec = with_smart_truncation([big_one], tools=("big_one",))[0]
        shaped = asyncio.run(spec.fn())
        final, was_truncated_again = truncate(shaped, spec.max_result_tokens)
        self.assertFalse(was_truncated_again)
        self.assertIn("this is the actual bug", final)


if __name__ == "__main__":
    unittest.main()
