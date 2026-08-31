"""M9/T-9.3 (docs/17-research-alignment.md): "một event model, nhiều transport — không
transport nào có semantics riêng."

Trước bản vá này, `observe/transcript.py::TranscriptWriter` và `harness/server`'s
`_event_json` mỗi bên tự định nghĩa lại hình dạng dict — và `TranscriptWriter` đã ÂM
THẦM LỖI THỜI: viết TRƯỚC envelope v1 (T-8.1), không bao giờ được cập nhật để mang
`schema_version`/`trace_id`/`tenant_id`/`session_id`, nên transcript ghi trên đĩa và
event live qua SSE của CÙNG một run bất đồng về `Event` có những trường gì. Sửa: một
hàm `observe.events.to_dict()` duy nhất cả hai xây trên đó.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, "src")

from harness import Agent, tool
from harness.models.fake import FakeModel
from harness.observe.events import Event, EventKind, EVENT_SCHEMA_VERSION, to_dict
from harness.observe.transcript import read


@tool(effect="read")
def look(x: int) -> str:
    """Nhìn."""
    return "ok"


class ToDictLaCanonical(unittest.TestCase):
    def test_mang_du_field_bao_gom_envelope_v1(self):
        ev = Event(seq=1, ts=1.5, run_id="r1", kind=EventKind.RUN_STARTED, step=0,
                  data={"a": 1}, trace_id="t1", tenant_id="acme", session_id="s1")
        d = to_dict(ev)
        self.assertEqual(d["seq"], 1)
        self.assertEqual(d["run_id"], "r1")
        self.assertEqual(d["kind"], "run.started")
        self.assertEqual(d["data"], {"a": 1})
        self.assertEqual(d["schema_version"], EVENT_SCHEMA_VERSION)
        self.assertEqual(d["trace_id"], "t1")
        self.assertEqual(d["tenant_id"], "acme")
        self.assertEqual(d["session_id"], "s1")

    def test_khong_lam_thay_doi_data_goc(self):
        """`to_dict()` trả một BẢN SAO của `data` — sửa dict trả về không được đổi
        `Event.data` gốc (một `Event` là `@value`, bất biến)."""
        ev = Event(seq=1, ts=0.0, run_id="r1", kind=EventKind.RUN_STARTED, step=0,
                  data={"a": 1})
        d = to_dict(ev)
        d["data"]["a"] = 999
        self.assertEqual(dict(ev.data), {"a": 1})


class TranscriptMangDuEnvelopeV1(unittest.TestCase):
    """`TranscriptWriter` từng lỗi thời — sửa xong phải mang đủ bốn trường envelope."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "t.jsonl")

    def test_dong_transcript_mang_schema_version_va_trace_id(self):
        m = FakeModel([FakeModel.text("done")])
        a = Agent(name="T", job="j", provider=m, budget="$5", transcript=self.path,
                  tenant_id="acme", session_id="s1")
        a.run("go")
        rows = list(read(self.path))
        self.assertTrue(rows)
        for row in rows:
            self.assertEqual(row["schema_version"], EVENT_SCHEMA_VERSION)
            self.assertEqual(row["tenant_id"], "acme")
            self.assertEqual(row["session_id"], "s1")
            self.assertIn("trace_id", row)

    def test_digest_van_hoat_dong_nhu_cu(self):
        """Hồi quy: chuyển sang xây trên `to_dict()` không được làm mất chính sách riêng
        của transcript — digest `arguments` của `TOOL_REQUESTED`, không ghi nguyên văn."""
        m = FakeModel([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("done")])
        a = Agent(name="T", job="j", tools=[look], provider=m, budget="$5",
                  transcript=self.path)
        a.run("go")
        rows = [r for r in read(self.path) if r["kind"] == "tool.requested"]
        self.assertEqual(len(rows), 1)
        self.assertNotIn("arguments", rows[0]["data"])
        self.assertIn("arguments_digest", rows[0]["data"])

    def test_mutation_bo_qua_to_dict_lam_mat_envelope(self):
        """Mutation: khôi phục dict cũ (trước T-9.3) — thiếu bốn trường envelope v1,
        đúng khoảng lệch thật giữa transcript và SSE mà bản vá này đóng."""
        ev = Event(seq=0, ts=0.0, run_id="r1", kind=EventKind.RUN_STARTED, step=0,
                  data={}, tenant_id="acme")
        old_row = {"seq": ev.seq, "ts": round(ev.ts, 6), "run_id": ev.run_id,
                  "kind": ev.kind.value, "step": ev.step, "data": dict(ev.data)}
        self.assertNotIn("tenant_id", old_row,
                        "mutation (dict cũ) phải THIẾU tenant_id — nếu nó cũng có, test "
                        "này không còn phân biệt được bản đúng và bản có lỗi")
        self.assertIn("tenant_id", to_dict(ev))


class CliJsonTransport(unittest.TestCase):
    """`harness run <file> <msg> --json` — transport thứ ba T-9.3 đặt tên, cùng
    `to_dict()` với hai transport kia."""

    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.path = os.path.join(self.dir, "agent.py")
        with open(self.path, "w") as f:
            f.write(
                "from harness import Agent\n"
                "from harness.models.fake import FakeModel\n"
                "agent = Agent(name='CliT', job='j', budget='$5',\n"
                "             provider=FakeModel([FakeModel.text('xong')]))\n"
            )

    def test_in_moi_dong_la_mot_event_json_dung_canonical_shape(self):
        from harness.cli import cmd_run
        lines: list[str] = []
        rc = cmd_run(self.path, "hi", out=lines.append, json_events=True)
        self.assertEqual(rc, 0)
        self.assertTrue(lines)
        rows = [json.loads(line) for line in lines]
        for row in rows:
            self.assertIn("schema_version", row)
            self.assertIn("trace_id", row)
            self.assertIn("kind", row)
        self.assertEqual(rows[0]["kind"], "run.started")
        self.assertEqual(rows[-1]["kind"], "run.finished")

    def test_that_bai_tra_ve_exit_code_1(self):
        """`stop_reason` khác `completed` (ở đây: hết `steps` sau lượt gọi tool đầu
        tiên, cần thêm một bước trả lời bằng text mà budget không cho) — CLI phải thoát
        với exit code 1, không phải 0."""
        path = os.path.join(self.dir, "agent_step_limit.py")
        with open(path, "w") as f:
            f.write(
                "from harness import Agent, tool\n"
                "from harness.models.fake import FakeModel\n"
                "@tool(effect='read')\n"
                "def look(x: int) -> str:\n"
                "    '''Nhin.'''\n"
                "    return 'ok'\n"
                "agent = Agent(name='CliT2', job='j', budget='$5, 1 steps', tools=[look],\n"
                "             provider=FakeModel([FakeModel.tool_call('look', {'x': 1}),\n"
                "                                 FakeModel.text('xong')]))\n"
            )
        from harness.cli import cmd_run
        lines: list[str] = []
        rc = cmd_run(path, "hi", out=lines.append, json_events=True)
        self.assertEqual(rc, 1)

    def test_mutation_bo_json_dumps_dung_str_tho_khong_phai_json(self):
        """Mutation: `out(str(event))` thay vì `out(json.dumps(to_dict(event)))` — dòng
        in ra không còn parse được bằng `json.loads`, phá vỡ đúng hợp đồng "CLI/JSON
        transport" (một dòng, một `Event` JSON hợp lệ)."""
        ev = Event(seq=0, ts=0.0, run_id="r1", kind=EventKind.RUN_STARTED, step=0, data={})
        buggy_line = str(ev)
        with self.assertRaises(json.JSONDecodeError):
            json.loads(buggy_line)
        json.loads(json.dumps(to_dict(ev), default=str))    # bản đúng vẫn parse được


if __name__ == "__main__":
    unittest.main()
