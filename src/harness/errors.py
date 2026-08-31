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


class ProviderError(HarnessError): ...
class ProviderAuthError(ProviderError): ...
class ProviderBadRequest(ProviderError): ...


class ProviderRateLimited(ProviderError):
    """N-5 (design/07-risks-and-open-issues.md): `retry_after` carries the vendor's own
    `Retry-After` header, in seconds, when the provider sent one — `None` when it did
    not (docs/10 §3 promises "honoring `Retry-After`"; a caller has to be told when
    there was none to honor, rather than silently falling back without saying so)."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ProviderUnavailable(ProviderError): ...
class ProviderTimeout(ProviderError): ...
