"""T-7.3/T-7.4 — the `Sandbox` seam, docs/17-research-alignment.md M7.

A sixth plugin seam, extending ADR-002's "five seams, everything else core" — `Sandbox`
passes the same three-part test (docs/02-architecture.md §2.4) the other five do: a
reasonable third party would publish one (Docker/Firecracker/gVisor wrappers all exist on
PyPI today), core needs zero knowledge of a concrete implementation, and two genuinely
different implementations ship today — `InProcess` and `Subprocess` below. Recorded as
ADR-047 (docs/12-decision-logs.md).

§01.5's non-goal stands: this library does not, and will not, embed a container runtime.
What it CAN do without one — an honest "no isolation" option that says so, a
clean-environment subprocess boundary, and a seam a real sandbox plugs into without
touching core — is what these two implementations are.
"""
from __future__ import annotations

import asyncio
import subprocess as _subprocess
from typing import Mapping, Protocol, Sequence

from ._value import value
from .secrets import Secret


@value
class Completed:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool


class Sandbox(Protocol):
    async def run(self, cmd: Sequence[str], *, cwd: str, env: Mapping[str, str],
                 timeout: float) -> Completed: ...


def _check_env(env: Mapping[str, str]) -> None:
    """T-7.4: `Sandbox.run` never accepts a `Secret` instance in `env`. `Secret.__str__`
    already masks (`<name hidden>`, IDL-32) so a raw pass-through would not LEAK the
    plaintext — it would silently hand the child process a useless placeholder string
    instead, which is a worse bug than a leak in one way: nothing signals the mistake, and
    the child fails confusingly instead of the caller failing loudly (IDL-30, fail
    visible). Reject outright: a tool author who needs a secret in the child's
    environment must call `secret.reveal()` themselves and knows exactly what they wrote.
    """
    for k, v in env.items():
        if isinstance(v, Secret):
            raise TypeError(
                f"env[{k!r}] is a Secret, not a string — Sandbox.run never accepts one "
                f"directly (T-7.4). Use `with secret.reveal() as plaintext:` and pass "
                f"`plaintext` explicitly if the child process genuinely needs it.\n\n"
                "  -> docs/06-safety.md"
            )


class InProcess:
    """No isolation at all — `Popen` in the calling process's own filesystem and network
    namespace. Named for what it honestly is, not what it sounds safe as: this exists so
    a caller with no isolation requirement doesn't have to spin up a subprocess sandbox
    they don't need, not so anyone mistakes it for one (W-03: approval is not isolation,
    and neither is a name that merely sounds like it).

    **`env` is used exactly as given — never merged with `os.environ`.** A caller who
    wants the child to see `PATH` has to pass `PATH` explicitly. Silently inheriting the
    parent's environment is exactly the leak T-7.4 exists to close: this process's own
    `os.environ` can carry the harness's own provider API key.
    """

    async def run(self, cmd: Sequence[str], *, cwd: str, env: Mapping[str, str],
                 timeout: float) -> Completed:
        _check_env(env)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=cwd, env=dict(env),
                stdout=_subprocess.PIPE, stderr=_subprocess.PIPE)
        except FileNotFoundError as exc:
            return Completed(127, "", str(exc), False)
        try:
            async with asyncio.timeout(timeout):
                out, err = await proc.communicate()
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return Completed(-1, "", f"timed out after {timeout}s", True)
        return Completed(proc.returncode or 0, out.decode("utf-8", "replace"),
                         err.decode("utf-8", "replace"), False)


class Subprocess:
    """The honest ceiling of what a pure-Python library can do without a container
    runtime (§01.5's stated non-goal): a clean-environment child process, `cwd` fixed to
    the caller's workspace, a hard timeout. **Not** namespace/cgroup/network isolation —
    a determined command can still see the host filesystem outside `cwd` and the host
    network. This is the seam a REAL sandbox (Docker, Firecracker, gVisor) plugs into;
    shipping this honest version rather than nothing is what makes that seam provable
    today (T-7.3's own Done criterion — a third party plugs in without touching core).
    """

    async def run(self, cmd: Sequence[str], *, cwd: str, env: Mapping[str, str],
                 timeout: float) -> Completed:
        _check_env(env)
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd, cwd=cwd, env=dict(env),          # dict(env): never os.environ
                stdout=_subprocess.PIPE, stderr=_subprocess.PIPE,
                start_new_session=True)                 # own process group, for a clean kill
        except FileNotFoundError as exc:
            return Completed(127, "", str(exc), False)
        try:
            async with asyncio.timeout(timeout):
                out, err = await proc.communicate()
        except TimeoutError:
            import os
            import signal
            try:
                os.killpg(proc.pid, signal.SIGKILL)     # the whole process group, not just proc
            except ProcessLookupError:
                pass
            await proc.wait()
            return Completed(-1, "", f"timed out after {timeout}s", True)
        return Completed(proc.returncode or 0, out.decode("utf-8", "replace"),
                         err.decode("utf-8", "replace"), False)
