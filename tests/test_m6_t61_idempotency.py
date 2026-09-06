"""M6/T-6.1 (docs/17-research-alignment.md): idempotency key + `execute_once` contract.

Test theo đúng tiêu chí T-6.1 tự đặt ra: "Retry cùng key không nhân đôi; hai key khác
nhau thì chạy hai lần", cộng phần "Failure: store chết → fail closed với write/danger,
fail open với read" — cả hai điều kiện đều test được KHÔNG CẦN Service API (M9) tồn tại,
đúng lý do `execute_once` được xây như một hàm độc lập trước, chưa gắn vào
`Agent`/`Dispatcher` (xem docstring `idempotency.py`).
"""
import unittest

from harness.idempotency import execute_once, idempotency_key
from harness.memory.inmemory import InMemoryStore


class Boom(RuntimeError):
    pass


class BrokenStore:
    """`Store` mà mọi `get`/`put` đều raise — mô phỏng store chết."""

    def __init__(self, *, break_get: bool = True, break_put: bool = True) -> None:
        self._break_get, self._break_put = break_get, break_put

    async def get(self, key: str):
        if self._break_get:
            raise Boom("store chết (get)")
        return None

    async def put(self, key: str, value: str, *, ttl_s=None) -> None:
        if self._break_put:
            raise Boom("store chết (put)")

    async def delete(self, key: str) -> None: ...
    async def search(self, query: str, *, limit=5): return []
    async def close(self) -> None: ...


class ExecuteOnceContract(unittest.TestCase):
    def test_idempotency_key_shape(self):
        self.assertEqual(idempotency_key("r_abc", "c1"), "r_abc:c1")

    def test_cung_key_khong_chay_lai_ham(self):
        async def scenario():
            store = InMemoryStore()
            calls = []

            async def fn():
                calls.append(1)
                return {"result": "done"}

            r1, replayed1 = await execute_once(store, "k1", fn)
            r2, replayed2 = await execute_once(store, "k1", fn)
            self.assertEqual(len(calls), 1, "cùng key gọi hai lần nhưng fn() chỉ chạy một")
            self.assertFalse(replayed1)
            self.assertTrue(replayed2)
            self.assertEqual(r1, r2)

        import asyncio
        asyncio.run(scenario())

    def test_key_khac_nhau_chay_hai_lan(self):
        async def scenario():
            store = InMemoryStore()
            calls = []

            async def fn():
                calls.append(1)
                return {"n": len(calls)}

            r1, _ = await execute_once(store, "k1", fn)
            r2, _ = await execute_once(store, "k2", fn)
            self.assertEqual(len(calls), 2, "hai key khác nhau phải chạy fn() hai lần")
            self.assertNotEqual(r1, r2)

        import asyncio
        asyncio.run(scenario())

    def test_store_chet_fail_closed_fn_khong_bao_gio_chay(self):
        """write/danger: `fail_open=False` (mặc định) — store chết ở `get` thì `fn`
        KHÔNG BAO GIỜ được gọi, lỗi truyền thẳng ra ngoài."""
        async def scenario():
            store = BrokenStore(break_get=True)
            calls = []

            async def fn():
                calls.append(1)
                return "ok"

            with self.assertRaises(Boom):
                await execute_once(store, "k1", fn)
            self.assertEqual(calls, [], "fail closed: fn() không được chạy khi get() chết")

        import asyncio
        asyncio.run(scenario())

    def test_store_chet_fail_open_fn_van_chay(self):
        """read/external: `fail_open=True` — store chết vẫn cho `fn` chạy, mất dedup
        nhưng không chặn một lời gọi an toàn, re-run được."""
        async def scenario():
            store = BrokenStore(break_get=True, break_put=True)
            calls = []

            async def fn():
                calls.append(1)
                return "ok"

            result, replayed = await execute_once(store, "k1", fn, fail_open=True)
            self.assertEqual(result, "ok")
            self.assertFalse(replayed)
            self.assertEqual(len(calls), 1, "fail open: fn() vẫn chạy dù store chết")

        import asyncio
        asyncio.run(scenario())

    def test_put_chet_fail_closed_van_raise_du_fn_da_chay(self):
        """Bất đối xứng có chủ đích: `put` chết sau khi `fn` ĐÃ chạy — fail closed vẫn
        raise (không thể undo side effect, nhưng làm lỗi RÕ thay vì im lặng mất bản ghi —
        IDL-30 fail visible)."""
        async def scenario():
            store = BrokenStore(break_get=False, break_put=True)
            calls = []

            async def fn():
                calls.append(1)
                return "ok"

            with self.assertRaises(Boom):
                await execute_once(store, "k1", fn)
            self.assertEqual(len(calls), 1,
                             "fn() đã chạy (side effect đã xảy ra) — raise không undo được "
                             "nó, chỉ làm rõ ràng rằng bản ghi dedup không được lưu")

        import asyncio
        asyncio.run(scenario())

    def test_ket_qua_round_trip_json(self):
        """`result` phải qua được JSON round-trip — cùng ràng buộc string-only của
        `Store` (memory/base.py)."""
        async def scenario():
            store = InMemoryStore()

            async def fn():
                return {"tool": "wipe", "n": 3, "ok": True}

            r1, _ = await execute_once(store, "k1", fn)
            r2, _ = await execute_once(store, "k1", fn)   # replay path
            self.assertEqual(r1, {"tool": "wipe", "n": 3, "ok": True})
            self.assertEqual(r1, r2)

        import asyncio
        asyncio.run(scenario())


class MutationExecuteOnceCoTacDung(unittest.TestCase):
    def test_bo_check_cache_thi_test_dedup_do(self):
        """Mutation: một `execute_once` giả LUÔN chạy `fn()` (bỏ nhánh kiểm cache) —
        xác nhận test dedup ở trên thật sự phụ thuộc vào việc kiểm `store.get()` trước."""
        async def always_runs(store, key, fn, *, fail_open=False):
            result = await fn()
            return result, False

        async def scenario():
            store = InMemoryStore()
            calls = []

            async def fn():
                calls.append(1)
                return {"n": len(calls)}

            await always_runs(store, "k1", fn)
            await always_runs(store, "k1", fn)
            self.assertEqual(len(calls), 2,
                             "với mutation này, fn() chạy 2 lần — khác kết quả của "
                             "execute_once() thật, chứng minh test thật phụ thuộc vào "
                             "nhánh kiểm cache")

        import asyncio
        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
