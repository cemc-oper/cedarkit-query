# Query protocol v1

`Query(mapping)` and `Query(**conditions)` create immutable declarations. Use `to_dict()` for a copy and `normalize_query` for normalized selectors. Omitted fields leave the corresponding records unrestricted within the effective source and product scope.

| Selector | Accepted form |
|---|---|
| `parameter` | A stable business parameter ID, registered name or alias, or a nonempty list of these strings |
| `native.grib` | ecCodes scalar key predicates, each with a value, a list or a numeric range |
| `time`, `valid_time` | ISO timestamp with an explicit offset, list or range |
| `step` | Nonnegative ISO duration, list or range |
| `level_type`, `level`, `level_unit` | Optional projected vertical coordinate and its input unit |
| `layer` | Complete bottom and top pair or a list of pairs |
| `member`, `member_role` | Optional member number or role in a registered source |

Ranges are inclusive. An optional positive `every` restricts values to the stated stride. The executor can project coordinates when the native information supports them. Raw keys remain available for selection independently of those projections.

Python, JSON and YAML use the same Query schema. `parameter` and `native.grib`
can appear together and both constrain the same field. A parameter name is
resolved against explicit definitions by the source integration. The
declaration itself can be loaded without those definitions.

A Request requires `api_version: cedarkit.request/v1`, `source` and a nonempty `queries` list. A file source takes a literal path or an ordered path list, or a directory with a glob. A local source takes a dataset and a root. An optional Context supplies a catalog, scope, execution policy or member roles. File paths resolve relative to the Request or Context document. Loading a declaration performs no source discovery.

QueryReport records retrieval and cardinality. A complete expected identity domain requires an independent declaration. Observed fields alone do not establish such a domain. Errors carry a code, a path and context.
