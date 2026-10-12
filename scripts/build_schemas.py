"""Write the versioned protocol schemas from their compact declarative source."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "src" / "cedarkit_query" / "schemas"
BASE = "urn:cedarkit:query:"


def object_schema(properties, required=(), **extra):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
        **extra,
    }


def string(pattern=None):
    result = {"type": "string", "minLength": 1}
    if pattern:
        result["pattern"] = pattern
    return result


def enum(*values):
    return {"enum": list(values)}


def ref(kind):
    return {"$ref": f"{BASE}{kind}:v1"}


def schema(kind, properties, required, *, definitions=None, rules=None):
    result = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{BASE}{kind}:v1",
        "title": f"CedarKit {kind} v1",
        **object_schema(properties, required),
    }
    if definitions:
        result["$defs"] = definitions
    if rules:
        result["allOf"] = rules
    return result


time = {"type": "string", "format": "date-time", "pattern": r"(?:Z|[+-][0-9]{2}:[0-9]{2})$"}
duration = {
    "type": "string",
    "pattern": r"^P(?:[0-9]+D)?(?:T(?:[0-9]+H)?(?:[0-9]+M)?(?:[0-9]+S)?)?$",
    "not": {"enum": ["P", "PT"]},
}
positive_duration = {"allOf": [duration, {"not": {"pattern": r"^P(?:0+D)?(?:T(?:0+H)?(?:0+M)?(?:0+S)?)?$"}}]}
alias = object_schema({"namespace": string(), "name": string()}, ("namespace", "name"))
parameter_atom = {"oneOf": [string(), alias]}
dataset = {"oneOf": [string(), alias]}
closed = enum("both", "left", "right", "neither")


def selector(atom, step):
    return {
        "oneOf": [
            atom,
            {"type": "array", "items": atom, "minItems": 1},
            object_schema(
                {"start": atom, "stop": atom, "step": step, "closed": closed},
                ("start", "stop", "step", "closed"),
            ),
        ]
    }


number = {"type": "number"}
bbox = {"type": "array", "prefixItems": [
    {"type": "number", "minimum": -180, "maximum": 180},
    {"type": "number", "minimum": -90, "maximum": 90},
    {"type": "number", "minimum": -180, "maximum": 180},
    {"type": "number", "minimum": -90, "maximum": 90},
], "minItems": 4, "maxItems": 4}
vertical_value = {"oneOf": [number, {"type": "array", "items": number, "minItems": 1},
                            object_schema({"start": number, "stop": number, "step": number, "closed": closed},
                                          ("start", "stop", "step", "closed"))]}
vertical = object_schema(
    {
        "type": enum("surface", "isobaric", "heightAboveGround", "soilLayer", "modelLevel"),
        "unit": string(),
        "values": vertical_value,
        "bounds": {"type": "array", "items": object_schema({"lower": number, "upper": number}, ("lower", "upper")), "minItems": 1},
    },
    ("type",),
    allOf=[
        {"if": {"properties": {"type": {"const": "surface"}}},
         "then": {"not": {"anyOf": [{"required": [key]} for key in ("unit", "values", "bounds")]}}},
        {"if": {"properties": {"type": {"not": {"const": "surface"}}}},
         "then": {"required": ["unit"], "oneOf": [{"required": ["values"], "not": {"required": ["bounds"]}},
                                                         {"required": ["bounds"], "not": {"required": ["values"]}}]}},
    ],
)
statistic = object_schema(
    {"kind": enum("instant", "accum", "mean", "min", "max"), "window": positive_duration},
    ("kind",),
    allOf=[{"if": {"properties": {"kind": {"const": "instant"}}}, "then": {"not": {"required": ["window"]}},
            "else": {"required": ["window"]}}],
)
member = object_schema({"id": string(), "role": enum("control", "perturbation")}, ("id", "role"))
query = schema(
    "query",
    {
        "api_version": {"const": "cedarkit.query/v1"},
        "domain": {"const": "grid"},
        "dataset": dataset,
        "parameter": {"oneOf": [parameter_atom, {"type": "array", "items": parameter_atom, "minItems": 1}]},
        "reference_time": selector(time, positive_duration),
        "lead_time": selector(duration, positive_duration),
        "valid_time": time,
        "vertical": vertical,
        "statistic": statistic,
        "member": {"oneOf": [member, {"type": "array", "items": member, "minItems": 1}]},
        "native_grid": object_schema({"id": string()}, ("id",)),
        "coverage": object_schema({"crs": {"const": "EPSG:4326"}, "bbox": bbox,
                                   "relation": {"const": "intersects"}}, ("crs", "bbox", "relation")),
    },
    ("api_version", "domain", "dataset"),
)

file_pattern = object_schema(
    {"resolver": {"const": "file-pattern"}, "root": string(), "pattern": string()},
    ("resolver", "root", "pattern"),
)
reki_local = object_schema(
    {"resolver": {"const": "reki-local"}, "data_type": string(), "config_version": string()},
    ("resolver", "data_type", "config_version"),
)
binding = schema(
    "binding",
    {"api_version": {"const": "cedarkit.binding/v1"}, "dataset": string(),
     "kind": enum("local", "cmadaas"), "source_id": string(), "catalog_version": string(),
     "locator": {"oneOf": [file_pattern, reki_local]}, "data_code": string(),
     "request_kind": {"const": "model_grid"}, "origin": string()},
    ("api_version", "dataset", "kind", "source_id", "catalog_version"),
    rules=[
        {"if": {"properties": {"kind": {"const": "local"}}},
         "then": {"required": ["locator"], "not": {"anyOf": [{"required": ["data_code"]}, {"required": ["request_kind"]}]}},
         "else": {"required": ["data_code", "request_kind"], "not": {"required": ["locator"]}}},
    ],
)

crop = object_schema({"kind": {"const": "crop"}, "bbox": bbox}, ("kind", "bbox"))
unit = object_schema({"kind": {"const": "unit"}, "target": string()}, ("kind", "target"))
transform = schema("transform", {"api_version": {"const": "cedarkit.transform/v1"},
                                 "operations": {"type": "array", "items": {"oneOf": [crop, unit]}}},
                   ("api_version", "operations"))

policy = object_schema(
    {"cardinality": enum("one", "zero_or_one", "all"), "missing": enum("error", "partial"),
     "duplicates": {"const": "error"}}, (),
    allOf=[{"if": {"properties": {"cardinality": {"const": "zero_or_one"}},
                   "required": ["cardinality"]},
            "then": {"properties": {"missing": {"const": "partial"}}, "required": ["missing"]}}],
)
output = object_schema({"format": {"const": "netcdf"}, "path": string()}, ("format", "path"))
options = object_schema({"max_expansion": {"type": "integer", "minimum": 1, "maximum": 4096},
                         "output": output})
request = schema(
    "request",
    {"api_version": {"const": "cedarkit.request/v1"},
     "action": enum("validate", "plan", "list", "locate", "read"),
     "query": ref("query"), "policy": policy, "transforms": ref("transform"), "options": options},
    ("api_version", "action", "query"),
    rules=[
        {"if": {"properties": {"action": {"enum": ["plan", "locate", "read"]}}},
         "then": {"properties": {"query": {"required": ["parameter", "reference_time", "lead_time"]}}}},
        {"if": {"properties": {"action": {"not": {"const": "read"}}}},
         "then": {"properties": {"options": {"not": {"required": ["output"]}}}}},
    ],
)
request_list = schema(
    "request-list",
    {"api_version": {"const": "cedarkit.request-list/v1"},
     "requests": {"type": "array", "items": ref("request"), "minItems": 1}},
    ("api_version", "requests"),
)

field_identity = object_schema(
    {"parameter": string(), "reference_time": time, "lead_time": duration,
     "vertical": vertical, "statistic": statistic, "member": member,
     "native_grid": object_schema({"id": string()}, ("id",))},
    ("parameter", "reference_time", "lead_time"),
)
fingerprint = object_schema(
    {"size": {"type": "integer", "minimum": 0}, "mtime_ns": {"type": "integer", "minimum": 0},
     "device": {"type": "integer", "minimum": 0}, "inode": {"type": "integer", "minimum": 0},
     "sha256": string(r"^[0-9a-f]{64}$")},
    ("size", "mtime_ns", "device", "inode"),
)
local_resource = object_schema(
    {"kind": {"const": "local"}, "locator": string(), "fingerprint": fingerprint},
    ("kind", "locator", "fingerprint"),
)
remote_resource = object_schema(
    {"kind": {"const": "cmadaas"}, "locator": string(), "version": string()},
    ("kind", "locator"),
)
field_locator = object_schema(
    {"frame_index": {"type": "integer", "minimum": 0},
     "field_index": {"type": "integer", "minimum": 0},
     "offset": {"type": "integer", "minimum": 0}},
    (), minProperties=1,
)
data_reference = schema(
    "data-reference",
    {"api_version": {"const": "cedarkit.data-reference/v1"}, "dataset": string(),
     "field_identity": field_identity, "binding_id": string(r"^sha256:[0-9a-f]{64}$"),
     "resource": {"oneOf": [local_resource, remote_resource]},
     "verification": enum("path_exists", "field_confirmed", "remote_unverified"),
     "field_locator": field_locator, "catalog_version": string(), "vocabulary_version": string()},
    ("api_version", "dataset", "field_identity", "binding_id", "resource", "verification"),
    rules=[
        {"if": {"properties": {"verification": {"const": "field_confirmed"}}},
         "then": {"required": ["field_locator"]},
         "else": {"not": {"required": ["field_locator"]}}},
        {"if": {"properties": {"resource": {"properties": {"kind": {"const": "cmadaas"}}}}},
         "then": {"properties": {"verification": {"const": "remote_unverified"}}}},
    ],
)

error_codes = ("invalid_query", "invalid_version", "unknown_dataset", "unknown_parameter",
               "unmapped_parameter", "ambiguous_mapping", "unsupported", "expansion_limit",
               "not_found", "multiple_matches", "availability_unknown", "permission_denied",
               "provider_error", "stale_reference")
error = schema(
    "error",
    {"api_version": {"const": "cedarkit.error/v1"}, "code": enum(*error_codes),
     "path": {"type": "string", "pattern": r"^(?:/(?:[^~/]|~[01])*)*$"},
     "message": string(), "context": {"type": "object"}},
    ("api_version", "code", "path", "message", "context"),
)
missing_item = object_schema({"field_identity": field_identity, "code": enum("not_found", "availability_unknown")},
                             ("field_identity", "code"))
response = schema(
    "response",
    {"api_version": {"const": "cedarkit.response/v1"},
     "action": enum("validate", "plan", "list", "locate", "read"),
     "ok": {"type": "boolean"}, "status": enum("valid", "planned", "available", "partial", "unknown"),
     "query_id": string(r"^sha256:[0-9a-f]{64}$"),
     "binding_id": string(r"^sha256:[0-9a-f]{64}$"),
     "references": {"type": "array", "items": ref("data-reference")},
     "missing": {"type": "array", "items": missing_item}, "artifact": output,
     "catalog_version": string(), "vocabulary_version": string(), "error": ref("error")},
    ("api_version", "action", "ok", "status"),
    rules=[{"if": {"properties": {"ok": {"const": False}}},
            "then": {"required": ["error"]}, "else": {"not": {"required": ["error"]}}}],
)

schemas = {
    "request": request,
    "request-list": request_list,
    "query": query,
    "binding": binding,
    "transform": transform,
    "data-reference": data_reference,
    "response": response,
    "error": error,
}


if __name__ == "__main__":
    ROOT.mkdir(parents=True, exist_ok=True)
    for name, contents in schemas.items():
        (ROOT / f"{name}-v1.schema.json").write_text(
            json.dumps(contents, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
