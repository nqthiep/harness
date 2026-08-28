"""OpenViking store — Round 36, task T-4.4.

[§04.5](../../../docs/04-interfaces.md#5-store) has said since Round 7 that FTS5 keyword
search is *not* semantic recall and that **"a user who wants it implements `Store`"**.
This is that implementation: OpenViking is a context database for agents, and it lands on
a seam the design already reserved rather than needing a new one.

Three things this module refuses to do, each of which would quietly cost an invariant:

1. **`recall` is `external`, not `read`.**  A context database ingests web pages
   (`ov add-resource https://…`).  Anything it hands back may be attacker-authored, so a
   recall is an untrusted read and must raise taint — otherwise a poisoned memory buys
   `danger`-tool privileges and the taint lattice has a hole the size of the memory
   system (ADR-035).
2. **It holds a capability-narrowed client.**  `SyncHTTPClient` exposes `admin_*`, `rm`
   and `delete_session`.  A store binding that hands those to a tool is a least-privilege
   violation, so the calls this module can make are a fixed, listed set.
3. **It never lets a key become a path.**  Keys are interpolated into a `viking://` URI;
   an unvalidated key escapes its namespace (`../../resources`) and reads other agents'
   data.  Keys are checked at the boundary against a strict pattern.
"""
from __future__ import annotations

import re
import time
from typing import Any, Sequence

from ..errors import ConfigError, HarnessError
from ..tools import tool
from .base import Memo

#: The only client methods this binding may call.  Anything outside the set is a
#: capability the store does not need and therefore must not hold (least privilege,
#: [§06.4](../../../docs/06-safety.md#4-least-privilege)).
ALLOWED_CALLS = frozenset({"search", "read", "write", "health", "initialize", "close"})

#: A key is a name, never a path.  `..`, `/` and `:` are all rejected rather than escaped:
#: escaping is a thing you can get subtly wrong, refusing is not (register #78).
_KEY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._\-]{0,127}$")


class VikingKeyError(ConfigError):
    """A key that could escape its namespace.  A `ConfigError`, so it raises at the call
    that made it rather than becoming a tool result the model reads and works around."""


class VikingUnavailable(HarnessError):
    """The context database could not be reached.  Distinct from an empty result: an
    agent that cannot tell 'nothing remembered' from 'the database is down' will
    confidently tell a customer their order does not exist (fail safe, §02.7)."""


def check_key(key: str) -> str:
    if not isinstance(key, str) or not _KEY.match(key):
        raise VikingKeyError(
            f"{key!r} is not a usable memory key.\n\n"
            "  A key is a name, not a path — letters, numbers, dot, dash and underscore,\n"
            "  up to 128 characters.  This is checked because a key becomes part of a\n"
            "  viking:// address, and a key with '/' or '..' in it would read somebody\n"
            "  else's memories.\n\n"
            "  -> docs/06-safety.md#4-least-privilege"
        )
    return key


class VikingStore:
    """`Store` over an OpenViking server ([§04.5](../../../docs/04-interfaces.md#5-store)).

    >>> store = VikingStore(url="http://localhost:8080", namespace="support")
    >>> await store.put("khach-01", "thích trả lời ngắn")
    >>> [m.value for m in await store.search("khách này thích gì")]
    ['thích trả lời ngắn']
    """

    def __init__(self, *, url: str | None = None, api_key: str | None = None,
                 namespace: str = "default", client: Any = None,
                 read_only: bool = False, timeout: float = 10.0) -> None:
        check_key(namespace)
        self.namespace, self.read_only = namespace, read_only
        if client is None:
            try:
                from openviking_sdk import AsyncHTTPClient
            except ImportError:                       # pragma: no cover - import guard
                raise ConfigError(
                    "The OpenViking store needs the openviking client:\n\n"
                    "      pip install 'harness[viking]'\n\n"
                    "  It talks to an openviking-server over HTTP; the server itself is a\n"
                    "  separate process, like any other database.\n\n"
                    "  -> docs/04-interfaces.md#5-store"
                ) from None
            client = AsyncHTTPClient(url=url, api_key=api_key, timeout=timeout)
        self._c = client
        self._ready = False

    # -- Store protocol ---------------------------------------------------
    def _uri(self, key: str) -> str:
        return f"viking://memories/{self.namespace}/{check_key(key)}"

    async def _ensure(self) -> None:
        if not self._ready:
            await self._call("initialize")
            self._ready = True

    async def _call(self, name: str, *a: Any, **kw: Any) -> Any:
        """Every request goes through here, so the capability list is enforced rather
        than remembered, and every failure is classified in one place."""
        if name not in ALLOWED_CALLS:
            raise AssertionError(f"{name!r} is not a capability this store holds")
        try:
            return await getattr(self._c, name)(*a, **kw)
        except Exception as exc:
            code = getattr(exc, "code", None)
            if code in ABSENT:
                return None
            if code in CREDENTIALS:
                raise ConfigError(
                    f"the context database refused this client ({code}).\n\n"
                    "  Check the url and api_key you passed to VikingStore, or the\n"
                    "  server's own account settings.\n\n"
                    "  -> docs/04-interfaces.md#5-store"
                ) from None
            raise VikingUnavailable(
                f"could not reach the context database "
                f"({code or type(exc).__name__}: {exc})"
            ) from exc

    async def get(self, key: str) -> str | None:
        await self._ensure()
        out = await self._call("read", self._uri(key))
        return out if isinstance(out, str) and out else None

    async def put(self, key: str, value: str, *, ttl_s: float | None = None) -> None:
        if self.read_only:
            raise VikingKeyError("this store was opened read_only=True")
        await self._ensure()
        await self._call("write", self._uri(key), value)

    async def delete(self, key: str) -> None:
        """Writes an empty value; `rm` is deliberately not a capability this store holds.

        A store that can delete is a store a prompt-injected model can use to destroy a
        customer's history.  Emptying is reversible in the database's own history; `rm`
        is not (ADR-035).
        """
        if self.read_only:
            raise VikingKeyError("this store was opened read_only=True")
        await self._ensure()
        await self._call("write", self._uri(key), "")

    async def search(self, query: str, *, limit: int = 5) -> Sequence[Memo]:
        await self._ensure()
        raw = await self._call("search", query,
                               target_uri=f"viking://memories/{self.namespace}",
                               limit=limit)
        return _memos(raw, limit)

    async def close(self) -> None:
        await self._call("close")
        self._ready = False

    # -- the tools, classified so the caller cannot get it wrong ----------
    def tools(self) -> list:
        """Model-facing tools over this store, with the effect classes already set.

        The classification is the whole safety decision, so it ships here rather than
        being left to each author: `recall` is `external` because what comes back may
        have been ingested from a web page (ADR-035).
        """
        store = self

        @tool(effect="external")
        async def recall(cau_hoi: str) -> str:
            """Tìm trong trí nhớ dài hạn những gì liên quan tới câu hỏi."""
            memos = await store.search(cau_hoi)
            return "\n".join(f"- {m.value}" for m in memos) or "(chưa nhớ gì)"

        if store.read_only:
            return [recall]

        @tool(effect="write")
        async def remember(ten: str, noi_dung: str) -> str:
            """Ghi nhớ một điều để lần sau dùng lại."""
            await store.put(ten, noi_dung)
            return f"đã nhớ {ten}"

        return [recall, remember]


#: The SDK's own error codes, read from `ERROR_CODE_TO_EXCEPTION`.  Compared as strings so
#: the binding does not import the SDK at module import (NFR-01, IDL-07).
#:
#: Only these two mean "there is nothing there".  Everything else — including a code this
#: version of the binding has never heard of — is a failure, because **mapping an unknown
#: outcome to "nothing remembered" is fail-open**: the agent would tell a customer their
#: order does not exist.  Same rule as IDL-30 for an unrecognised provider stop reason.
ABSENT = frozenset({"NOT_FOUND", "INVALID_URI"})

#: Not transient, and not absence: a wrong key or a revoked grant.  Retrying cannot fix
#: it and an empty result would hide it, so it surfaces as a `ConfigError`.
CREDENTIALS = frozenset({"UNAUTHENTICATED", "PERMISSION_DENIED"})


def _memos(raw: Any, limit: int) -> list[Memo]:
    """OpenViking returns a dict whose result list has moved between versions; read it
    defensively rather than trusting one shape (register #79)."""
    if not isinstance(raw, dict):
        return []
    rows = (raw.get("results") or raw.get("nodes")
            or raw.get("items") or raw.get("data") or [])
    if not isinstance(rows, list):
        return []
    now = time.time()
    out = []
    for r in rows[:limit]:
        if not isinstance(r, dict):
            continue
        text = r.get("content") or r.get("text") or r.get("abstract") or ""
        if not text:
            continue
        out.append(Memo(key=str(r.get("uri") or r.get("key") or ""), value=str(text),
                        score=float(r.get("score") or 0.0),
                        updated_at=float(r.get("updated_at") or now)))
    return out
