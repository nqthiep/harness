"""Arithmetic, evaluated without `eval`.

`eval` on model-supplied text is arbitrary code execution, which would make this a
`danger` tool wearing a `read` label — exactly the mislabelling the effect classes exist
to prevent.
"""
from __future__ import annotations

import ast
import operator
from typing import Callable

from .. import tool

_OPS: dict[type, Callable[..., float]] = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
        ast.Div: operator.truediv, ast.Pow: operator.pow, ast.Mod: operator.mod,
        ast.FloorDiv: operator.floordiv, ast.USub: operator.neg, ast.UAdd: operator.pos}


#: G-4, design/review-architect.md: `9**9**9**9` asks CPython for an integer with ~370
#: million digits, inside one C-level `long_pow` call that never releases the GIL —
#: `effect="read"` means this is auto-approved, run in parallel, retried on failure, and
#: nothing (`timeout_s`, the wall-clock budget, T-6.2 cancellation) can interrupt it once
#: it starts, because none of that machinery can run until the call returns. A calculator
#: for a language model has no legitimate use for tetration-scale exponents — refuse
#: BEFORE computing, not after timing out.
_MAX_POW_EXPONENT = 64
_MAX_POW_RESULT_BITS = 10_000


def _check_pow_bounds(base: float, exponent: float) -> None:
    if abs(exponent) > _MAX_POW_EXPONENT:
        raise ValueError(
            f"exponent {exponent} is too large (limit {_MAX_POW_EXPONENT}) — refused "
            f"before computing it, not after it hangs")
    if isinstance(base, int) and isinstance(exponent, int) and exponent > 0:
        if base.bit_length() * exponent > _MAX_POW_RESULT_BITS:
            raise ValueError(
                f"{base}**{exponent} would produce a number with roughly "
                f"{base.bit_length() * exponent} bits — refused before computing it, "
                f"not after it hangs")


def _ev(node: ast.AST) -> float:
    """Only the operators in `_OPS`; anything else raises. No eval, ever (AC-41)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _ev(node.left), _ev(node.right)
        if type(node.op) is ast.Pow:
            _check_pow_bounds(left, right)
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_ev(node.operand))
    raise ValueError("only numbers and + - * / // % ** are allowed")


@tool(effect="read")
def calculate(expression: str) -> float:
    """Work out a sum, like 12 * (3 + 4)."""
    return _ev(ast.parse(expression, mode="eval").body)
