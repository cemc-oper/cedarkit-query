# Grid query normalization and identity v1

Status: implemented by `cedarkit_query.normalize` in T05. This is a pure value operation: it does not load reki, a catalog, a parameter database, files, or a service. Input shape and eight root Schema IDs are defined in [protocol-v1.md](protocol-v1.md).

## Entry points and preserved values

```python
from cedarkit_query import normalize_request, expand_request, query_identity

raw = {
    "api_version": "cedarkit.request/v1", "action": "locate",
    "query": {"api_version": "cedarkit.query/v1", "domain": "grid",
              "dataset": "example.grid", "parameter": "cedarkit.t",
              "reference_time": "2026-08-30T08:00:00+08:00", "lead_time": "P1D"},
}
prepared = normalize_request(raw)
assert prepared.normalized_request.query.reference_time == "2026-08-30T00:00:00Z"
assert prepared.normalized_request.query.lead_time == "PT86400S"
expanded = expand_request(prepared)
assert len(expanded.expanded_requests) == 1
assert query_identity(prepared).startswith("sha256:")
```

`normalize_request` accepts a Request, RequestList, Python mapping, JSON text, or safe YAML text. It returns immutable `NormalizedRequest(raw_request, normalized_request, catalog_version, vocabulary_version)`. `raw_request` is a validated copy of the original expression. `normalized_request` retains action, explicit transforms, options and policy while replacing equivalent selectors with canonical values; omitted policy becomes `{cardinality:"one",missing:"error",duplicates:"error"}`. `expand_request` accepts this result or raw input and returns `ExpandedRequest` with those four fields plus ordered `expanded_requests`. A RequestList preserves item order and pairing. Each expanded item is a Request selecting one parameter, reference time, lead time, member, and vertical value/bound. Output is not decoded data and does not assert availability.

## Explicit alias snapshots

An alias is `{namespace,name}` in Query `dataset` or `parameter`. Resolution requires the corresponding caller-supplied `AliasSnapshot(version, aliases)` or `{version,aliases}` mapping. `aliases` is a nested mapping from namespace to name to stable ID; a list of IDs is allowed only to report ambiguity. For example:

```python
catalog = {"version": "catalog-1", "aliases": {"catalog": {"gfs": "example.grid"}}}
vocabulary = {"version": "vocab-1", "aliases": {"cmadaas": {"TEM": "cedarkit.t"}}}
```

Stable ID strings pass through without any snapshot. Missing aliases raise `unknown_dataset` or `unknown_parameter`; multiple distinct targets raise `ambiguous_mapping`. No current global registry is consulted. Versions are preserved beside the result, outside `query_id`; a changed snapshot that resolves to the same stable IDs does not change logical identity. T07/T08 are responsible for providing real published snapshots and for distinguishing known parameters without an external mapping (`unmapped_parameter`) from unknown aliases.

## Canonical values and expansion

- Time strings require timezone and whole-second precision. They become UTC `YYYY-MM-DDTHH:MM:SSZ`. Nonzero fractions or naive times fail. Durations may contain integral days, hours, minutes and seconds and become `PT<n>S`; lead time may be zero, range step and statistic window must be positive.
- `valid_time` is checked only when one reference time and one lead time are selected. It must equal their sum, then is omitted from the normalized Query because it is redundant. Multiple paired time requests belong in RequestList.
- Pressure levels use Pa (`hPa × 100`); heightAboveGround and soilLayer use m (`km × 1000`); modelLevel uses `1`. Conversion uses decimal arithmetic before JSON number serialization. Unknown/incompatible units fail; no meteorological field values or precipitation units are converted.
- A selector list is a set: normalize, sort, and remove duplicates. A range `{start,stop,step,closed}` is enumerated with `both/left/right/neither` endpoint semantics, then treated as the same set as an explicit list. Empty/reversed ranges and zero steps fail. Bounds require lower < upper. Members retain their explicit `id` and `role` and are sorted as a set. Coverage and crop boxes require west < east and south < north; crossing the date line is outside v1.
- Independent Query dimensions form a Cartesian product in the order parameter, reference time, lead time, member, vertical. RequestList entries stay paired. Each Query and the flattened list are capped at 4096 expanded fields; `options.max_expansion` may lower the per-item bound, never raise it. `zero_or_one` needs exactly one expanded combination. The limit is checked before constructing the product and before any provider access.

## Independent identities

Each ID is `sha256:<64 lowercase hex>` over sorted-key, compact, UTF-8 JSON of its named value. JSON protocol numbers are normalized to canonical decimal representations before hashing. The [fixed normalization vectors](../tests/vectors/v1/normalization.json) pin canonical Query values, expansion counts and hashes across languages.

| Function | Hashed value | Excluded from this identity |
|---|---|---|
| `query_identity(Query or Request or NormalizedRequest)` | Normalized Query, including protocol version, stable dataset and selectors; redundant `valid_time` removed | Action, policy, Transform, Binding, catalog/vocabulary version and resource |
| `transform_identity(Transform)` | Ordered operations, with validated numeric crop box | Query and data source |
| `binding_identity(Binding)` | Full credential-free Binding, including catalog version and local locator or CMADaaS data code | Query and concrete resource fingerprint |
| `resource_identity(DataReference)` | Binding ID plus resource locator and local fingerprint or remote version | Field/query conditions and transforms |

For remote references without a service version, `resource_identity` raises `availability_unknown`; `remote_unverified` itself is still a valid DataReference status. A query hash alone must never be used as a data cache key. A future execution cache key must also include transform ID, catalog and vocabulary versions, binding ID, and verified resource ID. Changed local file fingerprints or remote versions change resource identity. The implementation cannot prove a service's content is unchanged when it supplies no version.

## Old reki conversion

`reki.query_adapter.query_from_field_query(dataset, field_query, parameter_id, reference_time, lead_time, *, native_parameter=None, member_roles=None)` converts the subset of existing `FieldQuery` conditions with explicit meanings. `native_parameter` attests a verified old short name → stable parameter ID mapping. Numeric legacy member IDs need `member_roles`; ID `0` alone does not imply control. Known local level types and fixed-surface keys map to public vertical fields; `step_type` and `time_range` remain distinct from lead time. Unknown `extra` keys, unsupported level types, incomplete statistics, conflicting native step, or unverified member/parameter mappings raise `unsupported` or `invalid_query` instead of being dropped. Catalog Binding plans and GRIB `DataReference` location are implemented in reki; plots workflow conversion is implemented in `cedarkit-plots`, and cedar-graph consumes it through `RekiProvider.from_query_catalog()`. These adapters need explicit catalog, time and verified native parameter information; the pure value package never discovers them.
