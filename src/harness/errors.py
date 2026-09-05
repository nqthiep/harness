"""Exception hierarchy — docs/04-interfaces.md §7.

Everything under ConfigError is raised at import or construction time, never from
inside RunEngine.step().  AC-06 asserts that.
"""
from __future__ import annotations


class HarnessError(Exception):
    """Base for everything this library raises."""


class ConfigError(HarnessError):
    """A mistake in how the agent or a tool was set up.

    Raised at import or construction time, with exactly one exception:
    `SharedPolicyStateError` below, which is a setup mistake that CANNOT be seen until a
    run has already happened.  Everything else in this family is a promise that a
    misconfigured agent never starts.  Subclasses must render the four sections required
    by docs/03-public-api.md §8: what, where, the fix, a docs anchor.
    """


class MissingEffectError(ConfigError): ...
class ToolSchemaError(ConfigError): ...
class DuplicateToolError(ConfigError): ...
class NonDeterministicPromptError(ConfigError): ...
class UnsafeToolSetError(ConfigError): ...
class InvalidBudgetError(ConfigError): ...
class UnknownModelError(ConfigError): ...
class ProfileLoosenedSafetyError(ConfigError): ...

class ToolContractError(HarnessError): ...
class SyncInAsyncContextError(HarnessError): ...


class RunFailed(HarnessError):
    """Raised by .run() when the run did not complete.  Carries the partial work."""

    def __init__(self, message: str, partial: object) -> None:
        super().__init__(message)
        self.partial = partial


class SharedPolicyStateError(ConfigError):
    """A policy instance mutated during a run, so the next run would inherit it.

    The one `ConfigError` that cannot be raised at construction: a policy holding
    configuration and a policy holding a state machine look identical until one of them
    changes, so this is detected by comparing a snapshot taken before the run with one
    taken after (Round 34).

    It therefore carries `.partial`, the same way `RunFailed` does, and for the same
    reason: by the time this is detectable the model has been called, tools have run and
    the run has been billed.  Raising a bare `ConfigError` here discarded the `Result` —
    the cost, the text, and the pointer to the transcript that would let someone see what
    the leaking policy actually did.
    """

    def __init__(self, message: str, partial: object) -> None:
        super().__init__(message)
        self.partial = partial


class BudgetExceeded(HarnessError): ...
class PolicyDenied(HarnessError): ...


class ProviderError(HarnessError):
    """`retry_after_s`: N-5 — how long the vendor asked us to wait before retrying (a
    real `Retry-After`/`retry-after` header, read by `models/anthropic.py::_map()`),
    when it gave one. `None` means the vendor didn't say — `retry.py::with_provider_retry`
    falls back to its own exponential backoff in that case, never to zero wait."""

    def __init__(self, message: str, *, retry_after_s: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class ProviderAuthError(ProviderError): ...
class ProviderBadRequest(ProviderError): ...
class ProviderRateLimited(ProviderError): ...
class ProviderUnavailable(ProviderError): ...
class ProviderTimeout(ProviderError): ...
