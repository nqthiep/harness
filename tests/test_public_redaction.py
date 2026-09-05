"""The unwrapped-key scan `docs/06-safety.md:276` promised — F15b.

The doc: *"The redactor also scans for high-entropy strings matching known key formats
(`sk-ant-`, AWS, GitHub, Slack) even when they were never wrapped, and emits a
`error.raised` warning naming the event where one appeared."*  Register #23 grades it
"loud warning".  Measured before this existed:

    grep -rni "entropy" src/          -> zero hits
    redact("sk-ant-api03-AAAA...")    -> unchanged
    error.raised events               -> []
    tool_result the model received    -> "here you go: sk-ant-api03-AAAA..."

Half of that sentence is now true and half is still not, and this file pins both halves
so neither can be quietly re-described:

  * the four named formats are redacted, unwrapped, on every write path;
  * the `error.raised` warning is NOT emitted — `secrets.py` is a pure function with no
    `EventBus`.  `unwrapped_key_kinds()` is the seam for that one-line emit, and it is
    tested here so the emit has something to read when it is wired.

The negative cases matter more than the positive ones.  Redaction runs on EVERY write, so
a false positive silently corrupts the model's input; a general entropy classifier fires
on hashes, base64 and UUIDs, which are ordinary tool output.  `test_what_must_never_fire`
is the test that keeps the scope honest.
"""
from __future__ import annotations

import unittest

from harness.secrets import (_KEY_MARKERS, _KEY_PATTERNS, Secret, redact,
                             redaction_scope, scan_for_unwrapped_keys,
                             unwrapped_key_kinds)

#: One realistic sample per declared format.  Fake values, real shapes.
SAMPLES = {
    "anthropic": "sk-ant-api03-" + "A" * 95,
    "aws": "AKIAIOSFODNN7EXAMPLE",
    "github": "ghp_" + "b" * 36,
    "slack": "xoxb-123456789012-123456789012-abcdefghijklmnopqrstuvwx",
}

#: What an entropy classifier gets wrong, and what this scan must therefore leave alone.
MUST_NOT_FIRE = {
    "sha256 digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "base64 payload": "TWFuIGlzIGRpc3Rpbmd1aXNoZWQsIG5vdCBvbmx5IGJ5IGhpcyByZWFzb24sIGJ1dA==",
    "uuid4": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "jwt body": "eyJzdWIiOiIxMjM0NTY3ODkwIiwibmFtZSI6IkpvaG4gRG9lIiwiaWF0IjoxNTE2MjM5MDIyfQ",
    # No prefix at all, 40 base64 characters. Matching this IS the classifier this
    # refuses to be, so it is a documented miss rather than an oversight.
    "aws secret access key": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    "short placeholder": "sk-ant-good",
    "english prose with gh": "Although the night was bright, going through the high pass",
    "a git sha": "9f2c1ab4e7d3b6580c1e4a2f8d9b3c7e1a5f0d24",
}


class TheScanFires(unittest.TestCase):

    def test_every_declared_format_is_redacted_unwrapped(self):
        for kind, sample in SAMPLES.items():
            with self.subTest(kind=kind):
                out = redact(f"here you go: {sample}")
                self.assertNotIn(sample, out, f"{kind} key survived redaction")
                self.assertIn(f"<possible {kind} key hidden>", out)

    def test_the_four_formats_the_doc_names_are_the_four_implemented(self):
        self.assertEqual({k for k, _ in _KEY_PATTERNS},
                         {"anthropic", "aws", "github", "slack"})

    def test_a_key_in_the_middle_of_real_output_is_found(self):
        text = f'{{"ok": true, "note": "use {SAMPLES["anthropic"]} for staging"}}'
        self.assertNotIn(SAMPLES["anthropic"], redact(text))
        self.assertIn('"ok": true', redact(text), "it must not eat the rest of the line")

    def test_two_different_formats_in_one_string_are_both_found(self):
        out = redact(f"{SAMPLES['github']} and {SAMPLES['slack']}")
        self.assertIn("<possible github key hidden>", out)
        self.assertIn("<possible slack key hidden>", out)


class TheScanStaysInScope(unittest.TestCase):

    def test_what_must_never_fire(self):
        """A false positive here silently corrupts what the model reads."""
        for label, sample in MUST_NOT_FIRE.items():
            with self.subTest(case=label):
                self.assertEqual(redact(sample), sample,
                                 f"{label} was redacted; it is ordinary output")
                self.assertEqual(scan_for_unwrapped_keys(sample), ())

    def test_the_prefilter_can_reach_every_pattern(self):
        """`redact()` skips the regex unless a marker appears.  A pattern with no marker
        that reaches it would be dead code that looks like a control."""
        for kind, sample in SAMPLES.items():
            with self.subTest(kind=kind):
                self.assertTrue(any(m in sample for m in _KEY_MARKERS),
                                f"no marker in _KEY_MARKERS reaches a {kind} key")

    def test_the_prefilter_is_not_shortened_into_common_english(self):
        """`"gh"` as a marker made every sentence containing `night` or `through` pay for
        the regex: 51.28 us/call against 4.79 with the full prefixes."""
        prose = "Although the night was bright, going through the high pass, twice."
        self.assertFalse(any(m in prose for m in _KEY_MARKERS), _KEY_MARKERS)


class TheRegisteredSecretPathIsUnchanged(unittest.TestCase):

    def test_a_wrapped_secret_still_redacts_by_its_own_name(self):
        s = Secret("hunter2-the-whole-value", name="db_password")
        self.assertEqual(redact("pw is hunter2-the-whole-value"), "pw is <db_password hidden>")
        del s

    def test_a_wrapped_secret_wins_over_the_format_scan(self):
        """Exact beats best-effort: the name the user chose is more useful than a guess."""
        s = Secret(SAMPLES["anthropic"], name="staging_key")
        out = redact(f"key: {SAMPLES['anthropic']}")
        self.assertIn("<staging_key hidden>", out)
        self.assertNotIn("possible anthropic", out)
        del s


class TheEmitSeam(unittest.TestCase):
    """The half that is still missing, held open so wiring it is one line."""

    def test_kinds_are_recorded_inside_a_run(self):
        with redaction_scope():
            redact(f"a {SAMPLES['anthropic']} and a {SAMPLES['github']}")
            self.assertEqual(unwrapped_key_kinds(), frozenset({"anthropic", "github"}))

    def test_the_record_is_cleared_when_the_run_ends(self):
        with redaction_scope():
            redact(SAMPLES["aws"])
        self.assertEqual(unwrapped_key_kinds(), frozenset())

    def test_it_records_names_and_never_values(self):
        with redaction_scope():
            redact(SAMPLES["slack"])
            for kind in unwrapped_key_kinds():
                self.assertNotIn(kind, SAMPLES["slack"].split("-")[-1])
                self.assertIn(kind, {k for k, _ in _KEY_PATTERNS})

    def test_redaction_works_with_no_scope_open(self):
        """`redact()` is called from places that are not inside a run."""
        self.assertNotIn(SAMPLES["aws"], redact(SAMPLES["aws"]))


if __name__ == "__main__":                                      # pragma: no cover
    unittest.main()
