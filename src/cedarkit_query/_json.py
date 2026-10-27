"""Strict JSON copying, immutable containers, and duplicate-safe text parsing."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from types import MappingProxyType

import yaml


def pointer(parts):
    return "".join("/" + str(p).replace("~", "~0").replace("/", "~1") for p in parts)


def plain(value, path="", active=None):
    from .model import ProtocolValue

    if isinstance(value, ProtocolValue):
        value = value.to_dict()
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    if active is None:
        active = set()
    if isinstance(value, (Mapping, list, tuple)):
        if id(value) in active:
            raise ValueError(f"{path}: cyclic value")
        active.add(id(value))
        try:
            if isinstance(value, Mapping):
                if any(not isinstance(k, str) for k in value):
                    raise ValueError(f"{path}: object keys must be strings")
                return {
                    k: plain(v, path + pointer([k]), active) for k, v in value.items()
                }
            return [plain(v, path + pointer([i]), active) for i, v in enumerate(value)]
        finally:
            active.remove(id(value))
    raise ValueError(f"{path}: unsupported JSON value {type(value).__name__}")


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({k: freeze(v) for k, v in value.items()})
    if isinstance(value, list):
        return tuple(freeze(v) for v in value)
    return value


def thaw(value):
    if isinstance(value, Mapping):
        return {k: thaw(v) for k, v in value.items()}
    if isinstance(value, tuple):
        return [thaw(v) for v in value]
    return value


def canonical(value):
    return json.dumps(
        plain(value),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


class _Loader(yaml.SafeLoader):
    pass


_Loader.yaml_implicit_resolvers = {
    k: [(tag, regex) for tag, regex in values if tag != "tag:yaml.org,2002:timestamp"]
    for k, values in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str):
            raise ValueError("YAML object keys must be strings")
        if key in result:
            raise ValueError(f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_Loader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _mapping)


def parse(text, format=None):
    selected = format or ("json" if text.lstrip().startswith(("{", "[")) else "yaml")
    if selected == "json":

        def nonfinite(token):
            raise ValueError(f"non-finite JSON number: {token}")

        return json.loads(text, object_pairs_hook=_pairs, parse_constant=nonfinite)
    if selected == "yaml":
        return yaml.load(text, Loader=_Loader)
    raise ValueError(f"unsupported format: {selected}")


def serialize(value, format="json"):
    value = plain(value)
    if format == "json":
        return json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + "\n"
    if format == "yaml":
        return yaml.safe_dump(value, allow_unicode=True, sort_keys=False)
    raise ValueError(f"unsupported format: {format}")
