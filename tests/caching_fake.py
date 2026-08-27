"""A fake provider that implements real prefix-match caching semantics.

The point of the SC-4 benchmark is to measure the ASSEMBLER's byte-stability, so the
fake must model the actual rule: a cache entry is keyed on the exact bytes of the
rendered prompt up to a cache_control breakpoint, and a hit reads that whole prefix.
A stub that just reports a number would measure nothing.
"""
from __future__ import annotations

import json
import sys
sys.path.insert(0, "src")

from harness.models.fake import FakeModel
from harness.models.pricing import MAX_OUTPUT, price
from harness.result import Usage

MIN_CACHEABLE_TOKENS = 1024


def _bytes(block):
    """cache_control marks a boundary; it is not part of the cached content, so it is
    stripped before hashing.  Otherwise moving a breakpoint would invalidate the prefix
    it was meant to make readable."""
    b = {k: v for k, v in dict(block).items() if k != "cache_control"}
    return json.dumps(b, sort_keys=True, separators=(",", ":"), default=str)


def _blocks(request):
    """Rendered order: tools -> system -> messages, as a list of (bytes, is_breakpoint)."""
    out = []
    for tdef in request.tools:
        out.append((_bytes(tdef), False))
    for sb in request.system:
        out.append((_bytes(sb), "cache_control" in sb))
    for msg in request.messages:
        content = msg.get("content")
        blocks = content if isinstance(content, list) else [{"type": "text", "text": content}]
        for b in blocks:
            bp = isinstance(b, dict) and "cache_control" in b
            out.append((_bytes(b), bp))
    return out


class CachingFake(FakeModel):
    def __init__(self, script, model="claude-opus-5"):
        super().__init__(script)
        self._model = model
        self._cache: set[str] = set()          # prefixes previously written
        self.turns: list[Usage] = []

    def price(self, model): return price(self._model)
    def max_output(self, model): return MAX_OUTPUT[self._model]

    async def count_input_tokens(self, request):
        return sum(len(b) for b, _ in _blocks(request)) // 4

    async def complete(self, request, *, on_delta=None):
        blocks = _blocks(request)
        running, prefix_at_bp, tokens_at_bp = "", [], []
        for text, is_bp in blocks:
            running += text
            if is_bp:
                prefix_at_bp.append(running)
                tokens_at_bp.append(len(running) // 4)

        total = len(running) // 4
        read = 0
        # Longest previously-written prefix that still matches exactly.
        for pfx, tok in zip(reversed(prefix_at_bp), reversed(tokens_at_bp)):
            if pfx in self._cache and tok >= MIN_CACHEABLE_TOKENS:
                read = tok
                break
        written = 0
        for pfx, tok in zip(prefix_at_bp, tokens_at_bp):
            if pfx not in self._cache and tok >= MIN_CACHEABLE_TOKENS:
                self._cache.add(pfx)
                written = max(written, tok - read)

        r = await FakeModel.complete(self, request, on_delta=on_delta)
        u = Usage(input_tokens=max(0, total - read), output_tokens=r.usage.output_tokens,
                  cache_read_input_tokens=read, cache_creation_input_tokens=written)
        self.turns.append(u)
        from harness.models.base import ModelResponse
        return ModelResponse(r.content, r.stop_reason, u, r.model)
