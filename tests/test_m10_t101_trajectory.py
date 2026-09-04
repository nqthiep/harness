"""M10/T-10.1 (docs/17-research-alignment.md): trajectory contract — `must_call`,
`must_not_call`, `requires_approval`, `max_model_calls`, `max_tokens`, `max_cost_usd`,
`output_schema`, `no_duplicate_side_effects`.
"""
import asyncio
import unittest

from harness import Agent, tool
from harness.eval.trajectory import Trajectory, check_trajectory
from harness.models.fake import FakeModel


@tool(effect="read")
def look(x: int) -> str:
    """Nhìn."""
    return "ok"


@tool(effect="danger")
def wipe(x: int) -> str:
    """Xoá."""
    return "gone"


@tool(effect="write")
def push(x: int) -> str:
    """Ghi — không cần duyệt như `wipe`, nên chạy được hai lần trong cùng một test mà
    không cần `approve=`."""
    return "pushed"


async def _run(script, tools=(look, wipe, push), **kw):
    events = []

    class _Collect:
        def emit(self, e): events.append(e)
        def close(self): ...

    agent = Agent(name="A", job="j", provider=FakeModel(script), tools=list(tools),
                  budget="$5", exporters=[_Collect()], **kw)
    result = await agent.atry_run("hi")
    return result, events


def _run_sync(*a, **kw):
    return asyncio.run(_run(*a, **kw))


class MustCall(unittest.TestCase):
    def test_pass_khi_tool_da_chay(self):
        r, ev = _run_sync([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("d")])
        tr = check_trajectory(Trajectory(must_call=frozenset({"look"})), r, ev)
        self.assertTrue(tr.ok, tr.violations)

    def test_fail_khi_tool_khong_chay(self):
        r, ev = _run_sync([FakeModel.text("d")])
        tr = check_trajectory(Trajectory(must_call=frozenset({"look"})), r, ev)
        self.assertFalse(tr.ok)
        self.assertIn("must_call", tr.violations[0])


class MustNotCall(unittest.TestCase):
    def test_pass_khi_khong_chay(self):
        r, ev = _run_sync([FakeModel.text("d")])
        tr = check_trajectory(Trajectory(must_not_call=frozenset({"wipe"})), r, ev)
        self.assertTrue(tr.ok)

    def test_fail_khi_co_chay(self):
        r, ev = _run_sync([FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("d")],
                          approve=lambda c, ctx: True)
        tr = check_trajectory(Trajectory(must_not_call=frozenset({"wipe"})), r, ev)
        self.assertFalse(tr.ok)
        self.assertIn("wipe", tr.violations[0])


class RequiresApproval(unittest.TestCase):
    def test_pass_khi_di_qua_ask(self):
        r, ev = _run_sync([FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("d")],
                          approve=lambda c, ctx: True)
        tr = check_trajectory(Trajectory(requires_approval=frozenset({"wipe"})), r, ev)
        self.assertTrue(tr.ok, tr.violations)

    def test_fail_khi_tool_khong_can_hoi(self):
        """`look` (read) không bao giờ ASK — đòi `requires_approval` cho nó phải fail."""
        r, ev = _run_sync([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("d")])
        tr = check_trajectory(Trajectory(requires_approval=frozenset({"look"})), r, ev)
        self.assertFalse(tr.ok)

    def test_mutation_chi_kiem_ton_tai_policy_decided_khong_kiem_verdict_approval(self):
        """Mutation: coi MỌI `policy.decided` (kể cả auto-ALLOW không hề hỏi) là 'đã
        duyệt' — sẽ sai lầm cho `requires_approval` PASS trên một tool `read` chưa từng
        đi qua ASK, đúng lỗi mà check field `policy=="approval"` phải ngăn."""
        r, ev = _run_sync([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("d")])
        buggy_seen = {e.data.get("tool") for e in ev if e.kind.value == "policy.decided"}
        self.assertIn("look", buggy_seen,
                      "mutation (không lọc policy=='approval') phải coi 'look' là đã "
                      "qua duyệt dù nó auto-allow — nếu nó cũng không có, test này "
                      "không còn phân biệt được bản đúng và bản có lỗi")
        real_seen = {e.data.get("tool") for e in ev
                    if e.kind.value == "policy.decided" and e.data.get("policy") == "approval"}
        self.assertNotIn("look", real_seen)


class GioiHanSo(unittest.TestCase):
    def test_max_model_calls(self):
        r, ev = _run_sync([FakeModel.tool_call("look", {"x": 1}), FakeModel.text("d")])
        self.assertTrue(check_trajectory(Trajectory(max_model_calls=2), r, ev).ok)
        self.assertFalse(check_trajectory(Trajectory(max_model_calls=1), r, ev).ok)

    def test_max_tokens(self):
        r, ev = _run_sync([FakeModel.text("d")])
        self.assertTrue(check_trajectory(Trajectory(max_tokens=10_000), r, ev).ok)
        self.assertFalse(check_trajectory(Trajectory(max_tokens=1), r, ev).ok)

    def test_max_cost_usd(self):
        r, ev = _run_sync([FakeModel.text("d")])
        self.assertTrue(check_trajectory(Trajectory(max_cost_usd=100.0), r, ev).ok)


class OutputSchema(unittest.TestCase):
    def test_pass_khi_text_khop_schema(self):
        r, ev = _run_sync([FakeModel.text("hello")])
        schema = {"type": "string"}
        self.assertTrue(check_trajectory(Trajectory(output_schema=schema), r, ev).ok)

    def test_fail_khi_khong_khop(self):
        r, ev = _run_sync([FakeModel.text("hello")])
        schema = {"type": "integer"}
        tr = check_trajectory(Trajectory(output_schema=schema), r, ev)
        self.assertFalse(tr.ok)
        self.assertIn("output_schema", tr.violations[0])


EFFECT_OF = {"look": "read", "wipe": "danger", "push": "write"}


class NoDuplicateSideEffects(unittest.TestCase):
    def test_pass_mot_lan_goi(self):
        r, ev = _run_sync([FakeModel.tool_call("push", {"x": 1}), FakeModel.text("d")])
        self.assertTrue(check_trajectory(Trajectory(no_duplicate_side_effects=True), r, ev,
                                         effect_of=EFFECT_OF).ok)

    def test_fail_khi_chay_hai_lan_cung_arg_khac_step(self):
        r, ev = _run_sync([
            FakeModel.tool_call("push", {"x": 1}, call_id="c1"),
            FakeModel.tool_call("push", {"x": 1}, call_id="c2"),
            FakeModel.text("d"),
        ])
        tr = check_trajectory(Trajectory(no_duplicate_side_effects=True), r, ev,
                              effect_of=EFFECT_OF)
        self.assertFalse(tr.ok)
        self.assertIn("no_duplicate_side_effects", tr.violations[0])

    def test_bug_that_doc_bi_goi_trung_khong_bi_gan_co_vi_pham(self):
        """Bug thật, tìm thấy khi tự review lượt vá này: `check_trajectory` từng gắn cờ
        vi phạm cho BẤT KỲ tool nào gọi trùng tham số — kể cả `read`, vốn không có side
        effect nào để "chạy trùng" cả. `look` là `effect="read"`; gọi hai lần cùng
        tham số ở đây phải hợp lệ, đúng cái tên "no_duplicate_SIDE_EFFECTS" hứa."""
        r, ev = _run_sync([
            FakeModel.tool_call("look", {"x": 1}, call_id="c1"),
            FakeModel.tool_call("look", {"x": 1}, call_id="c2"),
            FakeModel.text("d"),
        ])
        tr = check_trajectory(Trajectory(no_duplicate_side_effects=True), r, ev,
                              effect_of=EFFECT_OF)
        self.assertTrue(tr.ok, tr.violations)

    def test_thieu_effect_of_thi_bao_loi_ro_rang_khong_doan_dai(self):
        r, ev = _run_sync([FakeModel.tool_call("wipe", {"x": 1}), FakeModel.text("d")])
        with self.assertRaises(ValueError):
            check_trajectory(Trajectory(no_duplicate_side_effects=True), r, ev)

    def test_mutation_khong_noi_call_id_ve_arguments_bo_lo_trung_lap(self):
        """Mutation: dùng `arguments` thẳng từ `tool.started` (event đó KHÔNG mang
        field này — chỉ `tool.requested` mang) thay vì nối qua `call_id` — mọi lời gọi
        đều đọc `{}`  làm args, nên hai lời gọi CÙNG tool nhưng KHÁC args thật cũng bị
        gộp nhầm thành trùng lặp. Dùng `push` (write), không phải `look` (read) — sau
        bản vá effect_of, một tool `read` bị lọc ra TRƯỚC khi chạm logic mutation này,
        nên test sẽ pass giả tạo bất kể mutation có mặt hay không nếu dùng `look`.
        """
        r, ev = asyncio.run(_run([
            FakeModel.tool_call("push", {"x": 1}, call_id="c1"),
            FakeModel.tool_call("push", {"x": 2}, call_id="c2"),
            FakeModel.text("d"),
        ]))
        started = [e for e in ev if e.kind.value == "tool.started"]
        buggy_args = [e.data.get("arguments", {}) for e in started]  # luôn {} — mutation
        self.assertTrue(all(a == {} for a in buggy_args),
                        "mutation (đọc arguments thẳng từ tool.started) phải luôn ra "
                        "{} — nếu nó khác, test này không còn phân biệt được bản đúng "
                        "và bản có lỗi")
        # Bản đúng (check_trajectory) phân biệt được hai args khác nhau -> KHÔNG coi là trùng.
        tr = check_trajectory(Trajectory(no_duplicate_side_effects=True), r, ev,
                              effect_of=EFFECT_OF)
        self.assertTrue(tr.ok, tr.violations)


if __name__ == "__main__":
    unittest.main()
