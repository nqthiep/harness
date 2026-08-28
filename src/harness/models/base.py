"""ModelProvider protocol and request/response types — docs/04-interfaces.md §0, §2."""
from __future__ import annotations

from .._value import value

import hashlib
import json
from typing import Any, Callable, Mapping, Protocol

from ..result import Usage

ContentBlock = Mapping[str, Any]
SystemBlock = Mapping[str, Any]
DeltaFn = Callable[[str], None]


@value
class ModelRequest:
    __hash__ = None                      # holds Mappings — memoize on canonical bytes
    model: str
    system: tuple[SystemBlock, ...]
    tools: tuple[Mapping[str, Any], ...]
    messages: tuple[Mapping[str, Any], ...]
    max_tokens: int                      # derived by the ledger (ADR-017)
    effort: str = "medium"
    stream: bool = False
    output_format: Mapping[str, Any] | None = None

    def cache_key(self) -> str:
        """blake2b over canonical bytes — the object itself is unhashable (Round 20)."""
        payload = json.dumps(
            {"model": self.model, "system": list(self.system), "tools": list(self.tools),
             "messages": list(self.messages)},
            sort_keys=True, separators=(",", ":"), default=str,
        )
        return hashlib.blake2b(payload.encode(), digest_size=16).hexdigest()


@value
class ModelResponse:
    content: tuple[ContentBlock, ...]
    stop_reason: str
    usage: Usage
    model: str
    raw_id: str = ""
    stop_details: Mapping[str, Any] | None = None


class ModelProvider(Protocol):
    name: str
    async def complete(self, request: ModelRequest, *, on_delta: DeltaFn | None = None) -> ModelResponse: ...
    def price(self, model: str) -> Any: ...
    async def count_input_tokens(self, request: ModelRequest) -> int: ...
    def max_output(self, model: str) -> int: ...
