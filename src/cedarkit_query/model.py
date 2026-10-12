"""Immutable JSON-compatible values for the CedarKit grid query protocol."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from types import MappingProxyType
from typing import Any, ClassVar, Mapping

import yaml
from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource

KINDS = ("request", "request-list", "query", "binding", "transform", "data-reference", "response", "error")


class QueryError(ValueError):
    """A protocol validation error with a stable code and JSON Pointer path."""

    def __init__(self, code: str, path: str, message: str, context: Mapping[str, Any] | None = None):
        self.code = code
        self.path = path
        self.message = message
        self.context = dict(context or {})
        super().__init__(f"{code} at {path or '/'}: {message}")

    def to_dict(self) -> dict[str, Any]:
        return {"api_version": "cedarkit.error/v1", "code": self.code, "path": self.path,
                "message": self.message, "context": dict(self.context)}


def _pointer(parts: Any) -> str:
    return "".join("/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts)


def _plain(value: Any, path: str = "", active: set[int] | None = None) -> Any:
    """Copy Python input to the JSON value domain before schema validation."""
    if active is None:
        active = set()
    if isinstance(value, ProtocolValue):
        value = value.to_dict()
    if value is None:
        raise QueryError("invalid_query", path, "null is not allowed")
    if isinstance(value, str):
        if not value and path not in ("/path", "/error/path"):
            raise QueryError("invalid_query", path, "empty strings are not allowed")
        return value
    if isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise QueryError("invalid_query", path, "non-finite numbers are not allowed")
        return value
    if isinstance(value, (Mapping, list, tuple)):
        identity = id(value)
        if identity in active:
            raise QueryError("invalid_query", path, "cyclic values are not allowed")
        active.add(identity)
        try:
            if isinstance(value, Mapping):
                result = {}
                for key, item in value.items():
                    if not isinstance(key, str) or not key:
                        raise QueryError("invalid_query", path, "object keys must be non-empty strings")
                    result[key] = _plain(item, path + "/" + key.replace("~", "~0").replace("/", "~1"), active)
                return result
            return [_plain(item, f"{path}/{index}", active) for index, item in enumerate(value)]
        finally:
            active.remove(identity)
    raise QueryError("invalid_query", path, f"unsupported Python value: {type(value).__name__}")


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def get_schema(kind: str) -> dict[str, Any]:
    """Return a copy of a packaged JSON Schema Draft 2020-12 resource."""
    if kind not in KINDS:
        raise ValueError(f"unknown protocol kind: {kind}")
    resource = files("cedarkit_query").joinpath("schemas", f"{kind}-v1.schema.json")
    return json.loads(resource.read_text(encoding="utf-8"))


@lru_cache(maxsize=len(KINDS))
def _validator(kind: str) -> Draft202012Validator:
    # The eight versioned resources form a closed graph of $refs.
    schemas = {name: get_schema(name) for name in KINDS}
    registry = Registry().with_resources(
        (contents["$id"], Resource.from_contents(contents)) for contents in schemas.values()
    )
    return Draft202012Validator(schemas[kind], registry=registry, format_checker=FormatChecker())


def validate_value(value: Any, kind: str) -> dict[str, Any]:
    """Validate a value and return an independent plain Python copy."""
    if kind not in KINDS:
        raise ValueError(f"unknown protocol kind: {kind}")
    plain = _plain(value)
    errors = sorted(_validator(kind).iter_errors(plain), key=lambda error: (_pointer(error.absolute_path), error.message))
    if errors:
        error = errors[0]
        path = _pointer(error.absolute_path)
        if error.validator == "required":
            missing = next(iter(error.validator_value)) if len(error.validator_value) == 1 else None
            if missing:
                path += "/" + missing.replace("~", "~0").replace("/", "~1")
        code = "invalid_version" if path.endswith("/api_version") else "invalid_query"
        raise QueryError(code, path, error.message)
    return plain


@dataclass(frozen=True, init=False)
class ProtocolValue:
    """Immutable protocol value; nested maps and arrays are immutable too."""

    _value: Mapping[str, Any]
    KIND: ClassVar[str] = ""

    def __init__(self, value: Mapping[str, Any]):
        plain = validate_value(value, self.KIND)
        object.__setattr__(self, "_value", _freeze(plain))

    def to_dict(self) -> dict[str, Any]:
        return _thaw(self._value)

    def __getattr__(self, name: str) -> Any:
        if name in self._value:
            value = self._value[name]
            if self.KIND == "request" and name == "query":
                return Query(value)
            if self.KIND == "request" and name == "transforms":
                return Transform(value)
            if self.KIND == "request-list" and name == "requests":
                return tuple(Request(item) for item in value)
            return value
        raise AttributeError(name)


class Query(ProtocolValue):
    KIND = "query"


class Binding(ProtocolValue):
    KIND = "binding"


class Transform(ProtocolValue):
    KIND = "transform"


class DataReference(ProtocolValue):
    KIND = "data-reference"


class Request(ProtocolValue):
    KIND = "request"


class RequestList(ProtocolValue):
    KIND = "request-list"


class Response(ProtocolValue):
    KIND = "response"


class ErrorValue(ProtocolValue):
    KIND = "error"


MODELS = {model.KIND: model for model in
          (Request, RequestList, Query, Binding, Transform, DataReference, Response, ErrorValue)}


def _unique_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise QueryError("invalid_query", "", f"duplicate JSON key: {key}")
        result[key] = value
    return result


class _UniqueLoader(yaml.SafeLoader):
    pass


# Protocol times are strings. SafeLoader otherwise converts unquoted YAML
# timestamps to Python datetime objects before schema validation.
_UniqueLoader.yaml_implicit_resolvers = {
    initial: [(tag, pattern) for tag, pattern in resolvers
              if tag != "tag:yaml.org,2002:timestamp"]
    for initial, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


def _yaml_mapping(loader: _UniqueLoader, node: yaml.MappingNode) -> dict[str, Any]:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=True)
        if not isinstance(key, str) or not key:
            raise QueryError("invalid_query", "", "YAML object keys must be non-empty strings")
        if key in result:
            raise QueryError("invalid_query", "", f"duplicate YAML key: {key}")
        result[key] = loader.construct_object(value_node, deep=True)
    return result


_UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _yaml_mapping)


def load_value(value: Mapping[str, Any] | str | ProtocolValue, kind: str, *, format: str | None = None) -> ProtocolValue:
    """Load a protocol value from Python, JSON, or safe YAML."""
    if kind not in MODELS:
        raise ValueError(f"unknown protocol kind: {kind}")
    if isinstance(value, str):
        value = _parse_text(value, format)
    elif format is not None:
        raise ValueError("format is only valid for text input")
    return MODELS[kind](value)


def load_request(value: Mapping[str, Any] | str | Request | RequestList, *, format: str | None = None) -> Request | RequestList:
    """Load a Request or RequestList through the same strict value path."""
    if isinstance(value, (Request, RequestList)):
        return value
    if isinstance(value, str):
        # Parsing stays shared with load_value; inspect the plain parsed version
        # once and then validate the selected envelope.
        parsed = _parse_text(value, format)
    else:
        if format is not None:
            raise ValueError("format is only valid for text input")
        parsed = _plain(value)
    if not isinstance(parsed, Mapping):
        raise QueryError("invalid_query", "", "request must be an object")
    version = parsed.get("api_version")
    kind = "request-list" if (
        isinstance(version, str) and version.startswith("cedarkit.request-list/")
    ) or (version is None and "requests" in parsed) else "request"
    return MODELS[kind](parsed)


def _parse_text(value: str, format: str | None) -> Any:
    selected = format or ("json" if value.lstrip().startswith(("{", "[")) else "yaml")
    try:
        if selected == "json":
            return json.loads(value, object_pairs_hook=_unique_pairs,
                              parse_constant=lambda token: (_ for _ in ()).throw(
                                  QueryError("invalid_query", "", f"non-finite JSON number: {token}")))
        if selected == "yaml":
            return yaml.load(value, Loader=_UniqueLoader)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        raise QueryError("invalid_query", "", f"invalid {selected} input: {exc}") from exc
    raise ValueError(f"unknown input format: {selected}")


def dump_value(value: ProtocolValue, *, format: str = "json") -> str:
    """Serialize a validated value without Python runtime objects."""
    if not isinstance(value, ProtocolValue):
        raise TypeError("dump_value requires a protocol value")
    plain = value.to_dict()
    if format == "json":
        return json.dumps(plain, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))
    if format == "yaml":
        return yaml.safe_dump(plain, allow_unicode=True, sort_keys=True)
    raise ValueError(f"unknown output format: {format}")
