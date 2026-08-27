"""Python signature -> JSON Schema — docs/04-interfaces.md §1, task T-0.2.

Unsupported types raise at import.  There is deliberately no best-effort fallback:
an unannotated parameter defaulting to `str` would make add(3, 4) return "34",
which is a silently wrong answer a learner cannot search for (IDL-22).
"""
from __future__ import annotations

import enum
import inspect
import typing
from typing import Any, Literal, Union, get_args, get_origin

from ..errors import ToolSchemaError

_SCALARS: dict[Any, str] = {str: "string", int: "integer", float: "number", bool: "boolean"}

_TYPE_WORDS = "int = whole number   float = decimal   str = text   bool = yes/no"


def _schema_for(annotation: Any, *, fn_name: str, param: str) -> dict[str, Any]:
    if annotation in _SCALARS:
        return {"type": _SCALARS[annotation]}

    if annotation is list:                       # bare list — what a beginner writes
        return {"type": "array"}
    if annotation is dict:
        return {"type": "object"}

    origin = get_origin(annotation)
    args = get_args(annotation)

    if origin is Literal:
        return {"type": "string", "enum": [str(a) for a in args]}

    if origin is list:
        return {"type": "array", "items": _schema_for(args[0], fn_name=fn_name, param=param)}

    if origin is dict:
        return {"type": "object",
                "additionalProperties": _schema_for(args[1], fn_name=fn_name, param=param)}

    if origin is Union or origin is getattr(typing, "UnionType", None) or (
        origin is not None and str(origin) == "<class 'types.UnionType'>"
    ):
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _schema_for(non_none[0], fn_name=fn_name, param=param)
        return {"anyOf": [_schema_for(a, fn_name=fn_name, param=param) for a in non_none]}

    if isinstance(annotation, type) and issubclass(annotation, enum.Enum):
        return {"type": "string", "enum": [str(m.value) for m in annotation]}

    raise ToolSchemaError(
        f"tool {fn_name!r}: parameter {param!r} has type {annotation!r}, which cannot be\n"
        f"described to a model.\n\n"
        f"  Supported: {_TYPE_WORDS}\n"
        f"             plus list, dict, list[T], dict[str, T], Literal[...], Enum, T | None\n\n"
        "  -> docs/15-first-agent.md"
    )


def build(fn: Any) -> tuple[str, dict[str, Any]]:
    """Return (description, input_schema).  Raises ToolSchemaError at import."""
    name = fn.__name__
    doc = inspect.getdoc(fn) or ""
    summary = doc.strip().split("\n\n")[0].strip()
    if not summary:
        raise ToolSchemaError(
            f"tool {name!r} needs a docstring saying what it does — the model reads it to\n"
            f"decide when to use the tool.\n\n"
            f'      def {name}(...):\n'
            f'          """Say what this does in one line."""\n\n'
            "  -> docs/15-first-agent.md"
        )

    sig = inspect.signature(fn)
    # A parameter whose name starts with "_" is harness-internal (the parent's remaining
    # budget passed to a subagent, for instance).  It is never described to the model and
    # never requires an annotation — the model must not be able to set it.
    sig = sig.replace(parameters=[p for n, p in sig.parameters.items()
                                  if not n.startswith("_")])
    try:
        hints = typing.get_type_hints(fn)
    except Exception as exc:                                        # pragma: no cover
        raise ToolSchemaError(f"tool {name!r}: could not read type hints ({exc})") from exc

    props: dict[str, Any] = {}
    required: list[str] = []
    unannotated = [p for p in sig.parameters if p not in hints]
    if unannotated:
        shown = ", ".join(f"{p}: int" for p in sig.parameters)
        raise ToolSchemaError(
            f"Your tool needs to say what kind of thing each answer is.\n\n"
            f"    def {name}({', '.join(sig.parameters)}):"
            f"{' ' * 4}← you wrote this\n"
            f"    def {name}({shown}):{' ' * 4}← change it to this\n\n"
            f"  {_TYPE_WORDS}\n\n"
            f"  -> docs/15-first-agent.md"
        )

    for pname, p in sig.parameters.items():
        props[pname] = _schema_for(hints[pname], fn_name=name, param=pname)
        if p.default is inspect.Parameter.empty:
            required.append(pname)

    schema = {
        "type": "object",
        "properties": props,
        "required": required,
        "additionalProperties": False,      # mandatory: makes strict:true possible (ADR-022)
    }
    return summary, schema
