"""N-7 (design/07-risks-and-open-issues.md §1.5): `Agent.with_()` âm thầm làm mất
`transcript`, `exporters`, `accepts_tainted`, `sensitive` — MỌI lần gọi, không chỉ khi
caller đụng tới một trong bốn trường đó. Phát hiện khi viết T-8.5 (`agent.stream()`, dùng
`with_()` để thêm một exporter riêng) — test transcript của `stream()` fail vì
`with_()` đã âm thầm đặt `transcript=None` trên agent phái sinh.
"""
import unittest

from harness import Agent


class WithGiuLaiMoiTruong(unittest.TestCase):
    def test_transcript_khong_bi_mat(self):
        a = Agent(name="A", job="j", transcript="/tmp/n7-test-transcript.jsonl")
        b = a.with_(name="B")
        self.assertEqual(b.transcript, "/tmp/n7-test-transcript.jsonl")

    def test_exporters_khong_bi_mat(self):
        class Sink:
            def emit(self, e): ...
            def close(self): ...
        sink = Sink()
        a = Agent(name="A", job="j", exporters=[sink])
        b = a.with_(name="B")
        self.assertEqual(b.exporters, (sink,))

    def test_accepts_tainted_khong_bi_mat(self):
        a = Agent(name="A", job="j", accepts_tainted=["send_email"])
        b = a.with_(name="B")
        self.assertEqual(b._grants.accepts_tainted, frozenset({"send_email"}))

    def test_sensitive_khong_bi_mat(self):
        a = Agent(name="A", job="j", sensitive=["read_db"])
        b = a.with_(name="B")
        self.assertEqual(b._grants.sensitive, frozenset({"read_db"}))

    def test_require_approval_evidence_khong_bi_mat(self):
        """S-11: cùng lớp lỗi N-7 sửa — một trường mới thêm vào `Agent` phải được liệt
        kê trong `base` dict của `with_()`, không thì mất y hệt bốn trường N-7 tìm ra."""
        a = Agent(name="A", job="j", require_approval_evidence=True)
        b = a.with_(name="B")
        self.assertTrue(b.require_approval_evidence)

    def test_principal_khong_bi_mat(self):
        a = Agent(name="A", job="j", principal="user-42")
        b = a.with_(name="B")
        self.assertEqual(b.principal, "user-42")

    def test_decisions_khong_bi_mat(self):
        from harness.policy.decision import DecisionLog
        log = DecisionLog()
        a = Agent(name="A", job="j", decisions=log)
        b = a.with_(name="B")
        self.assertIs(b.decisions, log)

    def test_override_tuong_minh_van_hoat_dong(self):
        """`with_()` phải vẫn cho GHI ĐÈ khi caller cố tình muốn — không phải khoá
        cứng bốn trường này lại."""
        a = Agent(name="A", job="j", transcript="/tmp/a.jsonl")
        b = a.with_(name="B", transcript="/tmp/b.jsonl")
        self.assertEqual(b.transcript, "/tmp/b.jsonl")

    def test_khong_truyen_gi_thi_giu_nguyen_tat_ca(self):
        a = Agent(name="A", job="j", transcript="/tmp/n7.jsonl",
                 accepts_tainted=["x"], sensitive=["y"])
        b = a.with_()
        self.assertEqual(b.transcript, a.transcript)
        self.assertEqual(b._grants.accepts_tainted, a._grants.accepts_tainted)
        self.assertEqual(b._grants.sensitive, a._grants.sensitive)


class MutationN7CoTacDung(unittest.TestCase):
    def test_khoi_phuc_base_dict_cu_thi_test_do(self):
        """Mutation: khôi phục `base` dict CŨ (thiếu 4 trường) — xác nhận test thật sự
        phụ thuộc vào bản vá, không phải một cơ chế khác vô tình giữ lại giá trị."""
        import harness.agent as agent_mod

        def old_with_(self, **overrides):
            base = {k: getattr(self, k) for k in
                    ("name", "job", "model", "effort", "returns", "budget", "safety",
                     "approve", "policies", "allowed_hosts", "provider",
                     "max_parallel_tools", "max_asks_per_run", "tenant_id", "session_id")}
            base["tools"] = list(self.toolset)
            base.update(overrides)
            return agent_mod.Agent(**base)

        original = agent_mod.Agent.with_
        agent_mod.Agent.with_ = old_with_
        try:
            a = agent_mod.Agent(name="A", job="j", transcript="/tmp/n7.jsonl")
            b = a.with_(name="B")
            self.assertIsNone(b.transcript,
                             "với mutation này (base dict cũ), transcript PHẢI mất — "
                             "khác hành vi thật, chứng minh test phụ thuộc đúng vào "
                             "bản vá N-7")
        finally:
            agent_mod.Agent.with_ = original


if __name__ == "__main__":
    unittest.main()
