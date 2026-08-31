"""S-11's phần còn lại — design/07-risks-and-open-issues.md. `AuthEvidence`: một callback
`approve=` tự xác minh bằng chứng từ kênh của NÓ (Slack, OAuth, …) rồi mới tự báo
`Actor(..., verified=True)` — chặn được lời tự khai gian bằng cách cho callback một công
cụ crypto ĐÚNG, không phải bằng cách harness tự đi verify hộ (harness không biết gì về
kênh của ai). Xem docstring `harness.policy.auth_evidence` cho lý do phạm vi dừng ở đây.
"""
import sys
import unittest

sys.path.insert(0, "src")

from harness.policy.auth_evidence import AuthEvidence, sign_evidence, verify_auth_evidence
from harness.policy.decision import Actor, Approval


class SignAndVerifyRoundTrip(unittest.TestCase):
    def test_valid_evidence_verifies(self):
        ev = sign_evidence("s3cr3t", channel="slack", channel_message_id="msg1",
                           actor_id="alice")
        self.assertTrue(verify_auth_evidence(ev, secret="s3cr3t", actor_id="alice"))

    def test_wrong_secret_fails(self):
        ev = sign_evidence("s3cr3t", channel="slack", channel_message_id="msg1",
                           actor_id="alice")
        self.assertFalse(verify_auth_evidence(ev, secret="wrong", actor_id="alice"))

    def test_evidence_for_a_different_actor_does_not_verify(self):
        """Chặn đúng kịch bản S-11 lo: evidence hợp lệ cho alice bị dùng lại để "chứng
        minh" bob."""
        ev = sign_evidence("s3cr3t", channel="slack", channel_message_id="msg1",
                           actor_id="alice")
        self.assertFalse(verify_auth_evidence(ev, secret="s3cr3t", actor_id="bob"))

    def test_tampered_signed_payload_fails(self):
        ev = sign_evidence("s3cr3t", channel="slack", channel_message_id="msg1",
                           actor_id="alice")
        tampered = AuthEvidence(ev.channel, "msg-DIFFERENT", ev.signed_payload,
                                ev.signature, ev.timestamp)
        self.assertFalse(verify_auth_evidence(tampered, secret="s3cr3t", actor_id="alice"))

    def test_tampered_signature_fails(self):
        ev = sign_evidence("s3cr3t", channel="slack", channel_message_id="msg1",
                           actor_id="alice")
        tampered = AuthEvidence(ev.channel, ev.channel_message_id, ev.signed_payload,
                                "0" * 64, ev.timestamp)
        self.assertFalse(verify_auth_evidence(tampered, secret="s3cr3t", actor_id="alice"))

    def test_different_channel_message_id_produces_different_signature(self):
        ev1 = sign_evidence("s3cr3t", channel="slack", channel_message_id="msg1",
                            actor_id="alice", timestamp=1000.0)
        ev2 = sign_evidence("s3cr3t", channel="slack", channel_message_id="msg2",
                            actor_id="alice", timestamp=1000.0)
        self.assertNotEqual(ev1.signature, ev2.signature)


class ReplayWindow(unittest.TestCase):
    def test_fresh_evidence_within_window_verifies(self):
        ev = sign_evidence("s3cr3t", channel="slack", channel_message_id="m",
                           actor_id="a", timestamp=1000.0)
        self.assertTrue(verify_auth_evidence(ev, secret="s3cr3t", actor_id="a",
                                             max_age_s=300.0, now=1200.0))

    def test_evidence_older_than_max_age_is_rejected(self):
        ev = sign_evidence("s3cr3t", channel="slack", channel_message_id="m",
                           actor_id="a", timestamp=1000.0)
        self.assertFalse(verify_auth_evidence(ev, secret="s3cr3t", actor_id="a",
                                              max_age_s=300.0, now=1301.0))

    def test_evidence_from_the_future_is_rejected(self):
        """Chống clock skew ngược — một evidence "ký trong tương lai" so với verifier
        không nên tự động được tin."""
        ev = sign_evidence("s3cr3t", channel="slack", channel_message_id="m",
                           actor_id="a", timestamp=2000.0)
        self.assertFalse(verify_auth_evidence(ev, secret="s3cr3t", actor_id="a",
                                              now=1000.0))

    def test_evidence_signed_right_now_verifies_without_explicit_now(self):
        ev = sign_evidence("s3cr3t", channel="slack", channel_message_id="m", actor_id="a")
        self.assertTrue(verify_auth_evidence(ev, secret="s3cr3t", actor_id="a"))


class ActorAndApprovalCarryIt(unittest.TestCase):
    def test_actor_defaults_unverified(self):
        self.assertFalse(Actor.human("alice", via="slack").verified)

    def test_actor_can_report_verified(self):
        a = Actor.human("alice", via="slack", verified=True)
        self.assertTrue(a.verified)

    def test_approval_carries_evidence_for_later_audit(self):
        ev = sign_evidence("s3cr3t", channel="slack", channel_message_id="m",
                           actor_id="alice")
        approval = Approval(True, actor=Actor.human("alice", via="slack", verified=True),
                            evidence=ev)
        self.assertIs(approval.evidence, ev)
        self.assertTrue(approval.actor.verified)
        # Bằng chứng vẫn tự xác minh lại được độc lập với PolicyEngine — đúng mục đích
        # "audit sau này" của evidence, không phải một cờ tin suông.
        self.assertTrue(verify_auth_evidence(approval.evidence, secret="s3cr3t",
                                             actor_id=approval.actor.id))

    def test_approval_without_evidence_still_works_unchanged(self):
        """Không phá hợp đồng cũ — S-11's bản vá trước (Approval không evidence) vẫn y
        nguyên hành vi."""
        approval = Approval(True, actor=Actor.human("alice", via="callback"))
        self.assertIsNone(approval.evidence)
        self.assertFalse(approval.actor.verified)


if __name__ == "__main__":
    unittest.main()
