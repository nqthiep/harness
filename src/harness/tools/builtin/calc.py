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


def _ev(node: ast.AST) -> float:
    """Only the operators in `_OPS`; anything else raises. No eval, ever (AC-41)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_ev(node.left), _ev(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_ev(node.operand))
    raise ValueError("only numbers and + - * / // % ** are allowed")


@tool(effect="read")
def calculate(expression: str) -> float:
    """Work out a sum, like 12 * (3 + 4)."""
    return _ev(ast.parse(expression, mode="eval").body)
