"""Harness — build agents that are cheap to run, hard to misuse, and easy to start with."""
from __future__ import annotations

from .agent import Agent
from .budget.ledger import DEFAULT_BUDGET, Budget
from .errors import (BudgetExceeded, ConfigError, DuplicateToolError, HarnessError,
                     InvalidBudgetError, MissingEffectError, NonDeterministicPromptError,
                     PolicyDenied, ProviderError, RunFailed, SyncInAsyncContextError,
                     ToolContractError, ToolSchemaError, UnknownModelError,
                     UnsafeToolSetError)
from .models.base import ModelProvider
from .policy.auth_evidence import AuthEvidence, sign_evidence, verify_auth_evidence
from .policy.base import Ruling, Policy, ToolCall, Verdict
from .policy.decision import Actor, Approval, Decision, DecisionLog
from .result import Money, Result, StopReason, Step, Usage
from .run import RunContext
from .secrets import Secret, safe_for_display
from .session import Session, SessionExpiredError
from .tools import Effect, ToolSpec, tool

__version__ = "0.1.0.dev0"

#: S-02 re-check (design/07-risks-and-open-issues.md, `tests/test_roadmap.py`) — the
#: research's "Approval là một BẢN GHI" (decision id, actor, policy version, expiry,
#: audit entry) is `Decision` (`policy/decision.py`, built for S-11/S-29/T-8.2), not a
#: separate type. `ApprovalRecord` is an alias, not a second class, so there is exactly
#: one shape to keep in sync rather than two that could drift.
ApprovalRecord = Decision

__all__ = [
    "Agent", "tool", "Result", "StopReason", "Usage", "Step", "Money", "RunContext",
    "Effect", "Secret", "safe_for_display", "Policy", "Verdict", "Ruling", "ToolCall",
    "ToolSpec", "Actor", "Approval", "Decision", "ApprovalRecord", "DecisionLog",
    "AuthEvidence", "sign_evidence", "verify_auth_evidence",
    "Session", "SessionExpiredError",
    "Budget", "DEFAULT_BUDGET", "ModelProvider",
    "HarnessError", "ConfigError", "MissingEffectError", "ToolSchemaError",
    "DuplicateToolError", "NonDeterministicPromptError", "UnsafeToolSetError",
    "InvalidBudgetError", "UnknownModelError", "ToolContractError",
    "SyncInAsyncContextError", "RunFailed", "BudgetExceeded", "PolicyDenied",
    "ProviderError", "__version__",
]
