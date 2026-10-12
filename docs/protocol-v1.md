# CedarKit grid query protocol v1

Status: versioned value model, JSON Schema, pure normalization, expansion and identity are implemented in `cedarkit-query` 0.1.0. See [normalization-v1.md](normalization-v1.md) for T05 semantics. Dataset/source execution and text requests are later tasks. Loading a valid value does not confirm a dataset or resource exists.

## Versions and resources

All objects are JSON compatible. The eight independently loadable roots have these `api_version` strings and packaged JSON Schema Draft 2020-12 resources:

| Object | `api_version` | Schema `$id` | Resource |
|---|---|---|---|
| Request | `cedarkit.request/v1` | `urn:cedarkit:query:request:v1` | `schemas/request-v1.schema.json` |
| RequestList | `cedarkit.request-list/v1` | `urn:cedarkit:query:request-list:v1` | `schemas/request-list-v1.schema.json` |
| Query | `cedarkit.query/v1` | `urn:cedarkit:query:query:v1` | `schemas/query-v1.schema.json` |
| Binding | `cedarkit.binding/v1` | `urn:cedarkit:query:binding:v1` | `schemas/binding-v1.schema.json` |
| Transform | `cedarkit.transform/v1` | `urn:cedarkit:query:transform:v1` | `schemas/transform-v1.schema.json` |
| DataReference | `cedarkit.data-reference/v1` | `urn:cedarkit:query:data-reference:v1` | `schemas/data-reference-v1.schema.json` |
| Response | `cedarkit.response/v1` | `urn:cedarkit:query:response:v1` | `schemas/response-v1.schema.json` |
| Error | `cedarkit.error/v1` | `urn:cedarkit:query:error:v1` | `schemas/error-v1.schema.json` |

Unknown fields, wrong versions, explicit `null`, empty strings (except the root JSON Pointer in Error `path`), empty selector arrays, non-finite numbers, duplicate JSON/YAML keys, and non-JSON Python values are rejected. YAML uses SafeLoader and rejects arbitrary object tags. An omitted field means no restriction; an unknown output value is omitted and accompanied by status. The protocol carries no runtime handles, pandas/xarray objects, credentials, or reader instances. `domain` is exactly `grid`; the observation draft is a separate version family.

## Values

`Query` requires `api_version`, `domain:"grid"`, and `dataset`. Dataset is a stable catalog ID or `{namespace,name}` alias. Optional `parameter` is a stable ID, an alias, or a nonempty list of them. Optional `reference_time` and `lead_time` are exact values, nonempty lists, or `{start,stop,step,closed}` ranges; `closed` is `both`, `left`, `right`, or `neither`. Times require a timezone; durations allow days, hours, minutes, and seconds. Optional `valid_time` is a redundant check of one reference time plus one lead time. `vertical` has `type` and, except for `surface`, `unit` with exactly one of `values` or `bounds`. `statistic` is `instant` without a window or `accum/mean/min/max` with a positive window. `member` has `id` and `role` (`control` or `perturbation`). `native_grid` has `id`. `coverage` uses `EPSG:4326`, `[west,south,east,north]`, and `relation:"intersects"`.

`Request` requires `action` and `query`. Actions are `validate`, `plan`, `list`, `locate`, and `read`; `plan/locate/read` require parameter, reference time, and lead time. Optional `policy` contains `cardinality` (`one`, `zero_or_one`, `all`), `missing` (`error`, `partial`), and `duplicates:"error"`. `zero_or_one` requires `missing:"partial"`. Optional `transforms` is a Transform object; `options.max_expansion` is 1–4096, and `options.output` is `{format:"netcdf",path}` for `read` only. An omitted policy means cardinality one, missing error, duplicates error. `RequestList` holds a nonempty ordered array of independent Request objects; items never inherit fields from one another.

`Binding` connects one dataset to either `kind:"local"` with a `file-pattern` or `reki-local` locator, or `kind:"cmadaas"` with `data_code` and `request_kind:"model_grid"`. It requires `source_id` and `catalog_version`; `origin` is optional. Binding contains deployment location, not credentials. `Transform.operations` is an ordered array, possibly empty, of `{kind:"crop",bbox}` or `{kind:"unit",target}`. `crop` uses the query's EPSG:4326 bbox convention. Unimplemented operations must later fail as `unsupported`; they are never silently skipped.

`DataReference` identifies one expanded field with `dataset`, `field_identity`, `binding_id`, `resource`, and `verification`. `field_identity` requires one parameter, reference time, and lead time, and may contain vertical, statistic, member, or native grid. A local resource has a locator and fingerprint `{size,mtime_ns,device,inode}` with optional SHA256. A CMADaaS resource has a locator and optional version. `field_locator` accepts a nonempty subset of nonnegative `frame_index`, `field_index`, and `offset`, and is present only for `verification:"field_confirmed"`. Local resources can be `path_exists` or `field_confirmed`; remote resources are `remote_unverified`. An offset or index is meaningful only under its resource fingerprint. Resource changes require relocation or `stale_reference` during execution.

`Response` has `action`, boolean `ok`, and status `valid/planned/available/partial/unknown`. It may contain query/binding IDs, references, missing items, artifact, and catalog/vocabulary versions. A failed response contains an Error; a successful one cannot. `Error` contains `code`, JSON Pointer `path`, nonempty `message`, and object `context`. Error codes are `invalid_query`, `invalid_version`, `unknown_dataset`, `unknown_parameter`, `unmapped_parameter`, `ambiguous_mapping`, `unsupported`, `expansion_limit`, `not_found`, `multiple_matches`, `availability_unknown`, `permission_denied`, `provider_error`, and `stale_reference`.

The packaged schemas enforce JSON value shape, version, enum, required/forbidden fields, format, and many conditional requirements. T05 normalization checks time arithmetic, alias resolution against supplied snapshots, unit conversion, longitude ordering, range expansion, and query ID. Neither layer accesses catalogs or files. A syntactically valid `read` request is not proof that a provider can execute it.

## Python entry points

```python
from cedarkit_query import QueryError, dump_value, load_request

request = load_request({
    "api_version": "cedarkit.request/v1",
    "action": "validate",
    "query": {"api_version": "cedarkit.query/v1", "domain": "grid", "dataset": "example.grid"},
})
assert request.query.dataset == "example.grid"
json_text = dump_value(request)
yaml_text = dump_value(request, format="yaml")
```

`load_request` accepts a Python mapping, JSON text, or safe YAML text and returns an immutable Request or RequestList. `load_value(value, kind)` loads any root object. `validate_value(value, kind)` returns a fresh plain Python copy, `get_schema(kind)` returns a fresh schema copy, and `dump_value(model, format="json"|"yaml")` serializes a validated model. Errors use `QueryError.code`, `.path`, `.message`, and `.to_dict()`. The language-neutral fixtures in `tests/vectors/v1/` provide positive and negative examples. The JSON Schema files travel in both wheel and source distribution.

The exchange version is independent of the package version, catalog snapshot, vocabulary snapshot, binding ID, and query ID. T05 canonical values, expansion and identity are implemented and documented separately. T06 adds the limited text syntax; it is not implemented by this version of the package.
