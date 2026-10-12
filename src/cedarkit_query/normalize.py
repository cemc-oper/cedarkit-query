"""Pure normalization, bounded expansion, and independent protocol identities."""

from __future__ import annotations

import hashlib
import itertools
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any, Mapping

from .model import Binding, DataReference, Query, QueryError, Request, RequestList, Transform, load_request

HARD_EXPANSION_LIMIT = 4096
_DURATION = re.compile(r"P(?:(?P<days>[0-9]+)D)?(?:T(?:(?P<hours>[0-9]+)H)?(?:(?P<minutes>[0-9]+)M)?(?:(?P<seconds>[0-9]+)S)?)?\Z")
_UNIT = {"isobaric": {"Pa": Decimal(1), "hPa": Decimal(100)},
         "heightAboveGround": {"m": Decimal(1), "km": Decimal(1000)},
         "soilLayer": {"m": Decimal(1), "km": Decimal(1000)},
         "modelLevel": {"1": Decimal(1)}}
_CANONICAL_UNIT = {"isobaric": "Pa", "heightAboveGround": "m", "soilLayer": "m", "modelLevel": "1"}


@dataclass(frozen=True)
class AliasSnapshot:
    """Explicit namespace/name aliases; no process-global catalog is consulted."""

    version: str
    aliases: Mapping[str, Mapping[str, str | tuple[str, ...]]]

    def __post_init__(self) -> None:
        if not isinstance(self.version, str) or not self.version:
            raise ValueError("snapshot version must be a non-empty string")
        if not isinstance(self.aliases, Mapping):
            raise ValueError("snapshot aliases must be a mapping")
        copied: dict[str, Mapping[str, str | tuple[str, ...]]] = {}
        for namespace, names in self.aliases.items():
            if not isinstance(namespace, str) or not namespace or not isinstance(names, Mapping):
                raise ValueError("snapshot aliases must be namespace -> name -> stable ID")
            entries = {}
            for name, targets in names.items():
                if not isinstance(name, str) or not name:
                    raise ValueError("alias name must be non-empty")
                if isinstance(targets, str):
                    if not targets:
                        raise ValueError("stable ID must be non-empty")
                    entries[name] = targets
                elif isinstance(targets, (list, tuple)) and targets and all(
                    isinstance(target, str) and target for target in targets
                ):
                    entries[name] = tuple(targets)
                else:
                    raise ValueError("alias targets must be a stable ID or non-empty ID list")
            copied[namespace] = MappingProxyType(entries)
        object.__setattr__(self, "aliases", MappingProxyType(copied))


@dataclass(frozen=True)
class NormalizedRequest:
    raw_request: Request | RequestList
    normalized_request: Request | RequestList
    catalog_version: str | None
    vocabulary_version: str | None


@dataclass(frozen=True)
class ExpandedRequest(NormalizedRequest):
    expanded_requests: tuple[Request, ...]


def _snapshot(value: AliasSnapshot | Mapping[str, Any] | None, name: str) -> AliasSnapshot | None:
    if value is None:
        return None
    if isinstance(value, AliasSnapshot):
        return value
    if isinstance(value, Mapping) and set(value) == {"version", "aliases"}:
        try:
            return AliasSnapshot(value["version"], value["aliases"])
        except (TypeError, ValueError) as exc:
            raise QueryError("invalid_query", f"/{name}", str(exc)) from exc
    raise QueryError("invalid_query", f"/{name}", "snapshot requires version and aliases")


def _alias(value: str | Mapping[str, str], snapshot: AliasSnapshot | None, path: str, kind: str) -> str:
    if isinstance(value, str):
        return value
    namespace, name = value["namespace"], value["name"]
    matches = None if snapshot is None else snapshot.aliases.get(namespace, {}).get(name)
    if matches is None:
        code = "unknown_dataset" if kind == "dataset" else "unknown_parameter"
        raise QueryError(code, path, f"alias {namespace}/{name} is absent from the supplied snapshot")
    if isinstance(matches, str):
        return matches
    candidates = sorted(set(matches))
    if len(candidates) != 1:
        raise QueryError("ambiguous_mapping", path, f"alias {namespace}/{name} has multiple stable IDs")
    return candidates[0]


def _time(value: str, path: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None or parsed.microsecond:
            raise ValueError("time requires a timezone and whole-second precision")
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (ValueError, OverflowError) as exc:
        raise QueryError("invalid_query", path, "invalid whole-second timezone-aware time") from exc


def _duration_seconds(value: str, path: str, *, positive: bool = False) -> int:
    match = _DURATION.fullmatch(value)
    if match is None or not any(match.groupdict().values()):
        raise QueryError("invalid_query", path, "duration requires day/hour/minute/second components")
    if "T" in value and not any(match.group(name) is not None for name in ("hours", "minutes", "seconds")):
        raise QueryError("invalid_query", path, "time designator requires a time component")
    parts = {key: int(item or 0) for key, item in match.groupdict().items()}
    seconds = parts["days"] * 86400 + parts["hours"] * 3600 + parts["minutes"] * 60 + parts["seconds"]
    if positive and seconds == 0:
        raise QueryError("invalid_query", path, "duration step/window must be positive")
    return seconds


def _duration(value: str, path: str, *, positive: bool = False) -> str:
    return f"PT{_duration_seconds(value, path, positive=positive)}S"


def _decimal(value: int | float, path: str) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite():
            raise InvalidOperation
        return result
    except (InvalidOperation, ValueError) as exc:
        raise QueryError("invalid_query", path, "finite decimal number required") from exc


def _json_number(value: Decimal) -> int | float:
    if value == value.to_integral_value():
        return int(value)
    try:
        result = float(value)
    except OverflowError as exc:
        raise QueryError("invalid_query", "", "number exceeds JSON floating-point range") from exc
    if result == float("inf") or result == float("-inf"):
        raise QueryError("invalid_query", "", "number exceeds JSON floating-point range")
    return result


def _range_values(value: Mapping[str, Any], kind: str, path: str) -> list[Any]:
    start, stop, step = value["start"], value["stop"], value["step"]
    closed = value["closed"]
    if kind == "time":
        first = datetime.fromisoformat(_time(start, f"{path}/start").replace("Z", "+00:00"))
        last = datetime.fromisoformat(_time(stop, f"{path}/stop").replace("Z", "+00:00"))
        stride = _duration_seconds(step, f"{path}/step", positive=True)
        distance = int((last - first).total_seconds())
        convert = lambda n: (first + timedelta(seconds=n * stride)).strftime("%Y-%m-%dT%H:%M:%SZ")
    elif kind == "duration":
        first = _duration_seconds(start, f"{path}/start")
        last = _duration_seconds(stop, f"{path}/stop")
        stride = _duration_seconds(step, f"{path}/step", positive=True)
        distance = last - first
        convert = lambda n: f"PT{first + n * stride}S"
    else:
        first = _decimal(start, f"{path}/start")
        last = _decimal(stop, f"{path}/stop")
        stride = _decimal(step, f"{path}/step")
        if stride <= 0:
            raise QueryError("invalid_query", f"{path}/step", "range step must be positive")
        distance = last - first
        convert = lambda n: first + n * stride
    if distance < 0:
        raise QueryError("invalid_query", path, "range start exceeds stop")
    count = int(distance // stride) + 1
    left = 0 if closed in ("both", "left") else 1
    right = count - (1 if closed in ("left", "neither") and distance % stride == 0 else 0)
    size = max(0, right - left)
    if size == 0:
        raise QueryError("invalid_query", path, "range selects no values")
    if size > HARD_EXPANSION_LIMIT:
        raise QueryError("expansion_limit", path, "range exceeds the hard expansion limit")
    try:
        return [convert(index) for index in range(left, right)]
    except (OverflowError, ValueError) as exc:
        raise QueryError("invalid_query", path, "range exceeds supported time bounds") from exc


def _selection(value: Any, kind: str, path: str) -> Any:
    if isinstance(value, Mapping) and "start" in value:
        values = _range_values(value, kind, path)
    elif isinstance(value, (list, tuple)):
        values = list(value)
    else:
        values = [value]
    if kind == "time":
        canonical = [_time(item, f"{path}/{index}") for index, item in enumerate(values)]
    elif kind == "duration":
        canonical = [_duration(item, f"{path}/{index}") for index, item in enumerate(values)]
    else:
        canonical = values
    ordered = sorted(set(canonical), key=(lambda item: int(item[2:-1])) if kind == "duration" else None)
    return ordered[0] if len(ordered) == 1 else ordered


def _vertical(value: Mapping[str, Any], path: str) -> dict[str, Any]:
    kind = value["type"]
    if kind == "surface":
        return {"type": "surface"}
    unit = value["unit"]
    factor = _UNIT.get(kind, {}).get(unit)
    if factor is None:
        raise QueryError("invalid_query", f"{path}/unit", f"unit {unit!r} is not defined for {kind}")
    result: dict[str, Any] = {"type": kind, "unit": _CANONICAL_UNIT[kind]}
    if "values" in value:
        source = value["values"]
        values = _range_values(source, "number", f"{path}/values") if isinstance(source, Mapping) else (
            list(source) if isinstance(source, (list, tuple)) else [source]
        )
        ordered = sorted({_decimal(item, f"{path}/values") * factor for item in values})
        result["values"] = _json_number(ordered[0]) if len(ordered) == 1 else [_json_number(item) for item in ordered]
    else:
        bounds = []
        for index, item in enumerate(value["bounds"]):
            lower = _decimal(item["lower"], f"{path}/bounds/{index}/lower") * factor
            upper = _decimal(item["upper"], f"{path}/bounds/{index}/upper") * factor
            if lower >= upper:
                raise QueryError("invalid_query", f"{path}/bounds/{index}", "bound lower must be less than upper")
            bounds.append((lower, upper))
        result["bounds"] = [{"lower": _json_number(low), "upper": _json_number(high)}
                            for low, high in sorted(set(bounds))]
    return result


def _bbox(value: list[int | float], path: str) -> list[int | float]:
    west, south, east, north = (_decimal(item, f"{path}/{index}") for index, item in enumerate(value))
    if not west < east or not south < north:
        raise QueryError("invalid_query", path, "bbox requires west < east and south < north")
    return [_json_number(item) for item in (west, south, east, north)]


def _normalize_query(value: Query, catalog: AliasSnapshot | None, vocabulary: AliasSnapshot | None) -> Query:
    source = value.to_dict()
    source["dataset"] = _alias(source["dataset"], catalog, "/query/dataset", "dataset")
    if "parameter" in source:
        parameters = source["parameter"] if isinstance(source["parameter"], list) else [source["parameter"]]
        resolved = sorted({_alias(item, vocabulary, "/query/parameter", "parameter") for item in parameters})
        source["parameter"] = resolved[0] if len(resolved) == 1 else resolved
    for name, kind in (("reference_time", "time"), ("lead_time", "duration")):
        if name in source:
            source[name] = _selection(source[name], kind, f"/query/{name}")
    if "valid_time" in source:
        valid = _time(source.pop("valid_time"), "/query/valid_time")
        reference, lead = source.get("reference_time"), source.get("lead_time")
        if not isinstance(reference, str) or not isinstance(lead, str):
            raise QueryError("invalid_query", "/query/valid_time", "valid_time requires one reference_time and one lead_time")
        try:
            expected = (datetime.fromisoformat(reference.replace("Z", "+00:00")) +
                        timedelta(seconds=_duration_seconds(lead, "/query/lead_time"))).strftime("%Y-%m-%dT%H:%M:%SZ")
        except (OverflowError, ValueError) as exc:
            raise QueryError("invalid_query", "/query/valid_time", "valid_time arithmetic exceeds supported range") from exc
        if valid != expected:
            raise QueryError("invalid_query", "/query/valid_time", "valid_time conflicts with reference_time + lead_time")
    if "vertical" in source:
        source["vertical"] = _vertical(source["vertical"], "/query/vertical")
    if "statistic" in source and "window" in source["statistic"]:
        source["statistic"]["window"] = _duration(source["statistic"]["window"], "/query/statistic/window", positive=True)
    if "member" in source:
        members = source["member"] if isinstance(source["member"], list) else [source["member"]]
        ordered = sorted({(item["role"], item["id"]) for item in members})
        source["member"] = {"role": ordered[0][0], "id": ordered[0][1]} if len(ordered) == 1 else [
            {"role": role, "id": identity} for role, identity in ordered]
    if "coverage" in source:
        source["coverage"]["bbox"] = _bbox(source["coverage"]["bbox"], "/query/coverage/bbox")
    return Query(source)


def _normalize_request(value: Request, catalog: AliasSnapshot | None, vocabulary: AliasSnapshot | None) -> Request:
    source = value.to_dict()
    source["query"] = _normalize_query(value.query, catalog, vocabulary).to_dict()
    source["policy"] = {"cardinality": "one", "missing": "error", "duplicates": "error"} | source.get("policy", {})
    if "transforms" in source:
        for index, operation in enumerate(source["transforms"]["operations"]):
            if operation["kind"] == "crop":
                operation["bbox"] = _bbox(operation["bbox"], f"/transforms/operations/{index}/bbox")
    return Request(source)


def normalize_request(
    value: Request | RequestList | Mapping[str, Any] | str,
    *,
    catalog_snapshot: AliasSnapshot | Mapping[str, Any] | None = None,
    vocabulary_snapshot: AliasSnapshot | Mapping[str, Any] | None = None,
) -> NormalizedRequest:
    """Keep both input and canonical requests, with explicit snapshot versions."""
    raw = load_request(value)
    catalog = _snapshot(catalog_snapshot, "catalog_snapshot")
    vocabulary = _snapshot(vocabulary_snapshot, "vocabulary_snapshot")
    if isinstance(raw, RequestList):
        normalized = RequestList({"api_version": "cedarkit.request-list/v1",
                                  "requests": [_normalize_request(item, catalog, vocabulary).to_dict()
                                               for item in raw.requests]})
    else:
        normalized = _normalize_request(raw, catalog, vocabulary)
    return NormalizedRequest(raw, normalized,
                             catalog.version if catalog else None,
                             vocabulary.version if vocabulary else None)


def _alternatives(value: Any) -> list[Any]:
    return value if isinstance(value, list) else [value]


def _expanded_one(request: Request, remaining: int) -> tuple[Request, ...]:
    source = request.to_dict()
    query = source["query"]
    dimensions: list[tuple[str, list[Any]]] = []
    for name in ("parameter", "reference_time", "lead_time", "member"):
        if name in query:
            dimensions.append((name, _alternatives(query[name])))
    if "vertical" in query and "values" in query["vertical"]:
        dimensions.append(("vertical.values", _alternatives(query["vertical"]["values"])))
    elif "vertical" in query and "bounds" in query["vertical"]:
        dimensions.append(("vertical.bounds", query["vertical"]["bounds"]))
    count = 1
    for _, values in dimensions:
        count *= len(values)
        if count > remaining:
            raise QueryError("expansion_limit", "/query", "request exceeds expansion limit before data access")
    if source["policy"]["cardinality"] == "zero_or_one" and count != 1:
        raise QueryError("invalid_query", "/policy/cardinality", "zero_or_one requires one expanded combination")
    expanded = []
    for selection in itertools.product(*(choices for _, choices in dimensions)):
        item = json.loads(json.dumps(source))
        for (name, _), choice in zip(dimensions, selection):
            if name.startswith("vertical."):
                item["query"]["vertical"][name.split(".", 1)[1]] = [choice] if name.endswith("bounds") else choice
            else:
                item["query"][name] = choice
        expanded.append(Request(item))
    return tuple(expanded)


def expand_request(
    value: NormalizedRequest | Request | RequestList | Mapping[str, Any] | str,
    *,
    catalog_snapshot: AliasSnapshot | Mapping[str, Any] | None = None,
    vocabulary_snapshot: AliasSnapshot | Mapping[str, Any] | None = None,
) -> ExpandedRequest:
    """Expand only after bounding each Cartesian product and the total list."""
    prepared = value if isinstance(value, NormalizedRequest) else normalize_request(
        value, catalog_snapshot=catalog_snapshot, vocabulary_snapshot=vocabulary_snapshot)
    normalized = prepared.normalized_request
    items = normalized.requests if isinstance(normalized, RequestList) else (normalized,)
    result: list[Request] = []
    for request in items:
        policy_limit = request.to_dict().get("options", {}).get("max_expansion", HARD_EXPANSION_LIMIT)
        limit = min(policy_limit, HARD_EXPANSION_LIMIT - len(result))
        result.extend(_expanded_one(request, limit))
    return ExpandedRequest(prepared.raw_request, normalized, prepared.catalog_version,
                           prepared.vocabulary_version, tuple(result))


def _digest(value: Mapping[str, Any]) -> str:
    serialized = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
    return "sha256:" + hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def query_identity(
    value: Query | Request | NormalizedRequest | Mapping[str, Any],
    *,
    catalog_snapshot: AliasSnapshot | Mapping[str, Any] | None = None,
    vocabulary_snapshot: AliasSnapshot | Mapping[str, Any] | None = None,
) -> str:
    """Hash the canonical logical Query, excluding action, transforms, and source."""
    if isinstance(value, NormalizedRequest):
        normalized = value.normalized_request
        if not isinstance(normalized, Request):
            raise TypeError("query_identity needs one Query or Request")
        query = normalized.query
    elif isinstance(value, Mapping) and value.get("api_version") == "cedarkit.query/v1":
        query = _normalize_query(Query(value), _snapshot(catalog_snapshot, "catalog_snapshot"),
                                 _snapshot(vocabulary_snapshot, "vocabulary_snapshot"))
    elif isinstance(value, (Request, Mapping)):
        normalized = normalize_request(value, catalog_snapshot=catalog_snapshot,
                                       vocabulary_snapshot=vocabulary_snapshot).normalized_request
        if not isinstance(normalized, Request):
            raise TypeError("query_identity needs one Query or Request")
        query = normalized.query
    elif isinstance(value, Query):
        query = _normalize_query(value, _snapshot(catalog_snapshot, "catalog_snapshot"),
                                 _snapshot(vocabulary_snapshot, "vocabulary_snapshot"))
    else:
        raise TypeError("query_identity needs one Query or Request")
    return _digest(query.to_dict())


def transform_identity(value: Transform) -> str:
    source = value.to_dict()
    for index, operation in enumerate(source["operations"]):
        if operation["kind"] == "crop":
            operation["bbox"] = _bbox(operation["bbox"], f"/operations/{index}/bbox")
    return _digest(source)


def binding_identity(value: Binding) -> str:
    return _digest(value.to_dict())


def resource_identity(value: DataReference) -> str:
    source = value.to_dict()
    if source["resource"]["kind"] == "cmadaas" and "version" not in source["resource"]:
        raise QueryError("availability_unknown", "/resource/version",
                         "remote resource has no version for a stable resource identity")
    return _digest({"binding_id": source["binding_id"], "resource": source["resource"]})
