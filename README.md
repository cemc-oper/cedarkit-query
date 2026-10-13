# cedarkit-query

`cedarkit-query` is the small, dependency-light Python implementation of the
CedarKit **grid query protocol v1**. It turns JSON, YAML, Python values, or a
restricted text form into validated, immutable request values. It also
normalizes time and units, expands bounded selections, and computes stable
query identities. The eight packaged JSON Schemas make the value format usable
outside Python.

This package describes **what data is requested**. It does not open files,
load a catalog, contact CMADaaS, or return an xarray object. `reki` supplies
source binding, GRIB location and reading. The package release is `0.1.0`;
the exchange format is `cedarkit.query/v1` and is versioned independently.
Python 3.11 or newer is required.

## Start here

From this checkout, install the project dependencies with `uv sync`, then run
the following example with `uv run python`. The example needs no data files or
service credentials. `grid` is an illustrative dataset ID; an executor needs
a catalog binding for it before it can find data.

```python
from cedarkit_query import (
    dump_value,
    expand_request,
    get_schema,
    load_request,
    normalize_request,
    query_identity,
)

request = load_request({
    "api_version": "cedarkit.request/v1",
    "action": "locate",
    "query": {
        "api_version": "cedarkit.query/v1",
        "domain": "grid",
        "dataset": "grid",
        "parameter": "cedarkit.t",
        "reference_time": "2026-08-30T00:00:00Z",
        "lead_time": "PT24H",
        "vertical": {"type": "isobaric", "unit": "hPa", "values": 850},
    },
})

prepared = normalize_request(request)
query = prepared.normalized_request.query
assert query.vertical["unit"] == "Pa"
assert query.vertical["values"] == 85000
assert len(expand_request(prepared).expanded_requests) == 1
print(query_identity(prepared))  # sha256:<canonical logical query hash>

yaml_text = dump_value(request, format="yaml")
assert load_request(yaml_text) == request
assert get_schema("request")["$id"] == "urn:cedarkit:query:request:v1"
```

`load_request` accepts a Python mapping, JSON text, or safe YAML text and
rejects unknown fields and invalid versions. `normalize_request` preserves
both the input and canonical request; it does not mutate the input. For
example, 850 hPa becomes 85000 Pa and a time with an offset becomes UTC.
`expand_request` turns lists and ranges into individual requests and rejects
more than 4096 combinations before any data access. `query_identity` hashes
the normalized logical query, excluding the action, transforms, binding,
resource, and catalog/vocabulary versions. Transforms, bindings, and verified
resources have their own identity functions; snapshot versions are retained
alongside the normalized request.

## Choose an entry point

| Input or task | Entry point | Result |
|---|---|---|
| Python mapping, JSON, or YAML request | `load_request(value)` | Immutable `Request` or `RequestList` |
| Restricted one-line request | `parse_text_request(text)` | `Request` |
| Several independently paired text requests | `parse_text_requests(text)` | `RequestList` |
| Canonical time, units, selectors, and explicit aliases | `normalize_request(value, ...)` | Raw and normalized requests plus snapshot versions |
| Lists and ranges | `expand_request(value, ...)` | Bounded, ordered individual requests |
| Protocol schema or serialized value | `get_schema(kind)`, `dump_value(value)` | JSON Schema copy or JSON/YAML text |

For example, the same request can begin as a compact text line:

```python
from cedarkit_query import parse_text_request

request = parse_text_request(
    "LOCATE dataset=grid parameter=cedarkit.t "
    "reference_time=2026-08-30T00:00:00Z lead_time=PT24H "
    "vertical.type=isobaric vertical.unit=hPa vertical.values=850"
)
```

The text form covers a deliberate subset of v1. Use structured input for
aliases, coverage, transforms, options, and other fields outside that subset.
Aliases resolve only against caller-supplied, versioned catalog or vocabulary
snapshots; this package never reads the process's current catalog implicitly.
Validation errors are `QueryError` instances with a stable `code`, JSON
Pointer `path`, `message`, and `to_dict()` representation.

## Relationship to reki

For a known GRIB file, `reki.from_source("file", path).sel(...).one()` remains
a direct way to read one field. A public Query uses a stable `dataset` and
parameter ID, an explicit reference time and lead time, and a source binding
chosen by reki. This makes the request portable across the CLI, Python, and
workflow consumers, but it adds execution context that this protocol package
does not own.

In Python, reki's [public query guide](../reki/doc/source/guide/public-query.md)
shows the complete sequence: select a catalog snapshot, bind the normalized
query, locate GRIB fields without decoding values, and read the matched
collection. Its [request CLI guide](../reki/doc/source/guide/query-cli.md)
covers `validate`, `plan`, `list`, `locate`, and `read`. Local public
`list/locate/read` currently handle GRIB; reki's existing NetCDF, GrADS, and
table readers keep their own APIs. A valid protocol request may still be
`unsupported` by a chosen source. The observation query remains a separate
draft and is not part of grid v1.

## Documentation and development

- [Runnable v1 guide](docs/using-v1.md): more examples, YAML round-trip, text
  input, and error handling.
- [Protocol v1](docs/protocol-v1.md): fields, eight Schema IDs, actions,
  responses, and errors.
- [Normalization v1](docs/normalization-v1.md): units, snapshots, expansion,
  and identities.
- [Text v1](docs/text-v1.md): the supported text grammar.
- [Language-neutral test vectors](tests/vectors/v1/): accepted and rejected
  inputs shared across implementations.

```bash
uv sync --group test
uv run pytest
uv build
```
