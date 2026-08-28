"""Web tools — effect="external", so their output taints the run (ADR-011).

§15 tells a child `from harness.tools.web import search`.  Round 30 found this module did
not exist: the tutorial's "a tool that comes with Harness" section raised ModuleNotFoundError.
"""
from __future__ import annotations

import urllib.parse
import urllib.request

from .. import tool

_UA = "harness/0.1 (+https://github.com/nqthiep/harness)"
MAX_BYTES = 200_000


def _get(url: str, timeout: float = 15.0) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:      # noqa: S310
        return r.read(MAX_BYTES).decode("utf-8", "replace")


@tool(effect="external", timeout_s=20.0)
def search(query: str) -> str:
    """Search the web and get a few results back."""
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    html = _get(url)
    import re
    hits = re.findall(r'result__a"[^>]*>(.*?)</a>', html)[:5]
    clean = [re.sub(r"<[^>]+>", "", h).strip() for h in hits]
    return "\n".join(f"- {c}" for c in clean if c) or "no results"


@tool(effect="external", timeout_s=20.0)
def fetch(url: str) -> str:
    """Read the text of a web page."""
    import re
    html = _get(url)
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()
