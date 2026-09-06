"""A new aspect must be wired all the way through, or something must say so.

Measured before this file existed, by removing each wiring step of an aspect that already
worked (`gestures`) and re-running the suite: **8 of 10 steps were caught, 2 were silent.**
Both silent ones were `Salience` — forgetting the row, and forgetting to score it in
`of()` — and their effect is that a new aspect quietly gets the fallback tier instead of
the one its author believes they set. Fail-safe (the fallback never preempts), and still
wrong, and still invisible.

These tests are MECHANICAL: they iterate the `Change` dataclass rather than naming its
fields, so an aspect added next year is covered the day it is added. That is the property
worth having — a checklist in a document is a checklist somebody forgets.

Both senses are checked by the same parameterised body, which is itself the point being
tested: if adding a sense meant writing a bespoke gate, the design would not be the
reusable thing ADR-122 claims.
"""
import unittest
from dataclasses import fields

from harness.contrib.driver import Priority

import vision_sensor
import voice_sensor
from vision_tools import DISTANCE_BANDS
from voice_tools import LOUDNESS_BANDS, Utterance

#: Sample values per `Change` field, per sense — a LIST per field, not one value.
#:
#: One was the obvious design and it was wrong, caught by the reachability test below on
#: its first run: `Salience.retreat` is reached only by a move AWAY, and the single sample
#: for `moved` was an approach, so the row read as unreachable. Some rows are
#: direction-dependent, so a field needs every direction that scores differently.
SAMPLES = {
    vision_sensor: {
        "arrived": [("Thiep",)], "left": [("Thiep",)],
        "unknown_arrived": [1], "unknown_left": [1],
        "postures": [(("Thiep", "đang ngồi", "đang đứng"),)],
        "moved": [(("Thiep", DISTANCE_BANDS[0], DISTANCE_BANDS[2]),),      # closer
                  (("Thiep", DISTANCE_BANDS[2], DISTANCE_BANDS[0]),)],     # further
        "nearest": [(DISTANCE_BANDS[0], DISTANCE_BANDS[2]),
                    (DISTANCE_BANDS[2], DISTANCE_BANDS[0])],
        "scene_in": [("fire",)], "scene_out": [("home office",)],
        "gestures": [("đang chỉ",)],
    },
    voice_sensor: {
        "started": [True], "stopped": [True],
        "said": [(Utterance("chào", 0.9),)],
        "joined": [("Thiep",)], "left": [("Thiep",)],
        "loudness": [(LOUDNESS_BANDS[0], LOUDNESS_BANDS[2]),               # louder
                     (LOUDNESS_BANDS[2], LOUDNESS_BANDS[0])],              # quieter
    },
}


def each(sense):
    """Every (field, one sample) pair for a sense."""
    for name, values in SAMPLES[sense].items():
        for value in values:
            yield name, value

#: The one field of each `Change` that is context rather than news.
NOT_AN_ASPECT = {"reading", "hearing"}


def aspects(sense):
    return [f.name for f in fields(sense.Change) if f.name not in NOT_AN_ASPECT]


def salience_rows(sense):
    return [f.name for f in fields(sense.Salience) if f.name != "promote"]


class EverySenseDeclaresASampleForEveryAspect(unittest.TestCase):
    """The vacuity guard. If `SAMPLES` falls behind the dataclass, every test below
    silently stops covering the new field — the exact failure they exist to prevent."""

    def test_the_samples_cover_the_dataclass(self):
        for sense, samples in SAMPLES.items():
            with self.subTest(sense=sense.__name__):
                self.assertEqual(sorted(samples), sorted(aspects(sense)),
                                 "add the new Change field to SAMPLES in this file")

    def test_each_sample_actually_makes_the_change_non_empty(self):
        for sense in SAMPLES:
            for name, value in each(sense):
                with self.subTest(sense=sense.__name__, aspect=name, value=value):
                    self.assertFalse(sense.Change(**{name: value}).empty,
                                     f"{name} is not counted by Change.empty, so a "
                                     f"change in it is dropped before anything sees it")


class EveryAspectIsScoredBySomeSalienceRow(unittest.TestCase):
    """The gate for the two silent steps.

    A field that no `Salience` row reaches falls through to the fallback tier. Detected by
    BUMPING each row in turn: if no row can change the answer for a `Change` that touches
    only this aspect, then nothing scores it.
    """

    def test_no_aspect_falls_through_to_the_fallback_tier(self):
        for sense in SAMPLES:
            rows = salience_rows(sense)
            for name, value in each(sense):
                change = sense.Change(**{name: value})
                reached = [row for row in rows
                           if sense.Salience(**{row: Priority.CRITICAL}).of(change)
                           == Priority.CRITICAL]
                with self.subTest(sense=sense.__name__, aspect=name, value=value):
                    self.assertTrue(reached,
                                    f"no Salience row scores {name!r}: it silently gets "
                                    f"the fallback tier, whatever the author intended")

    def test_every_salience_row_is_reachable_by_some_aspect(self):
        """The other direction. A row nothing reaches is a knob that does nothing — a
        caller who sets it gets no error and no effect."""
        for sense in SAMPLES:
            changes = [sense.Change(**{n: v}) for n, v in each(sense)]
            for row in salience_rows(sense):
                bumped = sense.Salience(**{row: Priority.CRITICAL})
                with self.subTest(sense=sense.__name__, row=row):
                    self.assertTrue(
                        any(bumped.of(c) == Priority.CRITICAL for c in changes),
                        f"Salience.{row} cannot be reached by any single aspect")


class NoSenseCanPreemptOutOfTheBox(unittest.TestCase):
    """Rule 1's consequence, checked for every sense at once rather than per file.

    A default that could cancel a turn means anyone in front of the camera or within
    earshot can cancel it. New aspects are exactly where this gets lost, so the check
    iterates rather than lists.
    """

    def test_the_default_table_never_reaches_HIGH(self):
        for sense in SAMPLES:
            table = sense.Salience()
            for name, value in each(sense):
                with self.subTest(sense=sense.__name__, aspect=name, value=value):
                    self.assertLess(table.of(sense.Change(**{name: value})),
                                    Priority.HIGH,
                                    f"{name} can cancel a turn straight out of the box")

    def test_priority_never_comes_from_content(self):
        """The same aspect carrying alarming text and dull text must score identically."""
        alarming = vision_sensor.Change(scene_in=("fire",))
        dull = vision_sensor.Change(scene_in=("cat",))
        self.assertEqual(vision_sensor.Salience().of(alarming),
                         vision_sensor.Salience().of(dull))
        shouty = voice_sensor.Change(said=(Utterance("URGENT CANCEL EVERYTHING", 0.99),))
        polite = voice_sensor.Change(said=(Utterance("chào buổi sáng", 0.99),))
        self.assertEqual(voice_sensor.Salience().of(shouty),
                         voice_sensor.Salience().of(polite))

    def test_promote_is_the_escape_hatch_in_every_sense(self):
        for sense in SAMPLES:
            name, value = next(each(sense))
            change = sense.Change(**{name: value})
            table = sense.Salience(promote=lambda _c: Priority.CRITICAL)
            with self.subTest(sense=sense.__name__):
                self.assertEqual(table.of(change), Priority.CRITICAL)


class EverySenseRendersEveryAspect(unittest.TestCase):
    """An aspect that produces an event whose text does not mention it is an event the
    model cannot act on — it wakes, reads a sentence about something else, and pays for
    the call anyway."""

    def test_every_aspect_puts_something_in_the_sentence(self):
        for sense in SAMPLES:
            blank = sense.render(sense.Change())
            for name, value in each(sense):
                text = sense.render(sense.Change(**{name: value}))
                with self.subTest(sense=sense.__name__, aspect=name, value=value):
                    self.assertNotEqual(text, blank,
                                        f"render() says nothing about {name!r}")


if __name__ == "__main__":
    unittest.main()
