"""Exception hierarchy — docs/04-interfaces.md §7.

Everything under ConfigError is raised at import or construction time, never from
inside RunEngine.step().  AC-06 asserts that.
"""
from __future__ import annotations


class HarnessError(Exception):
    """Base for everything this library raises."""


class ConfigError(HarnessError):
    """A mistake in how the agent or a tool was set up.

    Always raised at import or construction time.  Subclasses must render the four
    sections required by docs/03-public-api.md §8: what, where, the fix, a docs anchor.
    """


class MissingEffectError(ConfigError): ...
class ToolSchemaError(ConfigError): ...
class DuplicateToolError(ConfigError): ...
class NonDeterministicPromptError(ConfigError): ...
class UnsafeToolSetError(ConfigError): ...
class InvalidBudgetError(ConfigError): ...
class UnknownModelError(ConfigError): ...

class ToolContractError(HarnessError): ...
class SyncInAsyncContextError(HarnessError): ...


class RunFailed(HarnessError):
    """Raised by .run() when the run did not complete.  Carries the partial work."""

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
