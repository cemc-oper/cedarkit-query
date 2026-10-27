"""Immutable declaration values and the single Python/JSON/YAML validation path."""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import ClassVar
from yaml import YAMLError
import os

from jsonschema import Draft202012Validator, validators
from referencing import Registry, Resource

from ._json import freeze, parse, plain, pointer, serialize, thaw
from .errors import QueryError

KINDS = (
    "common",
    "query",
    "context",
    "request",
    "report",
    "explanation",
    "error",
    "parameters",
    "mappings",
    "catalog",
)
VERSIONS = {
    "request": "cedarkit.request/v1",
    "error": "cedarkit.error/v1",
    "parameters": "cedarkit.parameters/v1",
    "mappings": "cedarkit.mappings/v1",
    "catalog": "cedarkit.catalog/v1",
}
StrictValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine(
        "integer", lambda checker, value: type(value) is int
    ),
)


def get_schema(kind):
    if kind not in KINDS:
        raise ValueError(f"unknown schema kind: {kind}")
    import json

    return json.loads(
        files("cedarkit_query")
        .joinpath("schemas", f"{kind}-v1.schema.json")
        .read_text()
    )


@lru_cache(maxsize=len(KINDS))
def _validator(kind):
    schemas = [get_schema(k) for k in KINDS]
    registry = Registry().with_resources(
        (s["$id"], Resource.from_contents(s)) for s in schemas
    )
    return StrictValidator(get_schema(kind), registry=registry)


def _best(error):
    """Prefer the matching discriminator/type branch of a oneOf diagnostic."""
    if not error.context:
        return error
    groups = {}
    for child in error.context:
        groups.setdefault(child.schema_path[0], []).append(child)

    def rank(children):
        return (sum(e.validator in {"const", "type"} for e in children), len(children))

    children = min(groups.values(), key=rank)
    return min(
        (_best(c) for c in children), key=lambda e: (-len(e.absolute_path), e.message)
    )


def validate_value(value, kind):
    """Structure plus shared semantic validation; never loads referenced resources."""
    if kind not in KINDS or kind == "common":
        raise ValueError(f"not a value kind: {kind}")
    code = (
        "invalid_query"
        if kind == "query"
        else "invalid_request"
        if kind == "request"
        else "invalid_config"
    )
    try:
        data = plain(value)
    except ValueError as exc:
        location, _, message = str(exc).partition(": ")
        raise QueryError(
            code, location if location.startswith("/") else "", message or str(exc)
        ) from exc
    if isinstance(data, dict):
        if (
            kind in VERSIONS
            and "api_version" in data
            and isinstance(data["api_version"], str)
            and data["api_version"] != VERSIONS[kind]
        ):
            raise QueryError(
                "unsupported_version", "/api_version", "unsupported format version"
            )
        if (
            kind == "report"
            and "schema_version" in data
            and type(data["schema_version"]) is int
            and data["schema_version"] != 1
        ):
            raise QueryError(
                "unsupported_version", "/schema_version", "unsupported report version"
            )
    if kind == "mappings":
        from .resources import preflight_rules

        preflight_rules(data)
    error = next(_validator(kind).iter_errors(data), None)
    if error is not None:
        error = _best(error)
        parts = list(error.absolute_path)
        if error.validator == "required" and isinstance(error.instance, dict):
            parts.append(
                next(k for k in error.validator_value if k not in error.instance)
            )
        elif error.validator == "additionalProperties" and isinstance(
            error.instance, dict
        ):
            extras = set(error.instance) - set(error.schema.get("properties", {}))
            if extras:
                parts.append(sorted(extras)[0])
        if kind == "request" and parts:
            if parts[0] == "queries" and len(parts) > 1:
                code = "invalid_query"
            elif parts[0] in {"source", "context"}:
                code = "invalid_config"
        raise QueryError(code, pointer(parts), error.message)
    from .validation import validate_semantics

    validate_semantics(data, kind)
    return data


def validate_fragment(value, name, *, path="", code="invalid_config"):
    """Validate a shared schema fragment without resolving business references."""
    try:
        data = plain(value)
    except ValueError as exc:
        raise QueryError(code, path, str(exc)) from exc
    validator = _validator("context").evolve(
        schema={"$ref": "urn:cedarkit:query:common:v1#/$defs/" + name}
    )
    error = next(validator.iter_errors(data), None)
    if error is not None:
        error = _best(error)
        raise QueryError(code, path + pointer(error.absolute_path), error.message)
    return data


class ProtocolValue:
    _value: Mapping
    KIND: ClassVar[str] = ""

    def __setattr__(self, name, value):
        raise AttributeError("protocol values are immutable")

    def __delattr__(self, name):
        raise AttributeError("protocol values are immutable")

    def __eq__(self, other):
        return type(self) is type(other) and self._value == other._value

    __hash__ = None

    def __init__(self, value):
        object.__setattr__(self, "_value", freeze(validate_value(value, self.KIND)))

    def to_dict(self):
        return thaw(self._value)

    def __getattr__(self, name):
        if name in self._value:
            return self._value[name]
        raise AttributeError(name)


_UNSET = object()


class Query(ProtocolValue):
    KIND = "query"

    def __init__(self, value=_UNSET, **conditions):
        if value is not _UNSET and conditions:
            raise QueryError(
                "invalid_query",
                "",
                "use a Query mapping or keyword conditions, not both",
            )
        super().__init__(conditions if value is _UNSET else value)


class Request(ProtocolValue):
    KIND = "request"

    def __init__(self, value, *, base_dir=None):
        data = validate_value(value, "request")
        base = Path.cwd() if base_dir is None else Path(base_dir).absolute()

        def absolute(path):
            return os.path.abspath(base / path)

        source = data["source"]
        for key in ("path", "directory", "root", "config"):
            if key in source:
                source[key] = (
                    [absolute(p) for p in source[key]]
                    if isinstance(source[key], list)
                    else absolute(source[key])
                )
        context = data.get("context")
        if isinstance(context, str):
            data["context"] = absolute(context)
        elif isinstance(context, dict):
            data["context"] = Context.from_dict(context, base_dir=base).to_dict()
        object.__setattr__(self, "_value", freeze(data))

    @classmethod
    def from_dict(cls, data, *, base_dir=None):
        return cls(data, base_dir=base_dir)

    @classmethod
    def load(cls, path):
        path = Path(path).absolute()
        try:
            data = parse(path.read_text(encoding="utf-8"), _format(path))
        except (OSError, ValueError, YAMLError) as exc:
            raise QueryError(
                "invalid_request", "", "cannot load request", {"document": str(path)}
            ) from exc
        return cls.from_dict(data, base_dir=path.parent)

    @property
    def queries(self):
        return tuple(Query(q) for q in self._value["queries"])

    @property
    def context(self):
        return self._value.get("context")

    @property
    def completeness(self):
        return self._value.get("completeness")

    def dump(self, path):
        path = Path(path)
        path.write_text(serialize(self, _format(path)), encoding="utf-8")


class QueryReport(ProtocolValue):
    KIND = "report"

    def to_json(self):
        return serialize(self)

    @classmethod
    def from_json(cls, text):
        return load_value(text, "report", format="json")


class QueryExplanation(ProtocolValue):
    KIND = "explanation"


class ErrorValue(ProtocolValue):
    KIND = "error"


class Parameters(ProtocolValue):
    KIND = "parameters"


class Mappings(ProtocolValue):
    KIND = "mappings"


class Catalog(ProtocolValue):
    KIND = "catalog"


class Context(ProtocolValue):
    KIND = "context"

    def __init__(self, value, *, base_dir=None):
        data = validate_value(value, "context")
        base = Path.cwd() if base_dir is None else Path(base_dir).absolute()
        refs = [data.get(key) for key in ("parameter_registry", "catalog")]
        refs.extend(data.get("mappings", {}).values())
        for ref in refs:
            if ref and "path" in ref:
                ref["path"] = os.path.abspath(base / ref["path"])
        object.__setattr__(self, "_value", freeze(data))

    @classmethod
    def from_dict(cls, data, *, base_dir=None):
        return cls(data, base_dir=base_dir)


MODELS = {
    model.KIND: model
    for model in (
        Query,
        Request,
        QueryReport,
        QueryExplanation,
        ErrorValue,
        Parameters,
        Mappings,
        Catalog,
        Context,
    )
}


def _format(path):
    suffix = path.suffix.lower()
    if suffix not in {".yaml", ".yml", ".json"}:
        raise ValueError("expected .yaml/.yml/.json")
    return "json" if suffix == ".json" else "yaml"


def load_value(value, kind, *, format=None, base_dir=None):
    if isinstance(value, str):
        try:
            value = parse(value, format)
        except (ValueError, YAMLError) as exc:
            raise QueryError(
                "invalid_request"
                if kind == "request"
                else "invalid_query"
                if kind == "query"
                else "invalid_config",
                "",
                str(exc),
            ) from exc
    elif format is not None:
        raise ValueError("format is only valid for text input")
    return (
        MODELS[kind](value, base_dir=base_dir)
        if kind in {"request", "context"}
        else MODELS[kind](value)
    )


def load_request(value, *, format=None, base_dir=None):
    return load_value(value, "request", format=format, base_dir=base_dir)


def dump_value(value, *, format="json"):
    if not isinstance(value, ProtocolValue):
        raise TypeError("dump_value requires a validated value")
    return serialize(value, format)
