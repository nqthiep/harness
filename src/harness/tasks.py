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

**Vì sao read-modify-write ở đây an toàn.** Mọi tool sửa sổ đều `effect="write"`, và
`EFFECT_PROFILES[Effect.WRITE].parallel_safe` là `False` — harness đã tuần tự hoá chúng
trong một bước, nên không có hai lời gọi nào cùng đọc-sửa-ghi đè lên nhau. Đây là lý do
module này không cần khoá riêng; nếu ngày nào đó `write` thành parallel-safe thì chỗ này
phải xem lại (ghi ra đây để lần đó không im lặng).
"""
from __future__ import annotations

import json
import time
from typing import Any, Final, Sequence

from ._value import value
from .errors import HarnessError
from .memory.base import Store
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

    async def _save(self, tasks: Sequence[Task]) -> None:
        await self._store.put(self._key, json.dumps([_to_dict(t) for t in tasks],
                                                    ensure_ascii=False))

    async def add(self, title: str) -> Task:
        tasks = await self.all()
        now = time.time()
        t = Task(f"t{len(tasks) + 1}", title, "todo", "", now, now)
        await self._save([*tasks, t])
        return t

    async def set_status(self, task_id: str, status: str, note: str = "") -> Task:
        if status not in STATUSES:
            raise UnknownStatusError(
                f"{status!r} không phải trạng thái hợp lệ. Chọn một trong: "
                f"{', '.join(STATUSES)}")
        tasks = list(await self.all())
        for i, t in enumerate(tasks):
            if t.id == task_id:
                updated = Task(t.id, t.title, status, note or t.note, t.created_at,
                               time.time())
                tasks[i] = updated
                await self._save(tasks)
                return updated
        raise UnknownTaskError(
            f"không có task {task_id!r} trong sổ. Đang có: "
            f"{', '.join(t.id for t in tasks) or '(sổ trống)'}")

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
