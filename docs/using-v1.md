# Use the current declarations and validators

All files in examples are synthetic contract fixtures. The GRIB path is
intentionally absent; product version oper-1, the data code and
example.single-field interface are illustrative, not verified CEMC bindings.
The tests consume these exact examples rather than a second hand-maintained
copy.

```python
from pathlib import Path
from cedarkit_query import Request, expand_queries

request = Request.load(Path("examples/request.json"))
assert request.completeness == "required"
assert len(expand_queries(request.queries)) == 2
assert Path(request.source["path"]).is_absolute()
```

This opens only the request file and packaged schemas. It does not open the
referenced context/resources, discover files, decode grids, or fetch service
values. The executor later loads actual resources and binds source scope.

```python
from cedarkit_query import load_value, prepare_queries, validate_resource_set

parameters = load_value(Path("examples/parameters.json").read_text(), "parameters")
mappings = load_value(Path("examples/mappings.json").read_text(), "mappings")
catalog = load_value(Path("examples/catalog.json").read_text(), "catalog")
resources = validate_resource_set(
    parameters, [mappings], catalog,
    products=[("example", "orig", "oper-1")],
    interface_contracts={("example.single-field", 1)},
)
prepared = prepare_queries(request.queries, resources)
assert prepared[0].parameter == "cedarkit.t2m"
assert prepared[0].level == 2
```

The supplied contract set is an explicit synthetic test registry, not a lookup
or service call. I06 will supply an actual finite registry. `prepare_queries`
uses registered identities and declared constraints; it does not inspect the
file to guess a more specific identity or product version.

```python
from cedarkit_query import combine_queries

branches = combine_queries([
    [dict(parameter="t", level_type="pressure", level=850, step="PT6H"),
     dict(parameter="t", level_type="pressure", level=500, step="PT12H")],
    [dict(level_type="pressure", level=500)],
])
assert len(branches) == 1
assert branches[0]["query"]["step"] == "PT43200S"
assert branches[0]["origins"] == [
    {"step_index": 0, "query_index": 1},
    {"step_index": 1, "query_index": 0},
]
```

I05 should apply resource/fixed/scope validation to each newly submitted input,
then combine its effective steps, preserving origins. The pure helper does not
replace source lifecycle, runtime capabilities, numerical loading, report
production, or first/one/all semantics. Invalid conditions cannot be hidden
behind an earlier empty selection.

## Ownership at the next boundaries

- I03 reuses Parameters/Mappings, effective_parameters, normalize_fixed and
  validate_resource_set from a migration **copy**. It owns SQLite constraints,
  retired-ID occupancy, actual exports, content revisions and byte determinism.
- I04 owns resource discovery/loading, actual byte SHA/version checks,
  effective resource replacement, the populated catalog and exact context.
  It supplies explicit product triples and, when available, an exact interface
  registry to the shared validators; no global latest-version lookup exists.
- I05/I06 produce QueryReport/QueryExplanation and throw public errors with
  safe context. Normal first stopping, metadata failures, definition gaps,
  actual zero matches and strict completeness stay distinct.
- I08 migrates old imports and execution structures throughout reki/graph/
  workflow/apps, including re-exporting new types at reki's public boundary.
  This package intentionally provides no old-model aliases to keep those
  consumers temporarily runnable.
- I09/I10 handle real-product, online and cross-package installation evidence.
  I02 package tests/isolated installation do not close their acceptance gaps.

See protocol-v1.md, resources-v1.md and rules-v1.md for shape, error and
semantic constraints. Negative/positive vectors under tests/vectors/v1 are
shared across Python, JSON and YAML loaders; those tests use independent
expected codes, paths and canonical values.
