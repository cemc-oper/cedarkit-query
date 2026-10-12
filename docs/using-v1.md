# Use the grid query protocol

`cedarkit-query` 0.1.0 supplies versioned, JSON-compatible values and packaged JSON Schemas. It directly depends on `jsonschema` and `PyYAML`; it does not import reki, ecCodes, xarray, a catalog, or a data provider. Protocol version `v1` is independent of package version `0.1.0` and reki's CalVer version.

Run this from the `cedarkit-query` project after `uv sync`:

```python
from cedarkit_query import (
    QueryError, dump_value, expand_request, get_schema, load_request,
    normalize_request, parse_text_request,
)

query = {
    "api_version": "cedarkit.query/v1", "domain": "grid", "dataset": "grid",
    "parameter": "cedarkit.t", "reference_time": "2026-08-30T00:00:00Z",
    "lead_time": "PT24H", "vertical": {"type": "isobaric", "unit": "hPa", "values": 850},
}
request = load_request({"api_version": "cedarkit.request/v1", "action": "locate", "query": query})
normalized = normalize_request(request)
assert normalized.normalized_request.query.vertical["values"] == 85000
assert len(expand_request(request).expanded_requests) == 1
assert get_schema("request")["$id"] == "urn:cedarkit:query:request:v1"
assert load_request(dump_value(request, format="yaml")) == request

text_request = parse_text_request(
    "LOCATE dataset=grid parameter=cedarkit.t reference_time=2026-08-30T00:00:00Z "
    "lead_time=PT24H vertical.type=isobaric vertical.unit=hPa vertical.values=850"
)
assert normalize_request(text_request).normalized_request.query.to_dict() == normalized.normalized_request.query.to_dict()

try:
    load_request({"api_version": "cedarkit.request/v2", "action": "locate", "query": query})
except QueryError as error:
    assert error.code == "invalid_version"
    print(error.to_dict())  # stable code, JSON Pointer path, message and context
```

`get_schema(kind)` loads one of the eight packaged Draft 2020-12 schemas. `load_request` accepts JSON, safe YAML, or Python mappings without changing the input. `parse_text_request` accepts the restricted syntax documented in [text-v1.md](text-v1.md). Use `RequestList` to keep independently paired queries; selector lists and ranges within one query form a bounded Cartesian product. See [protocol-v1.md](protocol-v1.md) for every field, response and error code, and [normalization-v1.md](normalization-v1.md) for units, expansion and identity. Positive and negative language-neutral vectors are in `tests/vectors/v1/`.

The package validates meaning and shape but makes no availability claim. reki binds a normalized query to a caller-selected catalog and executes supported resources; see its [public query guide](../../reki/doc/source/guide/public-query.md) and [CLI guide](../../reki/doc/source/guide/query-cli.md). Public local `list/locate/read` currently handle GRIB fields; NetCDF, GrADS and table readers retain their existing APIs. A syntactically valid condition can still receive `unsupported` when the selected provider cannot verify it.
