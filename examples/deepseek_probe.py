"""One real call against DeepSeek's live API — proves `DeepSeekProvider`'s wire
translation actually works end to end, not just against synthetic requests/responses.
`deepseek_provider.py`'s own module docstring points here.

    export DEEPSEEK_API_KEY=sk-...
    python3 examples/deepseek_probe.py

Uses `async with` — the resource-lifecycle pattern `DeepSeekProvider`'s own docstring
asks for, so this file is also the reference example for "how do I actually close one
of these," not just "how do I call it once and let the process exit anyway."
"""
import asyncio
import sys

sys.path.insert(0, "src")
sys.path.insert(0, "examples")

from deepseek_provider import DeepSeekProvider
from harness import Agent


async def main() -> None:
    async with DeepSeekProvider() as provider:
        agent = Agent(name="Probe", job="Reply with exactly the word: pong",
                      model="deepseek-chat", provider=provider,
                      budget="$0.05, 3 steps")
        result = await agent.atry_run("ping")
        print("stop_reason:", result.stop_reason)
        print("detail:", result.detail)
        print("text:", result.text)
        print("cost:", result.cost)
        print("usage:", result.usage)


if __name__ == "__main__":
    asyncio.run(main())
