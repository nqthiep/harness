"""Harness — build agents that are cheap to run, hard to misuse, and easy to start with.

`__all__` below is the compatibility contract (poka-yoke register #8,
`docs/08-poka-yoke.md`).  `tests/test_public_api.py` is the CI test that register entry
names; read its module docstring before adding or removing a line here.

**All six seams are exported.**  `docs/02-architecture.md §4` declares six plugin seams —
Tool, `ModelProvider`, `Store`, `Policy`, `Exporter`, `Sandbox` — and three of them
(`Store`, `Exporter`, `Sandbox`) had no top-level export until now: implementing a
declared seam began with `from harness import Store` raising `ImportError`, and the first
independent reviewer to try it hit exactly that.  `docs/03-public-api.md §6` had reasoned
the other way ("protocols, imported from their own submodule when implementing one"), but
a seam is precisely the thing a third party is invited to implement, so making the invited
act the one that fails is backwards.  Each seam's value types come with it — you cannot
write a `Store` without `Memo`, an `Exporter` without `Event`/`EventKind`, or a `Sandbox`
without `Completed` — while the concrete implementations stay in their own modules
(`harness.memory.SqliteStore`, `harness.sandbox.Subprocess`, `harness.observe.otel`):
the seam is the contract, the implementations are a tier.
"""
from __future__ import annotations

from .agent import Agent
from .budget.ledger import DEFAULT_BUDGET, Budget
from .errors import (BudgetExceeded, ConfigError, DuplicateToolError, HarnessError,
                     InvalidBudgetError, MissingEffectError, NonDeterministicPromptError,
                     PolicyDenied, ProfileLoosenedSafetyError, ProviderError, RunFailed,
                     SyncInAsyncContextError, ToolContractError, ToolSchemaError,
                     UnknownModelError, UnsafeToolSetError)
from .memory.base import Memo, Store
from .middleware import (Middleware, ModelCall, RunIdentity, ShortCircuit,
                        ToolInvocation, with_middleware)
from .models.base import ModelProvider
from .observe.events import Event, EventKind, Exporter
from .policy.base import Ruling, Policy, ToolCall, Verdict
from .policy.decision import Actor, Approval, AuthEvidence
from .profile import Profile
from .result import Money, Result, StopReason, Step, Usage
from .run import RunContext
from .sandbox import Completed, Sandbox
from .secrets import Secret, safe_for_display
from .session import Session, SessionExpiredError
from .tools import Effect, ToolSpec, tool

__version__ = "0.1.0.dev0"

__all__ = [
    "Agent", "tool", "Result", "StopReason", "Usage", "Step", "Money", "RunContext",
    "Effect", "Secret", "safe_for_display", "Policy", "Verdict", "Ruling", "ToolCall",
    "ToolSpec", "Actor", "Approval", "AuthEvidence", "Session", "SessionExpiredError",
    # The three seams that had no export, each with the value types its implementer
    # must name.  `docs/02-architecture.md §4`, F13.
    "Store", "Memo", "Exporter", "Event", "EventKind", "Sandbox", "Completed",
    "Budget", "DEFAULT_BUDGET", "ModelProvider", "Middleware", "ModelCall",
    "ToolInvocation", "RunIdentity", "ShortCircuit", "with_middleware", "Profile",
    "HarnessError", "ConfigError", "MissingEffectError", "ToolSchemaError",
    "DuplicateToolError", "NonDeterministicPromptError", "UnsafeToolSetError",
    "InvalidBudgetError", "UnknownModelError", "ToolContractError",
    "SyncInAsyncContextError", "RunFailed", "BudgetExceeded", "PolicyDenied",
    "ProviderError", "ProfileLoosenedSafetyError", "__version__",
]
