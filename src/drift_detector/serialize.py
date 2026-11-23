"""Round-trip the comparison dataclasses through JSON.

The input bundle stores these objects so a later render does not need EDGAR,
and so a quote can be checked against the extracted section text.
"""

from __future__ import annotations

from dataclasses import MISSING, asdict, fields, is_dataclass
from typing import Union, get_args, get_origin, get_type_hints


def dump_company(company) -> dict:
    return asdict(company)


def load_company(payload: dict):
    from drift_detector.models import CompanyResult

    return _structure(CompanyResult, payload)


def _structure(cls, payload: dict):
    hints = get_type_hints(cls)
    kwargs = {}
    for field in fields(cls):
        if field.name not in payload:
            if field.default is not MISSING:
                kwargs[field.name] = field.default
            elif field.default_factory is not MISSING:
                kwargs[field.name] = field.default_factory()
            else:
                kwargs[field.name] = None
            continue
        kwargs[field.name] = _convert(hints[field.name], payload[field.name])
    return cls(**kwargs)


def _convert(tp, raw):
    if raw is None:
        return None
    origin = get_origin(tp)
    if origin in (Union, None) or _is_union(origin):
        args = [arg for arg in get_args(tp) if arg is not type(None)]
        if len(args) == 1:
            return _convert(args[0], raw)
    if origin is list:
        (inner,) = get_args(tp)
        return [_convert(inner, item) for item in raw or []]
    if origin is dict:
        _key_type, value_type = get_args(tp)
        return {_convert(str, key): _convert(value_type, value) for key, value in (raw or {}).items()}
    if isinstance(tp, type) and is_dataclass(tp):
        return _structure(tp, raw)
    if tp is float and isinstance(raw, int) and not isinstance(raw, bool):
        return float(raw)
    return raw


def _is_union(origin) -> bool:
    import types

    return origin is types.UnionType
