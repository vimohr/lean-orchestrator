"""Conversion between nested dataclasses and JSON-compatible dictionaries.

The research state is stored as JSON so that humans, agents, and git diffs can
read it. Dataclasses give the orchestrator typed access to the same data. Unknown
keys are ignored and missing optional fields take their defaults, so workspaces
keep loading across additive schema changes.
"""

from __future__ import annotations

import dataclasses
import types
import typing
from typing import Any, TypeVar, Union

T = TypeVar("T")


def to_dict(value: Any) -> Any:
    """Recursively convert dataclasses, lists, and dicts into JSON-compatible values."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: to_dict(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, dict):
        return {str(key): to_dict(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_dict(item) for item in value]
    return value


def from_dict(cls: type[T], data: Any) -> T:
    """Build an instance of dataclass ``cls`` from a dictionary produced by :func:`to_dict`."""
    return _convert(cls, data, cls.__name__)


def _convert(annotation: Any, value: Any, where: str) -> Any:
    origin = typing.get_origin(annotation)
    arguments = typing.get_args(annotation)

    if annotation is Any:
        return value
    if origin in (Union, types.UnionType):
        if value is None and type(None) in arguments:
            return None
        candidates = [argument for argument in arguments if argument is not type(None)]
        errors = []
        for candidate in candidates:
            try:
                return _convert(candidate, value, where)
            except (TypeError, ValueError) as error:
                errors.append(str(error))
        raise TypeError(f"{where}: value {value!r} matches no alternative ({'; '.join(errors)})")
    if origin is list:
        if not isinstance(value, list):
            raise TypeError(f"{where}: expected a list, got {type(value).__name__}")
        (item_type,) = arguments or (Any,)
        return [_convert(item_type, item, f"{where}[{index}]") for index, item in enumerate(value)]
    if origin is dict:
        if not isinstance(value, dict):
            raise TypeError(f"{where}: expected an object, got {type(value).__name__}")
        key_type, item_type = arguments or (str, Any)
        return {
            _convert(key_type, key, f"{where}.<key>"): _convert(item_type, item, f"{where}.{key}")
            for key, item in value.items()
        }
    if dataclasses.is_dataclass(annotation):
        if not isinstance(value, dict):
            raise TypeError(f"{where}: expected an object for {annotation.__name__}, got {type(value).__name__}")
        hints = typing.get_type_hints(annotation)
        kwargs = {}
        for field in dataclasses.fields(annotation):
            if field.name in value:
                kwargs[field.name] = _convert(hints[field.name], value[field.name], f"{where}.{field.name}")
            elif field.default is dataclasses.MISSING and field.default_factory is dataclasses.MISSING:
                raise TypeError(f"{where}: missing required field {field.name!r}")
        return annotation(**kwargs)
    if annotation is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(f"{where}: expected a number, got {value!r}")
        return float(value)
    if annotation is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"{where}: expected an integer, got {value!r}")
        return value
    if annotation in (str, bool):
        if not isinstance(value, annotation):
            raise TypeError(f"{where}: expected {annotation.__name__}, got {value!r}")
        return value
    return value
