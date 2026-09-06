"""`TaskLedger` — sổ công việc bền cho một phiên chạy dài.

**Vì sao đây KHÔNG mâu thuẫn ADR-023 ("no planner, reflection, or self-critique loop").**
ADR-023 bác bỏ *một lượt gọi model thêm* để lập kế hoạch hay tự phê bình — "a second model
call for an unmeasured quality gain". Module này không gọi model lần nào: nó là TRẠNG THÁI
BỀN, cùng loài với ba cuốn sổ harness đã coi trọng — `Ledger` (tiền/bước/thời gian),
`DecisionLog` (phê duyệt, append-only), `Label` (độ tin cậy). Việc LẬP kế hoạch vẫn là việc
của model, viết trong `job=`, đúng như ADR-023 đã lập luận; việc GIỮ kế hoạch đó qua một
phiên nhiều giờ, qua cả restart, là việc của harness.

**Vì sao dựa trên `Store` chứ không phải state của LangGraph.** `Store` đã là một seam
(ADR-002) với hai bản cài thật (`InMemoryStore`, `SqliteStore`), nên: (a) bền qua restart
tiến trình — mạnh hơn checkpoint của graph, vốn chỉ sống cùng checkpointer; (b) chạy y hệt
trên CẢ HAI backend, không phải thêm một khoá vào `AgentState` rồi chỉ backend graph mới
có; (c) không thêm tham số nào vào `Agent(...)`/`build_agent(...)` — người viết agent chỉ
thấy vài tool mới, đúng ràng buộc "không đổi coding interface".

**"read-modify-write ở đây an toàn" — đúng một nửa, và hai nhánh review tìm ra hai nửa
khác nhau của phần còn lại (G-5 `design/review-architect.md`; ADR-100; hợp nhất ở
ADR-119).**

Lập luận gốc: mọi tool sửa sổ đều `effect="write"`, `EFFECT_PROFILES[Effect.WRITE]
.parallel_safe` là `False`, nên không có hai lời gọi nào CÙNG MỘT BATCH cùng đọc-sửa-ghi
đè lên nhau. Đúng, và hẹp hơn nó tự nhận theo hai hướng độc lập:

1. **Người ghi thứ hai trong cùng tiến trình.** `parallel_safe=False` chỉ tuần tự hoá các
   lời gọi tool TRONG MỘT BATCH của MỘT run — nó không nói gì về hai run, hai
   `TaskLedger`, hay `harness.contrib.Driver` với pump nền của nó. Đo được: hai
   `TaskLedger` trên cùng một `SqliteStore`, mười `add()` đồng thời (năm mỗi bên) — còn
   lại ĐÚNG MỘT task, chín cái biến mất, không một lỗi nào được báo. Nguyên nhân:
   `SqliteStore.get`/`put` chạy qua `asyncio.to_thread`, nên `await` giữa phần đọc và
   phần ghi là một điểm nhường-luồng THẬT. Bịt bằng khoá per-`(store, key)` (`_lock_for`
   dưới đây, cùng khuôn `idempotency.py::_lock_for`).
2. **Người ghi thứ hai ở tiến trình khác.** Khoá trên không với tới được — chính
   `idempotency.py` cũng đã tự thừa nhận đúng giới hạn đó về khoá của nó. Bịt bằng
   `read_modify_write`: đọc, sửa, ĐỌC LẠI và so sánh, rồi mới ghi — mất-cập-nhật thành
   một lỗi được báo thay vì một hàng biến mất.

Ngăn ở chỗ ngăn được, phát hiện ở chỗ không ngăn được, và không chỗ nào còn dựa vào một
lập luận. Nếu ngày nào đó `write` thành parallel-safe thì đoạn này vẫn đúng — đó là điểm
của việc thay lập luận bằng cơ chế.
"""
from __future__ import annotations

import asyncio
import json
import time
import weakref
from typing import Any, Final

from ._value import value
from .errors import HarnessError
from .memory.base import Store, read_modify_write
from .tools import tool

#: Từ vựng đóng, không mở rộng được từ phía model — cùng lý do `Effect` đóng ở bốn giá
#: trị: một trạng thái tự do sẽ khiến "xong" và "hoàn thành" và "done" thành ba thứ khác
#: nhau trong cùng một sổ, và không cái nào đếm được.
STATUSES: Final = ("todo", "doing", "done", "blocked")

DEFAULT_KEY: Final = "harness:tasks"


class UnknownTaskError(HarnessError):
    """Tham chiếu tới một task không có trong sổ — thường là model tự bịa id."""


class UnknownStatusError(HarnessError):
    """Trạng thái ngoài `STATUSES`."""


#: G-5 — khoá theo `(id(store), key)`, không phải chỉ `key`: hai `Store` KHÁC NHAU dùng
#: trùng chuỗi `key` không có lý do gì phải chờ nhau. `WeakValueDictionary` cùng lý do
#: `idempotency.py::_locks` đã dùng: một khoá không cần sống lâu hơn mọi caller đang giữ
#: nó — module này không có hook vòng đời để tự giải phóng một khoá tường minh.
_locks: "weakref.WeakValueDictionary[tuple[int, str], asyncio.Lock]" = \
    weakref.WeakValueDictionary()



def _lock_for(store: Store, key: str) -> asyncio.Lock:
    lock_key = (id(store), key)
    lock = _locks.get(lock_key)
    if lock is None:
        lock = asyncio.Lock()
        _locks[lock_key] = lock
    return lock



@value
class Task:
    id: str
    title: str
    status: str = "todo"
    note: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    def line(self) -> str:
        """Một dòng cho model đọc — ngắn, vì sổ này sẽ được in lại nhiều lần."""
        mark = {"todo": "[ ]", "doing": "[~]", "done": "[x]", "blocked": "[!]"}[self.status]
        note = f"  — {self.note}" if self.note else ""
        return f"{mark} {self.id}: {self.title}{note}"


def _to_dict(t: Task) -> dict[str, Any]:
    return {"id": t.id, "title": t.title, "status": t.status, "note": t.note,
            "created_at": t.created_at, "updated_at": t.updated_at}


def _from_dict(d: dict[str, Any]) -> Task:
    return Task(str(d["id"]), str(d["title"]), str(d.get("status", "todo")),
                str(d.get("note", "")), float(d.get("created_at", 0.0)),
                float(d.get("updated_at", 0.0)))


class TaskLedger:
    """Danh sách công việc của MỘT phiên, lưu trong một `Store`.

    Cả sổ nằm dưới đúng một khoá (`key=`) dưới dạng một mảng JSON — không có bảng chỉ mục
    riêng để mà lệch nhau. Một phiên coding có hàng chục task, không phải hàng triệu; đổi
    lấy sự đơn giản đó là đúng giá ở quy mô này, và nói ra để lần sau ai cần quy mô khác
    thì biết chỗ phải đổi.
    """

    def __init__(self, store: Store, *, key: str = DEFAULT_KEY) -> None:
        self._store = store
        self._key = key

    async def all(self) -> tuple[Task, ...]:
        raw = await self._store.get(self._key)
        if not raw:
            return ()
        try:
            rows = json.loads(raw)
        except json.JSONDecodeError:
            # Sổ hỏng thì nói thẳng, không âm thầm bắt đầu lại từ sổ trắng: mất dấu công
            # việc đã làm còn tệ hơn một lỗi đọc được (IDL-30, fail visible).
            raise HarnessError(
                f"task ledger tại khoá {self._key!r} không đọc được (JSON hỏng) — "
                f"không tự khởi tạo lại sổ trắng, vì làm vậy sẽ xoá dấu vết công việc "
                f"đã hoàn thành."
            ) from None
        return tuple(_from_dict(r) for r in rows)

    async def _update(self, change) -> None:
        """Read, transform, write — through `read_modify_write`, so two writers racing on
        this key is an error instead of a silently discarded update.

        The paragraph at the top of this module explains why the race could not happen:
        every mutating tool is `effect="write"`, which is not `parallel_safe`, so they
        serialise within a step. That is still true and it stops being true the moment
        anything ELSE writes — `harness.contrib.Driver` runs a background pump, which is
        exactly that (ADR-100). An argument for why a race cannot happen is worth less
        than a check that says so when it does.

        Locked AND checked, because the two cover different halves and each admits the
        other's gap. `_lock_for` (G-5) serialises the whole read-modify-write for
        everything in THIS process — including the `Driver` pump — which is the half the
        check can only report after the fact; `read_modify_write` catches the half a
        process-local lock structurally cannot, two processes on one `Store`. Merged from
        two branches that each fixed one half: preventing beats detecting where you can
        prevent, and detecting beats trusting an argument where you cannot.
        """
        async with _lock_for(self._store, self._key):
            await self._locked_update(change)

    async def _locked_update(self, change) -> None:
        def mutate(raw: str | None) -> str | None:
            current = tuple(_from_dict(r) for r in json.loads(raw)) if raw else ()
            after = change(current)
            return (None if after is None
                    else json.dumps([_to_dict(t) for t in after], ensure_ascii=False))

        await read_modify_write(self._store, self._key, mutate,
                                what="this agent's task list")

    async def add(self, title: str) -> Task:
        now = time.time()
        made: list[Task] = []

        def change(current):
            # The id derives from the rows read INSIDE the protected section, not from a
            # separate earlier read. Two concurrent `add`s used to compute `t{n+1}` from
            # their own stale counts and produce the same id (ADR-100).
            task = Task(f"t{len(current) + 1}", title, "todo", "", now, now)
            made.append(task)
            return [*current, task]

        await self._update(change)
        return made[0]

    async def set_status(self, task_id: str, status: str, note: str = "") -> Task:
        if status not in STATUSES:
            raise UnknownStatusError(
                f"{status!r} không phải trạng thái hợp lệ. Chọn một trong: "
                f"{', '.join(STATUSES)}")
        done: list[Task] = []
        known: list[str] = []

        def change(current):
            known[:] = [t.id for t in current]
            rows = list(current)
            for i, t in enumerate(rows):
                if t.id == task_id:
                    rows[i] = Task(t.id, t.title, status, note or t.note, t.created_at,
                                   time.time())
                    done.append(rows[i])
                    return rows
            return None                      # not found: write nothing

        await self._update(change)
        if done:
            return done[0]
        raise UnknownTaskError(
            f"không có task {task_id!r} trong sổ. Đang có: "
            f"{', '.join(known) or '(sổ trống)'}")

    async def summary(self) -> str:
        """Toàn bộ sổ, dạng model đọc được. Đây là thứ khiến việc nén context an toàn:
        tiến độ nằm ở đây, không nằm trong lịch sử hội thoại, nên xoá kết quả tool cũ
        không làm agent quên mất mình đang làm gì (`context/window.py`)."""
        tasks = await self.all()
        if not tasks:
            return "(sổ công việc trống)"
        done = sum(1 for t in tasks if t.status == "done")
        head = f"{done}/{len(tasks)} xong"
        return head + "\n" + "\n".join(t.line() for t in tasks)

    def tools(self) -> list:
        """Tool cho model, effect đã phân loại sẵn — cùng khuôn `VikingStore.tools()`.

        Đọc sổ là `read`; mọi thao tác sửa sổ là `write` (hoàn tác được: sửa lại trạng
        thái là xong, không có gì mất vĩnh viễn). Không cái nào là `danger` — ghi một dòng
        vào sổ công việc của chính mình không phải hành động không hoàn tác được.
        """
        ledger = self

        @tool(effect="read")
        async def list_tasks() -> str:
            """Xem toàn bộ danh sách công việc và trạng thái từng việc."""
            return await ledger.summary()

        @tool(effect="write")
        async def add_task(title: str) -> str:
            """Thêm một việc cần làm vào danh sách."""
            t = await ledger.add(title)
            return f"đã thêm {t.id}: {t.title}"

        @tool(effect="write")
        async def start_task(task_id: str) -> str:
            """Đánh dấu một việc là đang làm."""
            t = await ledger.set_status(task_id, "doing")
            return f"{t.id} đang làm"

        @tool(effect="write")
        async def finish_task(task_id: str, note: str = "") -> str:
            """Đánh dấu một việc đã xong, kèm ghi chú ngắn về kết quả."""
            t = await ledger.set_status(task_id, "done", note)
            return f"{t.id} xong"

        @tool(effect="write")
        async def block_task(task_id: str, note: str) -> str:
            """Đánh dấu một việc bị kẹt, kèm lý do."""
            t = await ledger.set_status(task_id, "blocked", note)
            return f"{t.id} bị kẹt: {t.note}"

        return [list_tasks, add_task, start_task, finish_task, block_task]
