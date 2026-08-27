"""Plugin registry — task T-4.1, ADR-008.

Entry-point discovery is OPT-IN.  Auto-loading every installed entry point means a
transitively installed package can register tools — a supply-chain backdoor dressed as
convenience.  A plugin declares the maximum effect class it provides; registering above
that ceiling raises at registration, never at run time.
"""
from __future__ import annotations

from typing import Sequence

from ..errors import ConfigError
from ..tools import Effect, ToolSpec

API_VERSION = 1
_ORDER = {Effect.READ: 0, Effect.WRITE: 1, Effect.EXTERNAL: 2, Effect.DANGER: 3}


class PluginRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}
        self._sources: dict[str, str] = {}

    def register(self, name: str, tools: Sequence[ToolSpec], *,
                 provides: Effect | str = Effect.READ, api_version: int = API_VERSION) -> None:
        if api_version != API_VERSION:
            raise ConfigError(
                f"plugin {name!r} was built for harness plugin API v{api_version}; "
                f"this build speaks v{API_VERSION}.\n"
                f"  Upgrade the plugin, or pin harness to a matching version."
            )
        ceiling = Effect(provides)
        for spec in tools:
            if _ORDER[spec.effect] > _ORDER[ceiling]:
                raise ConfigError(
                    f"plugin {name!r} declared it provides at most {ceiling.value!r} tools, "
                    f"but {spec.name!r} is {spec.effect.value!r}.\n\n"
                    "  A plugin cannot quietly hand an agent more power than it declared.\n"
                    "  Either lower the effect on that tool, or declare "
                    f'provides="{spec.effect.value}"\n'
                    "  so the extra capability is visible to whoever installs it."
                )
            if spec.name in self._tools:
                raise ConfigError(
                    f"plugin {name!r} registers tool {spec.name!r}, already provided by "
                    f"{self._sources[spec.name]!r}."
                )
            self._tools[spec.name] = spec
            self._sources[spec.name] = name

    def discover(self) -> None:
        """Load installed entry points.  Only ever called under Agent(discover=True)."""
        try:
            from importlib.metadata import entry_points
        except ImportError:                              # pragma: no cover
            return
        for ep in entry_points(group="harness.plugins"):
            plugin = ep.load()
            self.register(ep.name, getattr(plugin, "TOOLS", ()),
                          provides=getattr(plugin, "PROVIDES", Effect.READ),
                          api_version=getattr(plugin, "API_VERSION", API_VERSION))

    def tools(self) -> list[ToolSpec]:
        return [self._tools[k] for k in sorted(self._tools)]

    def source_of(self, tool_name: str) -> str:
        return self._sources.get(tool_name, "")


_GLOBAL = PluginRegistry()
register = _GLOBAL.register
