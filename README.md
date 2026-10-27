# cedarkit-query

The dependency-light implementation of CedarKit's **single Query/Request
contract and shared resource validation**. Python 3.11+, `jsonschema`, and
`PyYAML` are the only runtime requirements. It has no dependency on reki,
ecCodes, xarray, SQLite, or a CMADaaS client.

Stage 1.1 replaces the earlier, unreleased v1 definitions in this package.
Request/error retain their v1 names; this does **not** make the old and new
structures compatible. Old `action/query/policy`, `RequestList`, bindings,
transforms, responses, and the restricted text parser have been removed.
Package version remains `0.1.0` in this source checkout; this work neither
publishes nor overwrites a distributed artifact. Matching downstream packages
still need their I04–I08 migration and I10 release preparation.

## Declare a request

```python
from cedarkit_query import Query, Request, expand_queries

request = Request.from_dict({
    "api_version": "cedarkit.request/v1",
    "source": {"kind": "file", "path": "./forecast.grib2"},
    "queries": [{
        "parameter": "t",
        "level_type": "pressure",
        "level": [850, 500],
        "time": "2026-10-27T00:00:00Z",
        "step": ["PT6H", "PT12H"],
    }],
})
assert request.completeness is None
assert len(expand_queries(request.queries)) == 4
assert Query().to_dict() == {}  # no additional restriction
```

`Request.load(path)` reads one YAML/JSON request; `Request.from_dict` accepts
Python mappings. Both use the same structural and semantic validators and
return deeply immutable values. `to_dict()` returns an independent JSON-safe
copy. `dump(path)` selects YAML/JSON from the extension. Paths are fixed to
absolute paths at construction; `glob` remains a pattern. Referenced context,
resource, connection, and business-data files are **not** opened by these
constructors. An absent data file can therefore be a valid declaration.

A request contains one `source`, a nonempty `queries` list, optional `context`,
and optional `completeness`. Its version governs its embedded Queries; Query
has no `api_version`, dataset, execution policy, or output action. Execution
and client injection belong to reki's later `from_source → sel → first/one/all
→ to_xarray` implementation, which is outside this package.

## Shared contracts

| Value | Exchange version | Python entry |
|---|---|---|
| Request | `cedarkit.request/v1` | `Request`, `load_request` |
| Query | governed by Request v1 | `Query`, `normalize_query` |
| Error | `cedarkit.error/v1` | `QueryError`, `CompletenessError`, `SourceError`, `ErrorValue` |
| Report | `schema_version: 1` | `QueryReport` |
| Explanation | diagnostic snapshot | `QueryExplanation` |
| Parameters | `cedarkit.parameters/v1` | `Parameters` |
| Backend mappings | `cedarkit.mappings/v1` | `Mappings` |
| Independent catalog | `cedarkit.catalog/v1` | `Catalog` |

`get_schema(kind)` returns a fresh copy of one of ten packaged Draft 2020-12
schemas, including `common` and `context`. Standard JSON Schema covers shapes,
strict fields, and representable conditional constraints. Comparisons between
values, inheritance, cycles, scoped references, and finite translation-table
conflicts require the **shared semantic validators**. `validate_value` and all
Python/JSON/YAML model loaders run both layers. A standalone generic JSON
Schema validator alone is not a complete CedarKit semantic validator.

```python
from cedarkit_query import Parameters, validate_resource_set

parameters = Parameters({
    "api_version": "cedarkit.parameters/v1", "id": "example", "version": "1",
    "parameters": [{"parameter_id": "example.t", "name": "t", "unit": "K"}],
})
resources = validate_resource_set(parameters)
assert resources.parameter("t")["parameter_id"] == "example.t"
```

All inputs reject unknown fields, unsafe/duplicate YAML or JSON keys,
nonfinite values, implicit numeric strings, and runtime objects. Empty Query
is legal; null Query is not. Evidence references are identifiers/paths, not
network dependencies or permanent verification flags. Valid declarations do
not prove actual products, native encodings, service availability, numerical
correctness, or complete coverage.

## Documentation and verification

- [Protocol and errors](docs/protocol-v1.md)
- [Normalization and pure selection algebra](docs/normalization-v1.md)
- [Resource structure and three validation layers](docs/resources-v1.md)
- [Nine finite rule contracts and target template families](docs/rules-v1.md)
- [Runnable examples and downstream handoff](docs/using-v1.md)
- [Language-neutral accepted/rejected/normalization vectors](tests/vectors/v1)
- [Synthetic resource examples](examples): these are not real product bindings.

```bash
uv sync --group test
uv run pytest -q
uv run python scripts/build_schemas.py --check
uv build
uv run python scripts/check_dist.py --dist dist --python 3.11
```

The ten shipped JSON files are the authoritative schemas. `build_schemas.py`
checks their syntax, canonical formatting, and closed local references; without
`--check` it canonicalizes those same files. It no longer generates the retired
protocol. `check_dist.py` expects exactly one candidate wheel/sdist pair and
checks a fresh isolated installation. Add `--offline` to use only cached
installation dependencies.

Building/testing this package checks its own contract and packaging. It does
not perform the cross-package installation, real CEMC data, or online-service
acceptance owned by subsequent tasks.
