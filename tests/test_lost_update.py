"""Two writers on one key, and what `Store` can and cannot do about it.

`Store` is whole-blob get/put with no compare-and-swap. Every ledger built on it does
read-modify-write, so two writers silently lose one update. Both ledgers in this
repository carried a paragraph explaining why that could not happen to them: their
mutating tools are `write`/`danger`, neither `parallel_safe`, so they serialise within a
step. The argument was true and it stopped being true when `harness.contrib.Driver`
started running a background pump (ADR-100).

An argument for why a race cannot happen is worth less than a check that says so when it
does — including the test at the bottom, which pins down the case the check still misses.
"""
import asyncio
import json
import unittest

from harness.memory.base import ConcurrentWriteError, read_modify_write
from harness.memory.inmemory import InMemoryStore
from harness.tasks import TaskLedger
from vision_tools import IdentityLedger


class Interfering(InMemoryStore):
    """A store that lets somebody else's write land at a chosen moment.

    `after_reads` counts `get` calls; when it hits the trigger, `intruder` is written
    straight into the backing store — which is exactly what a second writer racing this
    one would do.
    """

    def __init__(self, *, trigger: int, intruder: str) -> None:
        super().__init__()
        self.trigger, self.intruder, self.reads = trigger, intruder, 0

    async def get(self, key: str):
        self.reads += 1
        if self.reads == self.trigger:
            await super().put(key, self.intruder)
        return await super().get(key)


def run(coro):
    return asyncio.run(coro)


class TheCheckItself(unittest.TestCase):
    def test_an_uncontended_write_goes_through(self):
        store = InMemoryStore()
        out = run(read_modify_write(store, "k", lambda raw: "one"))
        self.assertEqual(out, "one")
        self.assertEqual(run(store.get("k")), "one")

    def test_a_write_that_would_discard_somebody_elses_change_raises(self):
        """The intruder lands after this caller has read and before it writes."""
        store = Interfering(trigger=2, intruder="theirs")
        with self.assertRaises(ConcurrentWriteError) as ctx:
            run(read_modify_write(store, "k", lambda raw: "mine", what="the book"))
        message = str(ctx.exception)
        self.assertIn("the book", message)
        self.assertIn("no compare-and-swap", message)
        self.assertEqual(run(InMemoryStore.get(store, "k")), "theirs",
                         "their change survives; ours is refused, not silently applied")

    def test_a_mutation_that_writes_nothing_cannot_conflict(self):
        """`mutate` returning `None` means "no change" — there is nothing to lose, so it
        returns before the re-read rather than raising on somebody else's write."""
        store = Interfering(trigger=2, intruder="theirs")
        out = run(read_modify_write(store, "k", lambda raw: None))
        self.assertIsNone(out)

    def test_the_window_it_cannot_close_is_named_and_real(self):
        """The docstring says this detects rather than prevents. Here is the case it
        misses: a write that lands AFTER the verification read. Asserted so the
        limitation is a tested fact rather than a modest-sounding sentence — and so
        nobody builds on this thinking it is compare-and-swap.
        """
        store = Interfering(trigger=3, intruder="theirs")   # 3rd get == the re-read
        out = run(read_modify_write(store, "k", lambda raw: "mine"))
        self.assertEqual(out, "mine")
        self.assertEqual(run(InMemoryStore.get(store, "k")), "mine",
                         "their update is lost, and no error was raised")


class TheTaskLedger(unittest.IsolatedAsyncioTestCase):
    async def test_two_adds_cannot_mint_the_same_id(self):
        """`add` used to read the book once for the count and again inside the write, so
        the id `t{n+1}` came from a read OUTSIDE the protected section. Found while
        writing the test above — the trigger count did not line up, because there was an
        extra read nobody had noticed (ADR-100)."""
        ledger = TaskLedger(InMemoryStore())
        first, second = await ledger.add("one"), await ledger.add("two")
        self.assertNotEqual(first.id, second.id)
        self.assertEqual([t.id for t in await ledger.all()], ["t1", "t2"])

    async def test_setting_a_status_on_a_missing_task_writes_nothing(self):
        from harness.tasks import UnknownTaskError
        store = InMemoryStore()
        ledger = TaskLedger(store)
        await ledger.add("real")
        before = await store.get(ledger._key)
        with self.assertRaises(UnknownTaskError):
            await ledger.set_status("t99", "doing")
        self.assertEqual(await store.get(ledger._key), before)

    async def test_a_concurrent_write_is_refused(self):
        store = Interfering(trigger=2, intruder=json.dumps(
            [{"id": "t9", "title": "theirs", "status": "todo", "note": "",
              "created_at": 1.0, "updated_at": 1.0}]))
        with self.assertRaises(ConcurrentWriteError):
            await TaskLedger(store).add("mine")

    async def test_the_ordinary_path_still_works(self):
        ledger = TaskLedger(InMemoryStore())
        await ledger.add("write the thing")
        await ledger.add("test the thing")
        self.assertEqual([t.title for t in await ledger.all()],
                         ["write the thing", "test the thing"])

    async def test_a_status_change_still_works(self):
        ledger = TaskLedger(InMemoryStore())
        task = await ledger.add("write the thing")
        await ledger.set_status(task.id, "doing")
        self.assertEqual((await ledger.all())[0].status, "doing")


class TheFindingsLog(unittest.IsolatedAsyncioTestCase):
    """The third ledger, found when it moved into core: in `examples/` it was a bare
    get/put pair, and ADR-100's rule applies to every ledger on a `Store`, not to the two
    that happened to be in core when it was written (ADR-101)."""

    async def test_a_concurrent_write_is_refused(self):
        from harness.findings import DEFAULT_KEY, FindingsLog
        store = Interfering(trigger=2, intruder=json.dumps(
            [{"at": "10:00:00", "text": "theirs"}]))
        with self.assertRaises(ConcurrentWriteError):
            await FindingsLog(store, key=DEFAULT_KEY).add("mine")

    async def test_recording_and_reading_back_still_work(self):
        from harness.findings import FindingsLog
        log = FindingsLog(InMemoryStore())
        await log.add("the linter OOMs without NODE_ENV=test")
        await log.add("that stack trace was a red herring")
        self.assertEqual(len(await log.all()), 2)
        self.assertIn("red herring", await log.summary())


class TheIdentityLedger(unittest.IsolatedAsyncioTestCase):
    async def test_a_concurrent_enrolment_is_refused(self):
        """The one that matters most: two writers on a biometric record, where the lost
        update is somebody's face silently not being enrolled."""
        store = Interfering(trigger=2, intruder=json.dumps(
            [{"name": "Theirs", "vectors": [[1.0, 0.0]]}]))
        with self.assertRaises(ConcurrentWriteError):
            await IdentityLedger(store).enroll("Mine", (0.0, 1.0))

    async def test_a_concurrent_forget_is_refused(self):
        store = Interfering(trigger=2, intruder=json.dumps(
            [{"name": "Nghia", "vectors": [[1.0, 0.0]]},
             {"name": "Thiep", "vectors": [[0.0, 1.0]]}]))
        await InMemoryStore.put(store, IdentityLedger.KEY,
                                json.dumps([{"name": "Nghia",
                                             "vectors": [[1.0, 0.0]]}]))
        with self.assertRaises(ConcurrentWriteError):
            await IdentityLedger(store).forget("Nghia")

    async def test_enrol_match_and_forget_still_work(self):
        ledger = IdentityLedger(InMemoryStore())
        self.assertEqual(await ledger.enroll("Nghia", (1.0, 0.0, 0.0)), 1)
        self.assertEqual(await ledger.enroll("Nghia", (0.9, 0.1, 0.0)), 2,
                         "a second angle of the same person")
        self.assertEqual((await ledger.match((1.0, 0.0, 0.0))).name, "Nghia")
        self.assertTrue(await ledger.forget("Nghia"))
        self.assertEqual(await ledger.names(), ())

    async def test_forgetting_somebody_absent_writes_nothing_and_says_so(self):
        ledger = IdentityLedger(InMemoryStore())
        await ledger.enroll("Nghia", (1.0, 0.0))
        self.assertFalse(await ledger.forget("Nobody"))
        self.assertEqual(await ledger.names(), ("Nghia",))


if __name__ == "__main__":
    unittest.main()
